"""Opt-in, whole-worktree junction preservation; never an eviction adapter.

The caller proves Git/ownership/policy and holds the cleanup lock. Only exact
ignored/untracked cache paths supplied by that caller are admitted. Shared target
contents are never enumerated. Junction objects are handle-renamed into the private
retirement archive before the ordinary worktree is recycled, and remain there for
recovery. No junction is unlinked, recreated or permanently deleted here.
"""
from contextlib import ExitStack, contextmanager
import contextlib
import datetime
import hashlib
import json
import ntpath
import os
from pathlib import Path
import stat
import struct
import time


class JunctionRefused(Exception):
    """Unknown, changed or unsupported input; retain source and registration."""


def path_key(path):
    return ntpath.normcase(ntpath.normpath(os.fspath(path)))


def _key(info):
    return [info.volume, info.index_high, info.index_low, bool(info.attributes & 0x10)]


def _local(native, path):
    try:
        return native._native_path(path)
    except (OSError, ValueError, native.PlatformRefused) as error:
        raise JunctionRefused('ordinary local absolute path required') from error


def _relative(value):
    name = os.fspath(value).replace('/', '\\').rstrip('\\')
    if (not name or ntpath.isabs(name) or ntpath.splitdrive(name)[0]
            or any(p in ('', '.', '..') or ':' in p or p.endswith((' ', '.')) for p in name.split('\\'))
            or ntpath.basename(name).casefold() not in ('node_modules', 'target')):
        raise JunctionRefused('only exact relative dependency/build cache paths are supported')
    return name


def _parse_reparse(raw, native, root):
    """Decode a bounded Microsoft directory junction, excluding volume mounts."""
    if not isinstance(raw, bytes) or not 16 <= len(raw) <= 16384:
        raise JunctionRefused('invalid reparse buffer size')
    tag, length, reserved = struct.unpack_from('<IHH', raw)
    if tag != 0xA0000003 or reserved or length + 8 != len(raw) or length < 8:
        raise JunctionRefused('unsupported reparse tag or malformed mount-point data')
    sub_offset, sub_length, print_offset, print_length = struct.unpack_from('<HHHH', raw, 8)
    names = raw[16:]
    for offset, count in ((sub_offset, sub_length), (print_offset, print_length)):
        if offset % 2 or count % 2 or offset + count > len(names):
            raise JunctionRefused('invalid junction name bounds')
    try:
        substitute = names[sub_offset:sub_offset + sub_length].decode('utf-16-le', errors='strict')
        printable = names[print_offset:print_offset + print_length].decode('utf-16-le', errors='strict')
    except UnicodeError as error:
        raise JunctionRefused('invalid junction name encoding') from error
    if not substitute.startswith('\\??\\') or '\x00' in substitute or '\x00' in printable:
        raise JunctionRefused('non-local or malformed junction target')
    target = _local(native, substitute[4:])
    rk, tk = path_key(root), path_key(target)
    if tk == rk or tk.startswith(rk + '\\') or rk.startswith(tk.rstrip('\\') + '\\'):
        raise JunctionRefused('internal, cyclic or ancestor junction target is held')
    return target


def _api(native):
    if not native.is_windows():
        raise JunctionRefused('native Windows junction preservation is unavailable')
    return native._win32()


def _fixed_drive(api, path):
    # Check the drive mapping before opening a target/ancestor path. A drive
    # letter can be a mapped network share even though it is syntactically local.
    drive = ntpath.splitdrive(path)[0]
    if api[2].GetDriveTypeW(drive + '\\') != 3:
        raise JunctionRefused('junction preservation requires local fixed drives')


@contextmanager
def _chain(native, api, path, *, share=3):
    """Pin ordinary directory names, allowing readers/writers but not renames.

    LIST_DIRECTORY engages sharing checks; an attributes-only handle does not.
    Target pins do not enumerate target contents. A concurrent delete/rename can
    be refused during the short transaction, so this is not a consumer lease.
    """
    with ExitStack() as stack:
        ids = []
        for directory in reversed((Path(path), *Path(path).parents)):
            name = str(directory)
            handle = stack.enter_context(native._handle(api, name, access=0x81, share=share))
            info = native._identity(api, handle, name, directory=True)
            ids.append({'path': name, 'path_key': path_key(name), 'identity': _key(info)})
        yield ids


def _chain_same(first, second):
    return [(i['path_key'], i['identity']) for i in first] == [(i['path_key'], i['identity']) for i in second]


def _source_protection(chain):
    for directory in chain:
        if any(os.path.lexists(ntpath.join(directory['path'], name)) for name in ('HOLD.json', 'KEEP.txt')):
            raise JunctionRefused('source ancestor retention marker keeps this worktree')


def _manifest_name(entry):
    name = 'package.json' if ntpath.basename(entry).casefold() == 'node_modules' else 'Cargo.toml'
    return ntpath.join(ntpath.dirname(entry), name)


def _manifest_same(expected, path, identity):
    return path_key(expected['path']) == path_key(path) and expected['identity'] == identity


@contextmanager
def _ordinary_file(native, api, path, *, include_handle=False):
    # READ_DATA engages sharing checks. Denying DELETE preserves the directory
    # entry, so its parent cannot become empty and acquire reparse metadata.
    with native._handle(api, path, access=0x81, share=3) as handle:
        info = native._identity(api, handle, path, directory=False)
        if info.links != 1:
            raise JunctionRefused('namespace guard file has multiple hard links')
        yield {'identity': _key(info), 'handle': handle, 'path': path} if include_handle else _key(info)


def _handle_name(api, handle):
    c, _, kernel, _, _ = api
    buffer = c.create_unicode_buffer(32768)
    count = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not count or count >= len(buffer):
        raise JunctionRefused('cannot establish junction handle name')
    name = buffer.value
    return name[4:] if name.startswith('\\\\?\\') else name


def _read_link(native, api, handle, path, root):
    c, w, kernel, FileInfo, _ = api
    info = FileInfo()
    if not kernel.GetFileInformationByHandle(handle, c.byref(info)):
        raise c.WinError(c.get_last_error())
    if not info.attributes & 0x400 or not info.attributes & 0x10:
        raise JunctionRefused('entry is no longer a directory reparse point')
    observed = os.lstat(path)
    if (getattr(observed, 'st_reparse_tag', None) != 0xA0000003
            or observed.st_ino != (info.index_high << 32) | info.index_low):
        raise JunctionRefused('junction identity or tag changed')
    if path_key(_handle_name(api, handle)) != path_key(path):
        raise JunctionRefused('junction handle does not name the reviewed entry')
    kernel.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, c.c_void_p, w.DWORD,
                                      c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]
    kernel.DeviceIoControl.restype = w.BOOL
    output, returned = c.create_string_buffer(16384), w.DWORD()
    if not kernel.DeviceIoControl(handle, 0x000900A8, None, 0, output, len(output), c.byref(returned), None):
        raise c.WinError(c.get_last_error())
    raw = output.raw[:returned.value]
    target = _parse_reparse(raw, native, root)
    return {'identity': _key(info), 'reparse_hex': raw.hex(), 'target': target}


def _same(actual, expected):
    return all(actual.get(k) == expected.get(k) for k in ('identity', 'reparse_hex', 'target'))


def _validate_plan(plan, native):
    if (not isinstance(plan, dict) or plan.get('schema_version') != 1
            or not isinstance(plan.get('entries'), list) or len(plan['entries']) > 128):
        raise JunctionRefused('invalid or overlarge junction plan')
    root = _local(native, plan['root'])
    seen = set()
    for entry in plan['entries']:
        expected = ntpath.join(root, _relative(entry['relative']))
        if (path_key(expected) != entry.get('path_key') or path_key(entry['path']) != path_key(expected)
                or entry['path_key'] in seen):
            raise JunctionRefused('junction plan entry is outside its exact source slot')
        try:
            raw = bytes.fromhex(entry['reparse_hex'])
        except (TypeError, ValueError) as error:
            raise JunctionRefused('invalid planned reparse bytes') from error
        if _parse_reparse(raw, native, root) != entry['target']:
            raise JunctionRefused('planned target differs from reparse data')
        seen.add(entry['path_key'])
    return root


def discover(root, allowed_relative_paths, native, *, max_seconds=30, max_entries=400000, on_entry=None):
    """Read-only plan. Every reparse point not explicitly admitted is held."""
    root = _local(native, root)
    api = _api(native)
    _fixed_drive(api, root)
    allowed = {path_key(ntpath.join(root, _relative(p))) for p in allowed_relative_paths}
    entries, visited, scheduled, started, root_id = [], 0, 1, time.monotonic(), None
    if (type(max_entries) is not int or max_entries < 1
            or type(max_seconds) not in (int, float) or not 0 < max_seconds < float('inf')):
        raise JunctionRefused('invalid junction inspection budget')

    def visit(path):
        nonlocal visited, root_id
        visited += 1
        if visited > max_entries or time.monotonic() - started >= max_seconds:
            raise JunctionRefused('junction inspection incomplete')
        observed = os.lstat(path)
        if Path(path).name.casefold() in ('hold.json', 'keep.txt'):
            raise JunctionRefused('source retention marker keeps this worktree')
        if Path(path).name.casefold() == '.git' and stat.S_ISDIR(observed.st_mode):
            raise JunctionRefused('nested Git metadata is held')
        if native._reparse_info(observed):
            if path_key(path) not in allowed:
                raise JunctionRefused('unapproved reparse entry in worktree')
            if len(entries) >= 128:
                raise JunctionRefused('too many junctions in one retirement')
            with native._handle(api, path, access=0x10080, share=0) as handle:
                link = _read_link(native, api, handle, path, root)
                _fixed_drive(api, link['target'])
                with _chain(native, api, link['target']) as targets:
                    manifest_path = _manifest_name(path)
                    with _ordinary_file(native, api, manifest_path) as manifest_id:
                        manifest = {'path': manifest_path, 'identity': manifest_id}
                    source_chain = ancestor_ids + [
                        {'path': name, 'path_key': path_key(name),
                         'identity': _key(native._identity(api, handle, name, directory=True))}
                        for name, handle in parents]
                    entries.append({'relative': ntpath.relpath(path, root), 'path': path,
                                    'path_key': path_key(path), 'parent_identity': source_chain[-1]['identity'],
                                    'source_chain': source_chain, 'target_chain': targets,
                                    'manifest': manifest, **link})
                    if on_entry is not None:
                        on_entry(path, observed, True)
            return
        if stat.S_ISDIR(observed.st_mode):
            # Namespace pins are not a promise that metadata-only writers cannot
            # resume. Enumerate the retained object and recheck its identity.
            with native._handle(api, path, access=0x81, share=1) as handle:
                info = native._identity(api, handle, path, directory=True)
                if on_entry is not None:
                    on_entry(path, observed, False)
                if path_key(path) == path_key(root):
                    root_id = _key(info)
                parents.append((path, handle))
                try:
                    before = os.lstat(path).st_mtime_ns
                    def admit():
                        nonlocal scheduled
                        scheduled += 1
                        if scheduled > max_entries:
                            raise JunctionRefused('junction inspection incomplete')
                    names = [ntpath.join(path, name) for name in native.directory_names(
                        api, handle, deadline=started + max_seconds, limit=max_entries, admitted=admit)]
                    for child in names:
                        visit(child)
                    after = [ntpath.join(path, name) for name in native.directory_names(
                        api, handle, deadline=started + max_seconds, limit=max_entries)]
                    native._identity(api, handle, path, directory=True)
                    if names != after or before != os.lstat(path).st_mtime_ns:
                        raise JunctionRefused('worktree namespace changed during inspection')
                finally:
                    parents.pop()
        elif not stat.S_ISREG(observed.st_mode):
            raise JunctionRefused('special filesystem entry is held')
        elif on_entry is not None:
            on_entry(path, observed, False)

    parents = []
    try:
        with _chain(native, api, str(Path(root).parent)) as ancestor_ids:
            _source_protection(ancestor_ids)
            visit(root)
        if root_id is None:
            raise JunctionRefused('retirement root must be an ordinary directory')
        return {'schema_version': 1, 'root': root, 'root_identity': root_id,
                'entries': sorted(entries, key=lambda e: e['path_key']), 'visited_entries': visited,
                'inspection': {'max_seconds': max_seconds, 'max_entries': max_entries}}
    except (OSError, ValueError, native.PlatformRefused) as error:
        raise JunctionRefused('junction inspection could not prove safe entry identities') from error


def probe(path, plan, native):
    """Validate one planned entry as a no-follow leaf, never its contents."""
    _validate_plan(plan, native)
    path, api = _local(native, path), _api(native)
    _fixed_drive(api, path)
    expected = next((e for e in plan['entries'] if e['path_key'] == path_key(path)), None)
    if expected is None:
        raise JunctionRefused('reparse entry is not in the reviewed plan')
    _fixed_drive(api, expected['target'])
    try:
        with _chain(native, api, str(Path(path).parent)) as sources:
            _source_protection(sources)
            if not _chain_same(sources, expected['source_chain']):
                raise JunctionRefused('source namespace changed since review')
            root_row = next((i for i in sources if i['path_key'] == path_key(plan['root'])), None)
            if root_row is None or root_row['identity'] != plan['root_identity']:
                raise JunctionRefused('worktree root identity changed')
            with _ordinary_file(native, api, _manifest_name(path)) as manifest_id:
                if not _manifest_same(expected['manifest'], _manifest_name(path), manifest_id):
                    raise JunctionRefused('source manifest identity changed')
            with native._handle(api, path, access=0x10080, share=0) as handle:
                actual = _read_link(native, api, handle, path, plan['root'])
                if not _same(actual, expected):
                    raise JunctionRefused('junction entry changed since review')
                with _chain(native, api, expected['target']) as targets:
                    if not _chain_same(targets, expected['target_chain']):
                        raise JunctionRefused('junction target namespace changed since review')
        return True
    except (OSError, ValueError, native.PlatformRefused) as error:
        raise JunctionRefused('junction entry is busy, unreadable or changed') from error


def _archive_disjoint(plan, archive, native):
    root = _validate_plan(plan, native)
    archive = _local(native, archive)
    ak = path_key(archive).rstrip('\\')
    for protected in [root, *[entry['target'] for entry in plan['entries']]]:
        pk = path_key(protected).rstrip('\\')
        if ak == pk or ak.startswith(pk + '\\') or pk.startswith(ak + '\\'):
            raise JunctionRefused('archive, source and shared targets must be disjoint')
    if ntpath.splitdrive(root)[0].casefold() != ntpath.splitdrive(archive)[0].casefold():
        raise JunctionRefused('junction escrow requires a same-volume archive')
    return archive


@contextmanager
def preflight(plan, archive_root, native):
    """Pin an existing nonempty flat archive before the caller's first write.

    No directory is created here. A pinned ordinary child keeps the destination
    nonempty against metadata-only reparse installation; namespace sharing alone
    cannot provide that guarantee. Keep this context through receipt and escrow.
    """
    archive = _archive_disjoint(plan, archive_root, native)
    api = _api(native)
    _fixed_drive(api, archive)
    try:
        with ExitStack() as stack:
            for entry in plan['entries']:
                _fixed_drive(api, entry['target'])
                targets = stack.enter_context(_chain(native, api, entry['target']))
                if not _chain_same(targets, entry['target_chain']):
                    raise JunctionRefused('shared target namespace changed before archive writes')
            stack.enter_context(_chain(native, api, archive))
            handle = stack.enter_context(native._handle(api, archive, access=0x81, share=3))
            native._identity(api, handle, archive, directory=True)
            anchor = None
            # Existing ordinary directories can also anchor their parent's
            # nonemptiness. Never inspect or traverse a child directory.
            for name in native.directory_names(api, handle, limit=4096):
                path = ntpath.join(archive, name)
                observed = os.lstat(path)
                if native._reparse_info(observed) or not (stat.S_ISREG(observed.st_mode) or stat.S_ISDIR(observed.st_mode)):
                    continue
                child = stack.enter_context(native._handle(api, path, access=0x81, share=3))
                native._identity(api, child, path, directory=stat.S_ISDIR(observed.st_mode))
                if path_key(ntpath.dirname(_handle_name(api, child))) != path_key(archive):
                    raise JunctionRefused('archive anchor namespace changed')
                anchor = child
                break
            if anchor is None:
                raise JunctionRefused('archive must already contain an ordinary namespace anchor')
            native._identity(api, handle, archive, directory=True)
            yield
    except (OSError, ValueError, native.PlatformRefused) as error:
        raise JunctionRefused('archive preflight cannot prove a stable nonempty destination') from error


def _rename(api, handle, name, *, parent_handle, anchor):
    """Rename the opened object under a structurally immutable parent.

    Win32 RootDirectory-relative rename returned ERROR_INVALID_PARAMETER on the
    qualification host. The full path is used only while ordinary ancestors are
    namespace-pinned and an existing ordinary file is pinned against deletion in
    the destination parent. That parent cannot become empty, so NTFS cannot add
    directory-reparse metadata. Share flags alone do not freeze attributes.
    """
    c, w, kernel, _, _ = api

    class Rename(c.Structure):
        _fields_ = [('replace', w.BOOL), ('root', w.HANDLE), ('length', w.DWORD), ('name', w.WCHAR * 1)]

    if ntpath.basename(name) != name or name in ('', '.', '..') or any(c in name for c in (':', '/', '\\', '\x00')):
        raise JunctionRefused('rename destination must be one relative leaf')
    info = api[3]()
    if not kernel.GetFileInformationByHandle(parent_handle, c.byref(info)):
        raise c.WinError(c.get_last_error())
    if info.attributes & 0x400 or not info.attributes & 0x10:
        raise JunctionRefused('rename destination parent is not ordinary')
    parent_name = _handle_name(api, parent_handle)
    if not kernel.GetFileInformationByHandle(anchor['handle'], c.byref(info)):
        raise c.WinError(c.get_last_error())
    if info.attributes & (0x400 | 0x10) or info.links != 1 or _key(info) != anchor['identity']:
        raise JunctionRefused('rename parent anchor changed')
    if path_key(ntpath.dirname(_handle_name(api, anchor['handle']))) != path_key(parent_name):
        raise JunctionRefused('rename anchor is not in the destination parent')
    encoded = ntpath.join(parent_name, name).encode('utf-16-le')
    buffer = c.create_string_buffer(Rename.name.offset + len(encoded) + 2)
    header = c.cast(buffer, c.POINTER(Rename)).contents
    header.replace, header.root, header.length = False, None, len(encoded)
    c.memmove(c.addressof(buffer) + Rename.name.offset, encoded, len(encoded))
    kernel.SetFileInformationByHandle.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.SetFileInformationByHandle.restype = w.BOOL
    if not kernel.SetFileInformationByHandle(handle, 3, buffer, len(buffer)):
        raise c.WinError(c.get_last_error())


class _Escrow:
    def __init__(self, plan, archive, native, *, prefix='', record_name='RETIRED.json'):
        _validate_plan(plan, native)
        self.plan, self.native = plan, native
        self.archive, self.api = _local(native, archive), _api(native)
        if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in prefix):
            raise JunctionRefused('invalid archive transaction prefix')
        if record_name != prefix + 'RETIRED.json':
            raise JunctionRefused('receipt must match archive transaction prefix')
        self.prefix, self.record_name = prefix, record_name
        _fixed_drive(self.api, self.archive)
        self.stack, self.items, self.journal, self.committed = ExitStack(), [], None, False
        self.journal_path = str(Path(self.archive)/(prefix + 'junction-escrow.jsonl'))
        self.state = 'not-started'

    def summary(self):
        return {'state': self.state, 'journal': self.journal_path,
                'entries': [{'source': item['expected']['path'], 'archive': item['destination'],
                             'relative': item['expected']['relative'],
                             'identity': item['expected']['identity'],
                             'state': 'escrowed' if item['moved'] else 'source'} for item in self.items]}

    def _event(self, event, **data):
        data = {'event': event, 'at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), **data}
        self.journal.write(json.dumps(data, sort_keys=True) + '\n')
        self.journal.flush()
        os.fsync(self.journal.fileno())

    def __enter__(self):
        try:
            root = self.plan['root']
            _archive_disjoint(self.plan, self.archive, self.native)
            if path_key(self.archive) == path_key(root) or path_key(self.archive).startswith(path_key(root) + '\\'):
                raise JunctionRefused('archive must be outside the retiring worktree')
            if ntpath.splitdrive(root)[0].casefold() != ntpath.splitdrive(self.archive)[0].casefold():
                raise JunctionRefused('junction escrow requires a same-volume archive')
            for expected in self.plan['entries']:
                archive_key, target_key = path_key(self.archive), path_key(expected['target'])
                if (archive_key == target_key or archive_key.startswith(target_key.rstrip('\\') + '\\')
                        or target_key.startswith(archive_key.rstrip('\\') + '\\')):
                    raise JunctionRefused('archive and shared target must be disjoint')
            limits = self.plan.get('inspection', {'max_seconds': 30, 'max_entries': 400000})
            fresh = discover(root, [entry['relative'] for entry in self.plan['entries']], self.native, **limits)
            if fresh != self.plan:
                raise JunctionRefused('junction or source protection plan changed before staging')
            self.stack.enter_context(_chain(self.native, self.api, self.archive))
            # A pinned existing record keeps this directory nonempty. NTFS will
            # not install a directory reparse point on a nonempty directory.
            # We intentionally use no new empty subdirectory as a destination.
            self.record_guard = self.stack.enter_context(_ordinary_file(
                self.native, self.api, str(Path(self.archive)/self.record_name), include_handle=True))
            self.archive_handle = self.stack.enter_context(self.native._handle(
                self.api, self.archive, access=0xA3, share=3))
            self.native._identity(self.api, self.archive_handle, self.archive, directory=True)
            journal_path = Path(self.journal_path)
            self.journal = self.stack.enter_context(journal_path.open('x', encoding='utf-8'))
            keep_path = Path(self.archive)/'KEEP.txt'
            if not os.path.lexists(keep_path):
                with keep_path.open('x', encoding='utf-8') as keep:
                    keep.write(journal_path.name + '\nHOLD.json\n' + self.record_name + '\n')
                    keep.flush(); os.fsync(keep.fileno())
            self.stack.enter_context(_ordinary_file(self.native, self.api, str(keep_path)))
            review = datetime.datetime.now(datetime.timezone.utc).date() + datetime.timedelta(days=30)
            hold_path = Path(self.archive)/'HOLD.json'
            if not os.path.lexists(hold_path):
                with hold_path.open('x', encoding='utf-8') as hold:
                    json.dump({'reason': 'Preserved junction objects and recovery journal; manual recovery review required',
                               'review_by': review.isoformat()}, hold)
                    hold.flush(); os.fsync(hold.fileno())
            self.stack.enter_context(_ordinary_file(self.native, self.api, str(hold_path)))
            self._event('planned', plan=self.plan)
            self.state = 'staging'
            for number, expected in enumerate(self.plan['entries']):
                path = expected['path']
                targets = self.stack.enter_context(_chain(self.native, self.api, expected['target']))
                if not _chain_same(targets, expected['target_chain']):
                    raise JunctionRefused('target namespace changed before staging')
                with _chain(self.native, self.api, str(Path(path).parent)) as sources, _ordinary_file(
                        self.native, self.api, expected['manifest']['path'], include_handle=True) as manifest_guard:
                    _source_protection(sources)
                    if not _manifest_same(expected['manifest'], _manifest_name(path), manifest_guard['identity']):
                        raise JunctionRefused('source manifest changed before staging')
                    if not _chain_same(sources, expected['source_chain']):
                        raise JunctionRefused('source namespace changed before staging')
                    root_row = next((i for i in sources if i['path_key'] == path_key(root)), None)
                    if root_row is None or root_row['identity'] != self.plan['root_identity']:
                        raise JunctionRefused('worktree root identity changed before staging')
                    with self.native._handle(self.api, str(Path(path).parent), access=0x81, share=3) as parent:
                        if _key(self.native._identity(self.api, parent, str(Path(path).parent), True)) != expected['parent_identity']:
                            raise JunctionRefused('original parent identity changed')
                    handle = self.stack.enter_context(self.native._handle(self.api, path, access=0x10080, share=0))
                    actual = _read_link(self.native, self.api, handle, path, root)
                    if not _same(actual, expected):
                        raise JunctionRefused('junction changed before staging')
                    leaf = f'{self.prefix}junction-{number:04d}-{hashlib.sha256(expected["relative"].encode()).hexdigest()[:16]}'
                    destination = str(Path(self.archive)/leaf)
                    item = {'expected': expected, 'handle': handle, 'destination': destination, 'moved': False}
                    self.items.append(item)
                    self._event('move-intent', relative=expected['relative'], destination=destination)
                    _rename(self.api, handle, leaf, parent_handle=self.archive_handle, anchor=self.record_guard)
                    item['moved'] = True
                    if not _same(_read_link(self.native, self.api, handle, destination, root), expected):
                        raise JunctionRefused('staged junction identity changed')
                    self._event('staged', relative=expected['relative'], destination=destination)
            self._event('staging-complete')
            self.state = 'staged'
            return self
        except BaseException as error:
            try:
                self.rollback()
            finally:
                self.stack.close()
            if isinstance(error, (OSError, ValueError, self.native.PlatformRefused)):
                raise JunctionRefused('junction staging failed; source or protected escrow retained') from error
            raise

    def commit(self):
        if os.path.lexists(self.plan['root']):
            raise JunctionRefused('cannot commit before verified worktree source disappearance')
        for item in self.items:
            with _chain(self.native, self.api, item['expected']['target']) as targets:
                if not _chain_same(targets, item['expected']['target_chain']):
                    raise JunctionRefused('shared target namespace changed before commit')
            if not _same(_read_link(self.native, self.api, item['handle'], item['destination'], self.plan['root']), item['expected']):
                raise JunctionRefused('preserved junction changed before commit')
        self._event('committed', source_absent=True, retained_objects=[i['destination'] for i in self.items])
        self.committed = True
        self.state = 'committed'

    def rollback(self):
        errors = []
        for item in reversed(self.items):
            expected, original = item['expected'], item['expected']['path']
            try:
                # Reconcile the opened object even if interruption arrived
                # immediately after Rename returned but before the flag update.
                actual_name = _handle_name(self.api, item['handle'])
                if path_key(actual_name) == path_key(original):
                    item['moved'] = False
                    continue
                if path_key(actual_name) != path_key(item['destination']):
                    raise JunctionRefused('preserved junction has an unexpected name')
                item['moved'] = True
                with _chain(self.native, self.api, str(Path(original).parent)), _ordinary_file(
                        self.native, self.api, expected['manifest']['path'], include_handle=True) as manifest_guard:
                    if not _manifest_same(expected['manifest'], _manifest_name(original), manifest_guard['identity']):
                        raise JunctionRefused('rollback source manifest changed')
                    with self.native._handle(self.api, str(Path(original).parent), access=0xA3, share=3) as parent:
                        if _key(self.native._identity(self.api, parent, str(Path(original).parent), True)) != expected['parent_identity']:
                            raise JunctionRefused('rollback parent was replaced')
                        if os.path.lexists(original):
                            raise JunctionRefused('rollback slot is occupied')
                        if not _same(_read_link(self.native, self.api, item['handle'], item['destination'], self.plan['root']), expected):
                            raise JunctionRefused('rollback junction changed')
                        with contextlib.suppress(OSError):
                            self._event('rollback-intent', relative=expected['relative'])
                        _rename(self.api, item['handle'], ntpath.basename(original), parent_handle=parent,
                                anchor=manifest_guard)
                        if not _same(_read_link(self.native, self.api, item['handle'], original, self.plan['root']), expected):
                            raise JunctionRefused('restored junction could not be verified')
                        item['moved'] = False
                        with contextlib.suppress(OSError):
                            self._event('restored', relative=expected['relative'])
            except (OSError, ValueError, JunctionRefused, self.native.PlatformRefused):
                errors.append(expected['relative'])
        if self.journal is not None:
            with contextlib.suppress(OSError):
                self._event('rollback-incomplete' if errors else 'rolled-back', unresolved=errors)
        self.state = 'rollback-incomplete' if errors else 'rolled-back'
        if errors:
            raise JunctionRefused('junction objects retained in archive; rollback requires reconciliation')

    def __exit__(self, kind, value, traceback):
        try:
            if not self.committed:
                self.rollback()
        finally:
            self.stack.close()
        return False


def escrow(plan, archive_entry, native, *, prefix='', record_name='RETIRED.json'):
    """Caller lock -> durable staging -> ordinary recycle proof -> commit.

    On failure, restore original slots without replacement; a conflict keeps the
    objects in the protected archive and requires reconciliation. Do not clear
    Git registration on any uncertain result. Targets are retained, not evicted.
    """
    return _Escrow(plan, archive_entry, native, prefix=prefix, record_name=record_name)
