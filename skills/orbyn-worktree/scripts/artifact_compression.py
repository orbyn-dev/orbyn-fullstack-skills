"""Opt-in, lossless compression of explicitly owned idle compiler files on NTFS.

This module never deletes files, follows junctions, invokes build tools or marks a
directory compressed. Unknown ownership, usage or storage semantics means hold.
"""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time


DAY = 86400
EXTENSIONS = {'.rlib', '.rmeta', '.pdb', '.obj', '.lib', '.o'}
FORBIDDEN = {'.git', '.codex', '.claude', 'node_modules', '.pnpm-store', 'go-mod',
             'output', 'outputs', 'evidence', 'artifacts', 'emulator', 'data'}
MAX_FILES = 10000


class Held(RuntimeError):
    """A required preservation proof is unavailable or changed."""


def _native_module():
    spec = importlib.util.spec_from_file_location(
        '_orbyn_compression_platform', Path(__file__).with_name('retirement_platform.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _kind(root):
    parts = tuple(p.lower() for p in root.parts)
    if any(p in FORBIDDEN or p.startswith(('.env', 'shared-')) or p == 'shared' for p in parts):
        raise Held('source, shared data, host-managed or evidence path is protected')
    if root.name.lower() == 'go-build' and root.parent.name.lower() == '.cache':
        return 'go'
    if (root.name.lower() == 'node-compile-cache'
            and any(p.name.lower().endswith(('task-temp', 'task-cache', 'task-caches'))
                    or p.name.lower() in ('.cache', 'cache', 'caches') for p in root.parents)):
        return 'node'
    text = root.as_posix().lower()
    if re.search(r'/(?:target|cargo-target)/(?:[a-z0-9_-]+/)?(?:debug|release)/(?:deps|build|incremental)(?:/[^/]+)?$', text):
        return 'cargo'
    raise Held('unrecognized private compiler-cache layout')


def _compiler_file(path, kind):
    if kind == 'go':
        return bool(re.fullmatch(r'[0-9a-f]{64}-[ad]', path.name))
    if kind == 'node':
        return not path.suffix and bool(re.fullmatch(r'[0-9a-fA-F]+', path.name))
    return path.suffix.lower() in EXTENSIONS


def _snapshot(path):
    info = path.lstat()  # DirEntry.stat has incomplete identity/link fields on Windows.
    attrs = getattr(info, 'st_file_attributes', 0)
    if attrs & 0x400 or stat.S_ISLNK(info.st_mode):
        raise Held('links and reparse points are held')
    return {'dev': info.st_dev, 'ino': info.st_ino, 'bytes': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns,
            'links': info.st_nlink, 'attributes': attrs,
            'directory': stat.S_ISDIR(info.st_mode), 'regular': stat.S_ISREG(info.st_mode)}


def _same(expected, actual, *, directory=False):
    keys = ('dev', 'ino') if directory else ('dev', 'ino', 'bytes', 'mtime_ns', 'ctime_ns', 'links')
    return all(expected.get(key) == actual.get(key) for key in keys)


def _protect(root):
    for parent in (root, *root.parents):
        _snapshot(parent)
        if any(os.path.lexists(parent/name) for name in ('KEEP.txt', 'HOLD.json')):
            raise Held('retention marker protects this root')
    if root.resolve() != root:
        raise Held('root resolves through an alias')


def _file_protect(path, root):
    _protect(path.parent)
    for parent in (path.parent, *path.parent.parents):
        if os.path.lexists(parent/'.git'):
            raise Held('nested Git ownership appeared in compiler root')
        if parent == root:
            break


def _owner(root):
    return next((p for p in (root, *root.parents) if os.path.lexists(p/'.git')), None)


def _git(owner, *args):
    result = subprocess.run(['git', '-C', str(owner), *args], capture_output=True, timeout=15)
    if result.returncode or len(result.stdout) > 8*1024*1024:
        raise Held('Git ownership inspection failed or exceeded its bound')
    return result.stdout


def _owner_proof(root, now, max_entries, deadline):
    owner = _owner(root)
    if owner is None:
        return None  # Exact root was supplied by the owner policy, never discovery.
    names = _git(owner, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0')
    if len(names) > max_entries:
        raise Held('source activity inspection exceeded its bound')
    for raw in names:
        if not raw:
            continue
        if time.monotonic() >= deadline:
            raise Held('source activity inspection exceeded its time bound')
        path = owner/os.fsdecode(raw)
        if path.is_relative_to(root):
            raise Held('tracked or non-ignored source exists in compiler root')
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise Held('source activity is uncertain') from None
        if max(info.st_mtime, info.st_ctime) > now - 7200:
            raise Held('owning source has activity within two hours')
    git_dir = Path(os.fsdecode(_git(owner, 'rev-parse', '--absolute-git-dir')).strip())
    for name in ('index.lock', 'HEAD.lock', 'config.lock', 'MERGE_HEAD', 'rebase-apply', 'rebase-merge'):
        if os.path.lexists(git_dir/name):
            raise Held('Git operation is in progress')
    return str(owner)


class NativeBackend:
    """Pinned native handles exclude writers and prevent source replacement."""
    def __init__(self):
        self.native = _native_module()
        if os.name != 'nt':
            raise Held('NTFS compression is supported only on native Windows')
        self.api = self.native._win32()

    def validate_root(self, root):
        if root.drive.upper() not in ('C:', 'D:') or not root.is_absolute() or len(root.parts) < 3:
            raise Held('only explicit local C: or D: compiler roots are supported')
        import ctypes as c
        from ctypes import wintypes as w
        kernel = self.api[2]
        function = kernel.GetVolumeInformationW
        function.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, c.POINTER(w.DWORD),
                             c.POINTER(w.DWORD), c.POINTER(w.DWORD), w.LPWSTR, w.DWORD]
        function.restype = w.BOOL
        serial, maximum, flags = w.DWORD(), w.DWORD(), w.DWORD()
        label, filesystem = c.create_unicode_buffer(261), c.create_unicode_buffer(261)
        if not function(root.anchor, label, 261, c.byref(serial), c.byref(maximum),
                        c.byref(flags), filesystem, 261):
            raise c.WinError(c.get_last_error())
        if filesystem.value != 'NTFS' or not flags.value & 0x10:
            raise Held('volume does not provide qualified NTFS file compression')

    def usage(self, root, seconds, entries):
        result = self.native.usage_details(root, max_seconds=seconds, max_entries=entries)
        if result['state'] != 'clear':
            raise Held('native usage is busy or unknown: '+result['reason_code'])

    @contextmanager
    def pin_root(self, root, expected):
        try:
            with self.native._pinned_ancestors(self.api, root), self.native._handle(self.api, str(root)) as handle:
                self.native._identity(self.api, handle, str(root), True)
                if not _same(expected, _snapshot(root), directory=True):
                    raise Held('root identity changed')
                yield
                self.native._identity(self.api, handle, str(root), True)
        except self.native.PlatformRefused as error:
            raise Held(str(error)) from error

    @contextmanager
    def pin_file(self, path, expected):
        try:
            with self.native._pinned_ancestors(self.api, path), self.native._handle(
                    self.api, str(path), access=0xC0000000, share=1) as handle:
                self.native._identity(self.api, handle, str(path), False)
                if not _same(expected, _snapshot(path)):
                    raise Held('file identity or content metadata changed')
                yield handle
                self.native._identity(self.api, handle, str(path), False)
        except self.native.PlatformRefused as error:
            raise Held(str(error)) from error

    def verify(self, handle, path):
        try:
            self.native._identity(self.api, handle, str(path), False)
        except self.native.PlatformRefused as error:
            raise Held(str(error)) from error

    def free_space(self, root):
        return shutil.disk_usage(root.anchor).free

    def allocation(self, handle):
        import ctypes as c
        from ctypes import wintypes as w
        class Standard(c.Structure):
            _fields_ = [('allocation', c.c_longlong), ('length', c.c_longlong), ('links', w.DWORD),
                        ('pending', c.c_ubyte), ('directory', c.c_ubyte)]
        info = Standard()
        function = self.api[2].GetFileInformationByHandleEx
        function.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        function.restype = w.BOOL
        if not function(handle, 1, c.byref(info), c.sizeof(info)):
            raise c.WinError(c.get_last_error())
        if info.links != 1 or info.pending or info.directory:
            raise Held('file no longer has exclusive ordinary ownership')
        return info.allocation

    def digest(self, handle):
        import ctypes as c
        from ctypes import wintypes as w
        kernel = self.api[2]
        kernel.SetFilePointerEx.argtypes = [w.HANDLE, c.c_longlong, c.POINTER(c.c_longlong), w.DWORD]
        kernel.SetFilePointerEx.restype = w.BOOL
        kernel.ReadFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]
        kernel.ReadFile.restype = w.BOOL
        if not kernel.SetFilePointerEx(handle, 0, None, 0):
            raise c.WinError(c.get_last_error())
        digest, buffer, count = hashlib.sha256(), c.create_string_buffer(1024*1024), w.DWORD()
        while True:
            if not kernel.ReadFile(handle, buffer, len(buffer), c.byref(count), None):
                raise c.WinError(c.get_last_error())
            if not count.value:
                return digest.hexdigest()
            digest.update(buffer.raw[:count.value])

    def compress(self, handle, compress=True):
        import ctypes as c
        from ctypes import wintypes as w
        function = self.api[2].DeviceIoControl
        function.argtypes = [w.HANDLE, w.DWORD, c.c_void_p, w.DWORD,
                             c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]
        function.restype = w.BOOL
        format_value, returned = w.WORD(1 if compress else 0), w.DWORD()
        if not function(handle, 0x0009C040, c.byref(format_value), c.sizeof(format_value),
                        None, 0, c.byref(returned), None):
            raise c.WinError(c.get_last_error())


def _scan(root, backend, now, idle_days, max_entries, deadline):
    _protect(root)
    backend.validate_root(root)
    root_info = _snapshot(root)
    if max(root_info['mtime_ns'], root_info['ctime_ns'])/1e9 > now-idle_days*DAY:
        raise Held('compiler root itself has recent activity')
    kind, files, stack, entries = _kind(root), [], [root], 0
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > max_entries or time.monotonic() >= deadline:
                    raise Held('compiler inspection budget exhausted')
                path = Path(child.path)
                if (child.name.lower() in ('keep.txt', 'hold.json', '.git')
                        or child.name.lower().startswith('.env')
                        or re.search(r'(^|[._-])(?:secrets?|credentials?|tokens?|private[_-]?key)([._-]|$)', child.name, re.I)):
                    raise Held('protected content in compiler root')
                info = _snapshot(path)
                if max(info['mtime_ns'], info['ctime_ns'])/1e9 > now-idle_days*DAY:
                    raise Held('compiler root contains recent activity')
                if info['directory']:
                    stack.append(path)
                elif info['regular'] and _compiler_file(path, kind) and info['bytes'] >= 65536:
                    if info['links'] != 1 or info['attributes'] & (0x800 | 0x200 | 0x4000 | 0x1000):
                        continue
                    if len(files) >= MAX_FILES:
                        raise Held('compiler file limit exceeded')
                    files.append({'path': str(path), 'identity': info})
    return files


def _tool_locks(root, backend, seconds):
    # Persistent Cargo lock files are normal; inspect their usage, never remove
    # them or infer idleness from their timestamps.
    for parent in (root, *root.parents):
        for name in ('.cargo-lock', '.package-cache'):
            lock = parent/name
            if os.path.lexists(lock):
                backend.usage(lock, min(seconds, 3), 1)
        if parent.name in ('target', 'cargo-target'):
            break


def report(roots, *, profile='aggressive', enabled=False, idle_days=7,
           max_entries=100000, max_seconds=60, backend=None, policy_check=None, now=None):
    """Read-only eligibility and allocation; policy roots must come from the owner."""
    result = {'schema_version': 1, 'action': 'ntfs-compress', 'profile': profile,
              'enabled': enabled, 'roots': [], 'status': 'held', 'idle_days': idle_days,
              'max_entries': max_entries, 'max_seconds': max_seconds,
              'totals': {'files': 0, 'allocated_bytes': 0}}
    if not enabled or profile != 'aggressive':
        result['reason'] = 'compression requires owner opt-in and Aggressive profile'
        return result
    if (not isinstance(roots, (list, tuple)) or not 0 < len(roots) <= 32
            or type(idle_days) is not int or not 7 <= idle_days <= 3650
            or type(max_entries) is not int or not 1 <= max_entries <= 2000000
            or type(max_seconds) not in (int, float) or not 0 < max_seconds <= 300):
        raise Held('invalid bounded compression configuration')
    now = time.time() if now is None else now
    try:
        backend = backend or NativeBackend()
    except (Held, OSError) as error:
        result['reason'] = str(error)
        return result
    seen = set()
    for name in roots:
        root = Path(name)
        row = {'root': str(root), 'status': 'held', 'files': []}
        result['roots'].append(row)
        try:
            folded = ntpath.normcase(str(root))
            if folded in seen or any(root.is_relative_to(Path(x)) or Path(x).is_relative_to(root) for x in seen):
                raise Held('duplicate or overlapping compiler roots')
            seen.add(folded)
            deadline = time.monotonic()+max_seconds
            _protect(root)
            backend.validate_root(root)
            _kind(root)
            owner = _owner_proof(root, now, max_entries, deadline)
            row['owner'] = owner
            if policy_check is not None and not policy_check(str(root), owner):
                raise Held('owner or target policy excludes this root')
            files = _scan(root, backend, now, idle_days, max_entries, deadline)
            backend.usage(root, max_seconds, max_entries)
            _tool_locks(root, backend, max_seconds)
            row['identity'] = _snapshot(root)
            with backend.pin_root(root, row['identity']):
                for file in files:
                    with backend.pin_file(Path(file['path']), file['identity']) as handle:
                        file['allocated_bytes'] = backend.allocation(handle)
            row['files'] = files
            row['allocated_bytes'] = sum(f['allocated_bytes'] for f in files)
            row['status'] = 'eligible' if files else 'held'
            if not files:
                row['reason'] = 'no matching ordinary uncompressed private compiler files'
            result['totals']['files'] += len(files)
            result['totals']['allocated_bytes'] += row['allocated_bytes']
        except (Held, OSError, ValueError, subprocess.SubprocessError) as error:
            row['reason'] = str(error)
    result['status'] = 'eligible' if result['totals']['files'] else 'held'
    return result


def apply(plan, *, receipt_path, profile='aggressive', enabled=False, backend=None, policy_check=None):
    """Revalidate a same-run preview, journal first, then compress pinned files."""
    if not enabled or profile != 'aggressive' or plan.get('profile') != profile or not plan.get('enabled'):
        raise Held('compression requires current owner opt-in and Aggressive profile')
    backend = backend or NativeBackend()
    receipt_path = Path(receipt_path)
    if receipt_path.exists() or os.path.lexists(receipt_path):
        raise Held('existing receipt requires explicit reconciliation; no automatic retry')
    if any(receipt_path.is_relative_to(Path(r['root'])) for r in plan['roots']):
        raise Held('receipt cannot reside inside a compression root')
    roots = [row['root'] for row in plan['roots']]
    fresh = report(roots, profile=profile, enabled=enabled, idle_days=plan['idle_days'],
                   max_entries=plan['max_entries'], max_seconds=plan['max_seconds'],
                   backend=backend, policy_check=policy_check)
    receipt = {'schema_version': 1, 'action': 'ntfs-compress', 'complete': False,
               'started_epoch': time.time(), 'items': [], 'roots': [], 'allocated_savings': 0}
    def free_space():
        values = {}
        for root in roots:
            path = Path(root)
            try:
                values[path.anchor] = backend.free_space(path) if hasattr(backend, 'free_space') else None
            except OSError:
                values[path.anchor] = None
        return values
    receipt['free_before'] = free_space()
    # Receipt parent must already be an authorized, ordinary directory. Never mkdir
    # into a target/cache or follow a replaced receipt ancestor.
    for ancestor in (receipt_path.parent, *receipt_path.parent.parents):
        info = _snapshot(ancestor)
        if not info['directory']:
            raise Held('receipt parent is not an ordinary directory')
    with backend.pin_root(receipt_path.parent, _snapshot(receipt_path.parent)):
        with receipt_path.open('x', encoding='utf-8') as stream:
            def save():
                stream.seek(0)
                json.dump(receipt, stream, indent=2)
                stream.write('\n')
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            save()
            try:
                for original, row in zip(plan['roots'], fresh['roots']):
                    root = Path(row['root'])
                    expected_files = {f['path']: f for f in original['files']}
                    if (original['status'] != 'eligible' or row['status'] != 'eligible'
                            or not _same(original['identity'], row['identity'], directory=True)
                            or set(expected_files) != {f['path'] for f in row['files']}):
                        receipt['roots'].append({'root': str(root), 'status': 'held', 'reason': 'preview changed or no longer eligible'})
                        save()
                        continue
                    if any(not _same(expected_files[f['path']]['identity'], f['identity']) for f in row['files']):
                        receipt['roots'].append({'root': str(root), 'status': 'held', 'reason': 'file identity changed'})
                        save()
                        continue
                    with backend.pin_root(root, original['identity']):
                        _tool_locks(root, backend, plan['max_seconds'])
                        for file in row['files']:
                            _protect(root)
                            if policy_check is not None and not policy_check(str(root), row.get('owner')):
                                raise Held('policy changed before compression')
                            path = Path(file['path'])
                            _file_protect(path, root)
                            backend.usage(path, min(plan['max_seconds'], 3), 1)
                            with backend.pin_file(path, file['identity']) as handle:
                                before_hash, before_bytes = backend.digest(handle), backend.allocation(handle)
                                receipt['pending'] = {'path': str(path), 'sha256_before': before_hash,
                                                      'allocated_before': before_bytes, 'identity': file['identity']}
                                save()
                                _file_protect(path, root)
                                owner = _owner_proof(root, time.time(), plan['max_entries'],
                                                     time.monotonic()+plan['max_seconds'])
                                if owner != row.get('owner'):
                                    raise Held('Git ownership changed before compression')
                                _tool_locks(root, backend, plan['max_seconds'])
                                if policy_check is not None and not policy_check(str(root), owner):
                                    raise Held('policy changed at native mutation boundary')
                                backend.verify(handle, path)
                                operation_error = None
                                try:
                                    backend.compress(handle)
                                except OSError as error:
                                    operation_error = error
                                after_hash, after_bytes = backend.digest(handle), backend.allocation(handle)
                                item = {'path': str(path), 'sha256': before_hash, 'bytes_verified': before_hash == after_hash,
                                        'allocated_before': before_bytes, 'allocated_after': after_bytes,
                                        'status': 'compressed' if operation_error is None else 'failed'}
                                receipt['items'].append(item)
                                receipt.pop('pending')
                                receipt['allocated_savings'] += before_bytes-after_bytes
                                save()
                                if not item['bytes_verified']:
                                    raise Held('byte verification failed; stop and retain all evidence')
                                if operation_error is not None:
                                    raise Held('native compression failed; actual allocation and hash recorded') from operation_error
                                backend.verify(handle, path)
                    receipt['roots'].append({'root': str(root), 'status': 'compressed'})
                    save()
                receipt['complete'] = True
            except (Held, OSError, ValueError) as error:
                receipt['error'] = str(error)
            finally:
                receipt['finished_epoch'] = time.time()
                receipt['free_after'] = free_space()
                save()
    return receipt
