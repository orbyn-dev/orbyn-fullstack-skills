"""Native Windows boundaries for retirement; importing this module has no effects.

The caller owns policy/confinement and receipts. This module never permanently
deletes an item and never requests elevation. Unknown usage means hold.
"""
from contextlib import contextmanager, ExitStack, nullcontext
import ntpath
import math
import os
from pathlib import Path
import stat
import struct
import time


class PlatformRefused(Exception):
    def __init__(self, message, code='safety_unknown'):
        super().__init__(message)
        self.code = code


def is_windows():
    return os.name == 'nt'


def is_reparse(path):
    return _reparse_info(os.lstat(path))


def _reparse_info(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def host_managed(path):
    parts = os.fspath(path).replace('\\', '/').casefold().split('/')
    return any(parts[i] in ('.claude', '.codex') and parts[i + 1] == 'worktrees'
               for i in range(len(parts) - 1))


def _native_path(path):
    value = os.fspath(path)
    # Device namespaces, relative paths, alternate streams and Win32 aliases do
    # not have the ordinary filesystem identity this boundary promises.
    drive, tail = ntpath.splitdrive(value)
    if (not drive or len(drive) != 2 or drive[1] != ':' or not tail.startswith(('\\', '/'))
            or ':' in tail or '\x00' in value):
        raise PlatformRefused('a local absolute drive path is required', 'invalid_path')
    parts = tail.replace('/', '\\').split('\\')
    if any(p in ('.', '..') or (p and p.endswith((' ', '.'))) for p in parts):
        raise PlatformRefused('ambiguous Windows path', 'invalid_path')
    return ntpath.normpath(value)


def _win32():
    """Load only from System32, and type HANDLEs explicitly on 64-bit hosts."""
    if not is_windows():
        raise PlatformRefused('native Windows APIs are unavailable', 'api_unavailable')
    import ctypes as c
    from ctypes import wintypes as w

    class FileInfo(c.Structure):
        _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME),
                    ('accessed', w.FILETIME), ('written', w.FILETIME),
                    ('volume', w.DWORD), ('size_high', w.DWORD), ('size_low', w.DWORD),
                    ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]

    class Overlapped(c.Structure):
        _fields_ = [('Internal', c.c_size_t), ('InternalHigh', c.c_size_t),
                    ('Offset', w.DWORD), ('OffsetHigh', w.DWORD), ('hEvent', w.HANDLE)]

    kernel = c.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
    signatures = {
        'CreateFileW': ([w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
        'CloseHandle': ([w.HANDLE], w.BOOL),
        'GetFileInformationByHandle': ([w.HANDLE, c.POINTER(FileInfo)], w.BOOL),
        'GetFinalPathNameByHandleW': ([w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
        'LockFileEx': ([w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)], w.BOOL),
        'UnlockFileEx': ([w.HANDLE, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)], w.BOOL),
        'GetVolumePathNameW': ([w.LPCWSTR, w.LPWSTR, w.DWORD], w.BOOL),
        'GetDriveTypeW': ([w.LPCWSTR], w.UINT),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    return c, w, kernel, FileInfo, Overlapped


def _open(api, path, access, share, creation=3):
    c, _, kernel, _, _ = api
    handle = kernel.CreateFileW(path, access, share, None, creation, 0x02200000, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    return handle


@contextmanager
def _handle(api, path, access=0x80, share=3, creation=3):
    handle = _open(api, path, access, share, creation)
    try:
        yield handle
    finally:
        api[2].CloseHandle(handle)


def _identity(api, handle, path, directory=None):
    c, _, kernel, FileInfo, _ = api
    info = FileInfo()
    if not kernel.GetFileInformationByHandle(handle, c.byref(info)):
        raise c.WinError(c.get_last_error())
    if info.attributes & 0x400:
        raise PlatformRefused('reparse points are held', 'reparse')
    if directory is not None and bool(info.attributes & 0x10) != directory:
        raise PlatformRefused('filesystem entry changed type', 'type_changed')
    buffer = c.create_unicode_buffer(32768)
    length = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise PlatformRefused('cannot verify final handle path', 'final_path')
    final = buffer.value
    if final.startswith('\\\\?\\'):
        final = final[4:]
    if ntpath.normcase(final) != ntpath.normcase(path):
        raise PlatformRefused('handle path differs from requested path', 'final_path')
    observed = os.lstat(path)
    index = (info.index_high << 32) | info.index_low
    if _reparse_info(observed):
        raise PlatformRefused('reparse points are held', 'reparse')
    if observed.st_ino != index:
        raise PlatformRefused('filesystem entry identity changed', 'identity_changed')
    return info


@contextmanager
def _pinned_ancestors(api, path):
    """No delete-sharing: opened ancestors cannot be renamed or replaced."""
    parent = Path(path).parent
    with ExitStack() as stack:
        for directory in reversed((parent, *parent.parents)):
            name = str(directory)
            handle = stack.enter_context(_handle(api, name))
            _identity(api, handle, name, directory=True)
        yield


def usage(path, *, max_seconds=None, max_entries=None, reparse_probe=None):
    """Probe exactly the candidate and its descendants; None is unknown/held."""
    return _usage(path, max_seconds=max_seconds, max_entries=max_entries, reparse_probe=reparse_probe)


def _usage(path, root_handle=None, *, max_seconds=None, max_entries=None, reparse_probe=None):
    """A retained root is identity-checked instead of conflicting with our handle."""
    details = _usage_details(path, root_handle, max_seconds=max_seconds, max_entries=max_entries,
                             reparse_probe=reparse_probe)
    return None if details['state'] == 'unknown' else set(details['busy_paths'])


def usage_details(path, *, max_seconds=None, max_entries=None, reparse_probe=None):
    """Read-only diagnostics; incomplete inspection always stays unknown."""
    return _usage_details(path, max_seconds=max_seconds, max_entries=max_entries, reparse_probe=reparse_probe)


def _os_reason(error):
    if isinstance(error, PermissionError) or getattr(error, 'winerror', None) == 5:
        return 'access_denied'
    if isinstance(error, FileNotFoundError) or getattr(error, 'winerror', None) in (2, 3):
        return 'missing'
    return 'os_error'


def _validate_usage_bounds(max_seconds, max_entries):
    try:
        valid_time = max_seconds is None or (type(max_seconds) in (int, float)
                     and math.isfinite(max_seconds) and max_seconds >= 0)
    except (ValueError, OverflowError):
        valid_time = False
    if not valid_time or (max_entries is not None and (type(max_entries) is not int or max_entries < 0)):
        raise PlatformRefused('invalid usage inspection bounds', 'invalid_budget')


def directory_names(api, handle, *, deadline=None, limit=None, admitted=None):
    """List the opened no-follow directory object, never reopen its pathname."""
    c, w, kernel, _, _ = api
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.GetFileInformationByHandleEx.restype = w.BOOL
    data, names, restart = c.create_string_buffer(65536), [], True
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise PlatformRefused('directory inspection time budget exhausted', 'budget_time')
        if not kernel.GetFileInformationByHandleEx(handle, 15 if restart else 14, data, len(data)):
            error = c.get_last_error()
            if error == 18:  # ERROR_NO_MORE_FILES
                return sorted(names)
            raise c.WinError(error)
        restart, offset = False, 0
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise PlatformRefused('directory inspection time budget exhausted', 'budget_time')
            if offset + 68 > len(data):
                raise PlatformRefused('incomplete directory response', 'directory_response')
            following = struct.unpack_from('<I', data, offset)[0]
            length = struct.unpack_from('<I', data, offset + 60)[0]
            if length % 2 or not length or offset + 68 + length > len(data):
                raise PlatformRefused('invalid directory response', 'directory_response')
            try:
                name = data[offset + 68:offset + 68 + length].decode('utf-16-le', errors='strict')
            except UnicodeError as error:
                raise PlatformRefused('invalid directory entry encoding', 'directory_response') from error
            if name not in ('.', '..'):
                if any(x in name for x in ('\\', '/', ':', '\x00')) or name.endswith((' ', '.')):
                    raise PlatformRefused('ambiguous directory entry', 'directory_response')
                if limit is not None and len(names) >= limit:
                    raise PlatformRefused('directory inspection entry budget exhausted', 'budget_entries')
                if admitted is not None:
                    admitted()
                names.append(name)
            if not following:
                break
            if following % 8 or following < 68 + length or offset + following >= len(data):
                raise PlatformRefused('invalid directory response offset', 'directory_response')
            offset += following


def _usage_details(path, root_handle=None, *, max_seconds=None, max_entries=None, reparse_probe=None):
    started, visited, busy = time.monotonic(), 0, set()
    def result(state, reason):
        return {'state': state, 'reason_code': reason, 'elapsed_seconds': max(0.0, time.monotonic() - started),
                'visited_entries': visited, 'busy_paths': sorted(busy) if state == 'busy' else []}
    try:
        _validate_usage_bounds(max_seconds, max_entries)
        if reparse_probe is not None and not callable(reparse_probe):
            raise PlatformRefused('invalid reparse inspection callback', 'invalid_policy')
        path, api = _native_path(path), _win32()
        deadline = started + max_seconds if max_seconds is not None else None
        scheduled = 1  # candidate root; descendants are counted when first listed

        def listing(name, handle, after=False):
            nonlocal scheduled
            # The second listing checks an already visited directory; it must
            # not consume the traversal budget a second time.
            def admitted():
                nonlocal scheduled
                if not after:
                    if max_entries is not None and scheduled >= max_entries:
                        raise PlatformRefused('usage inspection entry budget exhausted', 'budget_entries')
                    scheduled += 1
            return [os.path.join(name, child) for child in directory_names(
                api, handle, deadline=deadline, limit=max_entries, admitted=admitted)]

        def visit(name, retained=None):
            nonlocal visited
            if deadline is not None and time.monotonic() >= deadline:
                raise PlatformRefused('usage inspection time budget exhausted', 'budget_time')
            if max_entries is not None and visited >= max_entries:
                raise PlatformRefused('usage inspection entry budget exhausted', 'budget_entries')
            visited += 1
            observed = os.lstat(name)
            if _reparse_info(observed):
                # Only the whole-worktree owner may supply a fresh, exact-entry
                # junction verifier. Root/ancestor links remain forbidden. The
                # callback probes the link object without entering its target;
                # native recycling itself never accepts this exception.
                if (ntpath.normcase(name) != ntpath.normcase(path)
                        and reparse_probe is not None and reparse_probe(name) is True):
                    return
                raise PlatformRefused('reparse points are held', 'reparse')
            directory = stat.S_ISDIR(observed.st_mode)
            try:
                # Attributes plus delete access, no sharing: observe open handles
                # and working directories without reading file data or modifying it.
                with (nullcontext(retained) if retained is not None else
                      _handle(api, name, 0x10080, 0)) as handle:
                    _identity(api, handle, name, directory)
            except OSError as error:
                if getattr(error, 'winerror', None) not in (32, 33):
                    raise
                busy.add(name)
            if busy and (deadline is not None or max_entries is not None):
                return  # negative evidence needs no further traversal
            if directory:
                # LIST_DIRECTORY is required for handle-based enumeration. A
                # separate shared handle works even when a retained root only
                # has metadata access, and cannot follow a retargeted pathname.
                with _handle(api, name, access=0x81, share=7) as handle:
                    _identity(api, handle, name, True)
                    before = os.lstat(name).st_mtime_ns
                    names = listing(name, handle)
                    for child in names:
                        visit(child)
                        if busy and (deadline is not None or max_entries is not None):
                            return
                    _identity(api, handle, name, True)
                    after = listing(name, handle, after=True)
                    if retained is not None:
                        _identity(api, retained, name, True)
                    if before != os.lstat(name).st_mtime_ns or set(names) != set(after):
                        raise PlatformRefused('candidate changed during usage probe', 'candidate_changed')

        with _pinned_ancestors(api, path):
            visit(path, root_handle)
        if not busy and deadline is not None and time.monotonic() >= deadline:
            raise PlatformRefused('usage inspection time budget exhausted', 'budget_time')
        return result('busy', 'busy') if busy else result('clear', 'clear')
    except PlatformRefused as error:
        return result('unknown', error.code)
    except OSError as error:
        return result('unknown', _os_reason(error))
    except RecursionError:
        return result('unknown', 'recursion_limit')
    except ValueError:
        return result('unknown', 'invalid_path')


def probe_dirs(paths):
    """Non-recursive owner/lock handle probes; missing or uncertain entries hold.

    This is a snapshot, not proof that a quiet process cannot resume. The caller
    combines it with artifact inspection, tool locks and the owner quiet window.
    """
    try:
        api = _win32()
        busy = set()
        for given in paths:
            name = _native_path(given)
            directory = stat.S_ISDIR(os.lstat(name).st_mode)
            with _pinned_ancestors(api, name):
                try:
                    with _handle(api, name, 0x10080, 0) as handle:
                        _identity(api, handle, name, directory)
                except OSError as error:
                    if getattr(error, 'winerror', None) not in (32, 33):
                        raise
                    busy.add(name)
        return busy
    except (OSError, ValueError, PlatformRefused):
        return None


@contextmanager
def cleanup_lock(folder):
    """Hold folder/.cleanup.lock without unlinking or following any reparse point."""
    try:
        api = _win32()
        folder = _native_path(folder)
        with _pinned_ancestors(api, folder):
            if not os.path.lexists(folder):
                os.mkdir(folder)
            with _handle(api, folder) as directory:
                _identity(api, directory, folder, True)
                path = ntpath.join(folder, '.cleanup.lock')
                with _handle(api, path, 0xC0000000, 3, 4) as handle:
                    info = _identity(api, handle, path, False)
                    if info.links != 1:
                        raise PlatformRefused('cleanup lock has multiple hard links')
                    c, _, kernel, _, Overlapped = api
                    overlap = Overlapped()
                    # Byte locking past EOF is supported; do not overwrite a
                    # possibly preexisting lock file just to acquire a lock.
                    if not kernel.LockFileEx(handle, 3, 0, 1, 0, c.byref(overlap)):
                        raise PlatformRefused('cleanup lock is busy or unavailable')
                    try:
                        if _identity(api, handle, path, False).links != 1:
                            raise PlatformRefused('cleanup lock identity changed')
                        yield
                        if _identity(api, handle, path, False).links != 1:
                            raise PlatformRefused('cleanup lock identity changed while held')
                    finally:
                        if not kernel.UnlockFileEx(handle, 0, 1, 0, c.byref(overlap)):
                            raise PlatformRefused('cleanup lock release failed')
    except OSError as error:
        raise PlatformRefused('cleanup lock could not be safely opened') from error


class _DeleteProgress:
    """Testable proof state; callback adapters must never let Python escape COM."""
    def __init__(self, guard=None):
        self.guard = guard
        self.reason = None
        self.pre_count = 0
        self.post_count = 0

    def pre_delete(self, flags, source=None):
        self.pre_count += 1
        if not flags & 0x80:
            self.reason = 'Shell did not offer recycling; deletion vetoed'
            return -2147467259  # E_FAIL
        if self.guard is not None:
            try:
                self.guard(source)
            except (OSError, PlatformRefused):
                self.reason = 'candidate changed or became busy before deletion; deletion vetoed'
                return -2147467259
        return 0

    def post_delete(self, result, recycled):
        self.post_count += 1
        if result < 0 or not recycled:
            self.reason = 'Shell did not confirm a Recycle Bin destination'
            return -2147467259
        return 0

    def proof(self, result, aborted):
        return {'result': result, 'aborted': bool(aborted), 'reason': self.reason,
                'pre_count': self.pre_count, 'post_count': self.post_count}


def _shell_recycle(path, guard=None):
    """One STA IFileOperation, with a per-item veto and verified post-delete proof."""
    import ctypes as c
    from ctypes import wintypes as w
    import uuid

    HRESULT = c.c_long
    pointer = c.c_void_p
    callback = c.WINFUNCTYPE

    class GUID(c.Structure):
        _fields_ = [('bytes', c.c_ubyte * 16)]

    def guid(value):
        return GUID((c.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))

    def method(obj, index, result, *args):
        table = c.cast(obj, c.POINTER(c.POINTER(pointer))).contents
        return callback(result, pointer, *args)(table[index])

    def check(result, step):
        if result < 0:
            raise PlatformRefused(f'{step} failed (HRESULT 0x{result & 0xffffffff:08x})')

    ole = c.WinDLL('ole32.dll', winmode=0x800)
    shell = c.WinDLL('shell32.dll', winmode=0x800)
    ole.CoInitializeEx.argtypes, ole.CoInitializeEx.restype = [pointer, w.DWORD], HRESULT
    ole.CoUninitialize.argtypes, ole.CoUninitialize.restype = [], None
    ole.CoTaskMemFree.argtypes, ole.CoTaskMemFree.restype = [pointer], None
    ole.CoCreateInstance.argtypes = [c.POINTER(GUID), pointer, w.DWORD, c.POINTER(GUID), c.POINTER(pointer)]
    ole.CoCreateInstance.restype = HRESULT
    shell.SHCreateItemFromParsingName.argtypes = [w.LPCWSTR, pointer, c.POINTER(GUID), c.POINTER(pointer)]
    shell.SHCreateItemFromParsingName.restype = HRESULT
    check(ole.CoInitializeEx(None, 2), 'STA initialization')
    operation, item = pointer(), pointer()
    progress = _DeleteProgress(guard)
    references = [1]
    callbacks = []
    iid_sink = guid('04b0f1a7-9490-44bc-96e1-4296a31252e2')
    iid_unknown = guid('00000000-0000-0000-c000-000000000046')

    def item_path(source):
        allocated = pointer()
        try:
            check(method(source, 5, HRESULT, w.DWORD, c.POINTER(pointer))(
                source, 0x80058000, c.byref(allocated)), 'Shell source filesystem path')
            if not allocated:
                raise PlatformRefused('Shell source has no filesystem path')
            return c.wstring_at(allocated)
        finally:
            if allocated:
                ole.CoTaskMemFree(allocated)

    # All callbacks own their errors locally. ctypes otherwise prints an
    # exception and returns an undefined HRESULT to native deletion code.
    def adapt(function, result, *args):
        def guarded(*values):
            try:
                return function(*values)
            except BaseException:
                progress.reason = 'progress callback failed; deletion vetoed'
                return -2147467259
        native = callback(result, pointer, *args)(guarded)
        callbacks.append(native)
        return c.cast(native, pointer).value

    def query(this, requested, output):
        output[0] = None
        if bytes(requested.contents) not in (bytes(iid_sink), bytes(iid_unknown)):
            return -2147467262  # E_NOINTERFACE
        output[0] = this
        references[0] += 1
        return 0

    def add_ref(this):
        references[0] += 1
        return references[0]

    def release(this):
        references[0] -= 1
        return references[0]

    ok = lambda *args: 0
    entries = [adapt(query, HRESULT, c.POINTER(GUID), c.POINTER(pointer)),
               adapt(add_ref, w.ULONG), adapt(release, w.ULONG),
               adapt(ok, HRESULT), adapt(ok, HRESULT, HRESULT),  # Start/Finish
               adapt(ok, HRESULT, w.DWORD, pointer, w.LPCWSTR),  # PreRename
               adapt(ok, HRESULT, w.DWORD, pointer, w.LPCWSTR, HRESULT, pointer),
               adapt(ok, HRESULT, w.DWORD, pointer, pointer, w.LPCWSTR),  # PreMove
               adapt(ok, HRESULT, w.DWORD, pointer, pointer, w.LPCWSTR, HRESULT, pointer),
               adapt(ok, HRESULT, w.DWORD, pointer, pointer, w.LPCWSTR),  # PreCopy
               adapt(ok, HRESULT, w.DWORD, pointer, pointer, w.LPCWSTR, HRESULT, pointer),
               adapt(lambda this, flags, source: progress.pre_delete(flags, item_path(source)),
                     HRESULT, w.DWORD, pointer),
               adapt(lambda this, flags, source, result, destination: progress.post_delete(result, bool(destination)),
                     HRESULT, w.DWORD, pointer, HRESULT, pointer),
               adapt(ok, HRESULT, w.DWORD, pointer, w.LPCWSTR),  # PreNew
               adapt(ok, HRESULT, w.DWORD, pointer, w.LPCWSTR, w.LPCWSTR, w.DWORD, HRESULT, pointer),
               adapt(ok, HRESULT, w.UINT, w.UINT),  # UpdateProgress
               adapt(ok, HRESULT), adapt(ok, HRESULT), adapt(ok, HRESULT)]
    table = (pointer * len(entries))(*entries)

    class Sink(c.Structure):
        _fields_ = [('vtable', c.POINTER(pointer))]

    sink = Sink(table)
    try:
        clsid = guid('3ad05575-8857-4850-9277-11b85bdb8e09')
        iid_operation = guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
        check(ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid_operation), c.byref(operation)),
              'IFileOperation creation')
        # No confirmation/progress/error UI, early abort, recycle-on-delete.
        # In particular, never set SHOWELEVATIONPROMPT or require elevation.
        flags = 0x00080000 | 0x00100000 | 0x2000 | 0x0400 | 0x0010 | 0x0004
        check(method(operation, 5, HRESULT, w.DWORD)(operation, flags), 'recycle flags')
        iid_item = guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')
        check(shell.SHCreateItemFromParsingName(path, None, c.byref(iid_item), c.byref(item)), 'Shell item creation')
        if guard is not None:
            guard(item_path(item))
        check(method(operation, 18, HRESULT, pointer, pointer)(operation, item, c.byref(sink)), 'queue recycle')
        result = method(operation, 21, HRESULT)(operation)
        aborted = w.BOOL()
        check(method(operation, 22, HRESULT, c.POINTER(w.BOOL))(operation, c.byref(aborted)), 'recycle aborted state')
        return progress.proof(result, aborted.value)
    finally:
        # Operation retains its sink/item until released; keep Python callback
        # references alive throughout that release, even after partial setup.
        if operation:
            method(operation, 2, w.ULONG)(operation)
        if item:
            method(item, 2, w.ULONG)(item)
        ole.CoUninitialize()


def _bin_details(api, volume_root):
    """Strict aggregate query, shared by retirement proof and read-only reports."""
    c = api[0]
    from ctypes import wintypes as w

    class BinInfo(c.Structure):
        _fields_ = [('size', w.DWORD), ('bytes', c.c_int64), ('items', c.c_int64)]

    shell = c.WinDLL('shell32.dll', winmode=0x800)
    shell.SHQueryRecycleBinW.argtypes = [w.LPCWSTR, c.POINTER(BinInfo)]
    shell.SHQueryRecycleBinW.restype = c.c_long
    info = BinInfo(c.sizeof(BinInfo), 0, 0)
    if shell.SHQueryRecycleBinW(volume_root, c.byref(info)) != 0:
        raise PlatformRefused('the Recycle Bin is unavailable on this volume', 'recycle_bin_unavailable')
    if info.bytes < 0 or info.items < 0:
        raise PlatformRefused('the Recycle Bin returned invalid aggregate counts', 'invalid_bin_response')
    return {'reported_bytes': info.bytes, 'items': info.items}


def _bin_state(api, volume_root):
    """Items the volume's Recycle Bin holds; missing proof still raises."""
    return _bin_details(api, volume_root)['items']


def recycle_bin_info(path):
    """Read aggregate bytes/items only; never list, move or empty Bin contents."""
    result = {'state': 'unknown', 'volume': None, 'reported_bytes': None, 'items': None, 'reason_code': None}
    try:
        name, api = _native_path(path), _win32()
        c, _, kernel, _, _ = api
        volume = c.create_unicode_buffer(32768)
        if not kernel.GetVolumePathNameW(name, volume, len(volume)):
            raise PlatformRefused('cannot identify local volume', 'volume_unknown')
        if kernel.GetDriveTypeW(volume.value) != 3:
            raise PlatformRefused('aggregate reporting requires a local fixed volume', 'unsupported_volume')
        result['volume'] = _native_path(volume.value)
        counts = _bin_details(api, volume.value)
        result.update(state='ok', reason_code='ok', **counts)
    except PlatformRefused as error:
        result['reason_code'] = error.code
    except OSError as error:
        result['reason_code'] = _os_reason(error)
    except (ValueError, TypeError):
        result['reason_code'] = 'invalid_path'
    return result


def recycle(paths, *, max_seconds=None, max_entries=None):
    """Recycle local fixed-volume entries, refusing missing or partial proof."""
    try:
        _validate_usage_bounds(max_seconds, max_entries)
        bounds = {key: value for key, value in (('max_seconds', max_seconds), ('max_entries', max_entries)) if value is not None}
        _recycle(paths, **bounds)
    except OSError as error:
        raise PlatformRefused('native recycling could not be completed safely') from error


def _recycle(paths, *, max_seconds=None, max_entries=None):
    _validate_usage_bounds(max_seconds, max_entries)
    bounds = {key: value for key, value in (('max_seconds', max_seconds), ('max_entries', max_entries)) if value is not None}
    api = _win32()
    c, _, kernel, _, _ = api
    checked = []
    bins = {}

    def key(info):
        return info.volume, info.index_high, info.index_low, bool(info.attributes & 0x10)

    for raw in paths:
        path = _native_path(raw)
        if host_managed(path):
            raise PlatformRefused('host-managed worktrees are held')
        with _pinned_ancestors(api, path):
            with _handle(api, path, share=7) as handle:
                expected = key(_identity(api, handle, path))
            held = usage(path, **bounds)
            if held is None or held:
                raise PlatformRefused('candidate contains busy, unsafe or unreadable entries')
            volume = c.create_unicode_buffer(32768)
            if not kernel.GetVolumePathNameW(path, volume, len(volume)) or kernel.GetDriveTypeW(volume.value) != 3:
                raise PlatformRefused('recycling requires a local fixed volume')
            if volume.value not in bins:
                # Asked before anything moves: a volume whose Bin cannot answer may delete
                # permanently, so it is refused while the folder is still there.
                bins[volume.value] = _bin_state(api, volume.value)
        checked.append((path, expected, volume.value))
    for path, expected, volume_root in checked:
        with _pinned_ancestors(api, path):
            held = usage(path, **bounds)
            if held is None or held:
                raise PlatformRefused('candidate became busy or unreadable before recycling')
            # DELETE sharing keeps the original object alive without preventing
            # Shell from moving it. The callback binds both path and file ID;
            # a replacement at the checked name is never the reviewed item.
            with _handle(api, path, access=0x80010000, share=7) as original:
                def guard(source):
                    if not isinstance(source, str) or ntpath.normcase(source) != ntpath.normcase(path):
                        raise PlatformRefused('Shell source differs from reviewed candidate')
                    if key(_identity(api, original, path)) != expected:
                        raise PlatformRefused('candidate identity changed before recycling')
                    held = _usage(path, original, **bounds)
                    if held is None or held:
                        raise PlatformRefused('candidate changed or became busy before recycling')

                guard(path)
                proof = _shell_recycle(path, guard=guard)
            if (not isinstance(proof, dict) or proof.get('result') != 0 or proof.get('aborted', True)
                    or proof.get('reason') or not proof.get('pre_count')
                    or proof.get('pre_count') != proof.get('post_count')):
                raise PlatformRefused('Shell did not prove complete recycling')
            # The Bin's own count is the proof that does not rest on a callback: a permanent
            # delete leaves it unchanged, so the folder must appear there exactly once.
            recorded = _bin_state(api, volume_root)
            if recorded != bins[volume_root] + 1:
                raise PlatformRefused('the Recycle Bin did not record the recycled item')
            bins[volume_root] = recorded
            try:
                os.lstat(path)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise PlatformRefused('cannot verify source absence after recycling') from error
            raise PlatformRefused('source remains after recycling')
