"""Portable WinAPI counter/negative controls for read-only native probe contracts."""
from collections import Counter
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_tools import ROOT, module

native = module('retirement_platform_probe_tests', ROOT/'skills/orbyn-worktree/scripts/retirement_platform.py')
RAW_STAT = os.lstat
RAW_SCANDIR = os.scandir


class FileInfo(ctypes.Structure):
    _fields_ = [('attributes', ctypes.c_uint32), ('index_high', ctypes.c_uint32), ('index_low', ctypes.c_uint32)]


class FakeKernel:
    def __init__(self):
        self.counts, self.handles, self.opens = Counter(), {}, []
        self.next = 1
        self.busy = self.denied = self.bad_id = self.bad_path = self.reparse = self.bad_type = None

    def CreateFileW(self, path, access, share, *_args):
        self.counts['CreateFileW'] += 1
        self.opens.append((path, access, share))
        if path == self.denied:
            raise PermissionError('credential-bearing error must not be reported')
        if path == self.busy and access & 0x10000 and share == 0:
            error = OSError('sharing violation')
            error.winerror = 32
            raise error
        handle = self.next
        self.next += 1
        self.handles[handle] = path
        return handle

    def CloseHandle(self, handle):
        self.counts['CloseHandle'] += 1
        del self.handles[handle]
        return 1

    def GetFileInformationByHandle(self, handle, output):
        self.counts['GetFileInformationByHandle'] += 1
        path = self.handles[handle]
        observed, info = RAW_STAT(path), output._obj
        info.attributes = 0x10 if stat.S_ISDIR(observed.st_mode) else 0
        if path == self.reparse:
            info.attributes |= 0x400
        if path == self.bad_type:
            info.attributes ^= 0x10
        index = observed.st_ino ^ (1 if path == self.bad_id else 0)
        info.index_high, info.index_low = index >> 32, index & 0xffffffff
        return 1

    def GetFinalPathNameByHandleW(self, handle, buffer, _size, _flags):
        self.counts['GetFinalPathNameByHandleW'] += 1
        path = self.handles[handle]
        buffer.value = 'mismatched' if path == self.bad_path else '\\\\?\\' + path
        return len(buffer.value)


class ProbeContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.leaf = self.root/'first.txt'
        self.leaf.write_bytes(b'fixture')
        (self.root/'nested').mkdir()
        self.last = self.root/'nested/last.txt'
        self.last.write_bytes(b'fixture')
        self.kernel = FakeKernel()
        self.api = (ctypes, None, self.kernel, FileInfo, None)
        self.stack = mock.patch.object(native, '_native_path', side_effect=lambda path: str(path))
        self.stack.start()
        self.addCleanup(self.stack.stop)
        self.win = mock.patch.object(native, '_win32', return_value=self.api)
        self.win.start()
        self.addCleanup(self.win.stop)
        # This portable fixture models the handle-listing boundary; native tests
        # separately exercise FileFullDirectoryInfo and forbid path enumeration.
        self.reader = mock.patch.object(native, 'directory_names', side_effect=self.names)
        self.reader.start()
        self.addCleanup(self.reader.stop)

    def names(self, _api, handle, *, deadline=None, limit=None, admitted=None):
        self.kernel.counts['GetFileInformationByHandleEx'] += 1
        with RAW_SCANDIR(self.kernel.handles[handle]) as entries:
            names = sorted(entry.name for entry in entries)
        found = []
        for name in names:
            if deadline is not None and native.time.monotonic() >= deadline:
                raise native.PlatformRefused('synthetic time bound', 'budget_time')
            if limit is not None and len(found) >= limit:
                raise native.PlatformRefused('synthetic entry bound', 'budget_entries')
            if admitted is not None:
                admitted()
            found.append(name)
        return found

    def details(self, **bounds):
        result = native.usage_details(str(self.root), **bounds)
        self.assertEqual(self.kernel.handles, {})
        self.assertGreaterEqual(result['elapsed_seconds'], 0)
        return result

    def test_exact_handle_identity_counts_and_reduced_metadata_reads(self):
        counts = Counter()
        def lstat(path, *args, **kwargs):
            counts['lstat'] += 1
            return RAW_STAT(path, *args, **kwargs)
        with mock.patch.object(native.os, 'lstat', side_effect=lstat):
            result = self.details(max_seconds=30, max_entries=100)
        self.assertEqual((result['state'], result['reason_code'], result['visited_entries']), ('clear', 'clear', 4))
        ancestors, files, directories = len(self.root.parents), 2, 2
        self.assertEqual(counts['lstat'], 2*files + 6*directories + ancestors)
        self.assertLess(counts['lstat'], 4*files + 10*directories + 2*ancestors)
        self.assertEqual(self.kernel.counts['CreateFileW'], ancestors + files + 2*directories)
        self.assertEqual(self.kernel.counts['CloseHandle'], self.kernel.counts['CreateFileW'])
        for key in ('GetFileInformationByHandle', 'GetFinalPathNameByHandleW'):
            self.assertEqual(self.kernel.counts[key], ancestors + files + 3*directories)
        exclusive = [(path, access, share) for path, access, share in self.kernel.opens if share == 0]
        self.assertEqual(len(exclusive), files+directories)
        self.assertTrue(all(access == 0x10080 for _, access, _ in exclusive))

    def test_busy_leaf_is_negative_proof_and_legacy_set_is_preserved(self):
        self.kernel.busy = str(self.last)
        details = self.details(max_seconds=30, max_entries=100)
        self.assertEqual(details['state'], 'busy')
        self.assertEqual(details['busy_paths'], [str(self.last)])
        self.assertGreater(details['visited_entries'], 1)
        self.assertEqual(native.usage(self.root, max_seconds=30, max_entries=100), {str(self.last)})

    def test_budget_failures_are_distinct_and_never_clear(self):
        for bounds, code, visited in (({'max_seconds': 0}, 'budget_time', 0),
                                      ({'max_entries': 1}, 'budget_entries', 1)):
            with self.subTest(bounds=bounds):
                details = self.details(**bounds)
                self.assertEqual((details['state'], details['reason_code']), ('unknown', code))
                self.assertEqual(details['visited_entries'], visited)
                self.assertIsNone(native.usage(self.root, **bounds))
        clock = [0]
        def advance():
            clock[0] += 0.4
            return clock[0]
        with mock.patch.object(native.time, 'monotonic', side_effect=advance):
            details = self.details(max_seconds=1, max_entries=100)
        self.assertEqual(details['reason_code'], 'budget_time')
        self.assertGreater(details['visited_entries'], 0)

    def test_bad_bounds_never_disable_a_limit(self):
        for bounds in ({'max_seconds': float('nan')}, {'max_seconds': -1}, {'max_seconds': '30'},
                       {'max_entries': -1}, {'max_entries': True}):
            with self.subTest(bounds=bounds):
                self.assertEqual(self.details(**bounds)['reason_code'], 'invalid_budget')

    def test_access_denial_and_other_os_failure_are_sanitized(self):
        self.kernel.denied = str(self.last)
        details = self.details(max_seconds=30, max_entries=100)
        self.assertEqual((details['state'], details['reason_code']), ('unknown', 'access_denied'))
        self.assertNotIn('credential', json.dumps(details))
        self.kernel.denied = None
        with mock.patch.object(native, 'directory_names', side_effect=OSError('private URL')):
            details = self.details()
        self.assertEqual(details['reason_code'], 'os_error')
        self.assertNotIn('private URL', json.dumps(details))
        self.assertEqual(native.usage_details(self.root/'absent')['reason_code'], 'missing')

    def test_identity_type_handle_reparse_and_final_path_failures_are_distinct(self):
        for field, code in (('bad_id', 'identity_changed'), ('bad_type', 'type_changed'),
                            ('reparse', 'reparse'), ('bad_path', 'final_path')):
            with self.subTest(field=field):
                setattr(self.kernel, field, str(self.last))
                details = self.details(max_seconds=30, max_entries=100)
                self.assertEqual((details['state'], details['reason_code']), ('unknown', code))
                setattr(self.kernel, field, None)

    def test_observed_reparse_and_directory_mutation_are_unknown(self):
        def lstat(path, *args, **kwargs):
            info = RAW_STAT(path, *args, **kwargs)
            return SimpleNamespace(st_mode=info.st_mode, st_ino=info.st_ino, st_mtime_ns=info.st_mtime_ns,
                                   st_file_attributes=0x400) if str(path) == str(self.last) else info
        with mock.patch.object(native.os, 'lstat', side_effect=lstat):
            self.assertEqual(self.details()['reason_code'], 'reparse')
        calls = Counter()
        def changed(api, handle, **options):
            path = self.kernel.handles[handle]
            calls[str(path)] += 1
            found = self.names(api, handle, **options)
            if str(path) == str(self.root) and calls[str(path)] == 2:
                found.append('appeared.txt')
            return found
        with mock.patch.object(native, 'directory_names', side_effect=changed):
            details = self.details()
        self.assertEqual((details['state'], details['reason_code']), ('unknown', 'candidate_changed'))

    def test_retained_root_keeps_identity_checks_without_exclusive_self_probe(self):
        with native._handle(self.api, str(self.root), 0x80010000, 7) as retained:
            answer = native._usage(str(self.root), retained, max_seconds=30, max_entries=100)
        self.assertEqual(answer, set())
        self.assertFalse(any(path == str(self.root) and share == 0 for path, _, share in self.kernel.opens))
        self.assertEqual(self.kernel.handles, {})

    def test_missing_native_api_stays_unknown_without_error_text(self):
        with mock.patch.object(native, '_win32', side_effect=native.PlatformRefused('private path', 'api_unavailable')):
            details = self.details()
        self.assertEqual(details['reason_code'], 'api_unavailable')
        self.assertNotIn('private path', json.dumps(details))


class RecycleBinContracts(unittest.TestCase):
    def query(self, result=0, reported_bytes=1234, items=2):
        def fill(_volume, pointer):
            pointer._obj.bytes, pointer._obj.items = reported_bytes, items
            return result
        shell = SimpleNamespace(SHQueryRecycleBinW=mock.Mock(side_effect=fill))
        return shell

    def test_strict_aggregate_query_and_count_contract(self):
        shell = self.query()
        with mock.patch.object(ctypes, 'WinDLL', create=True, return_value=shell) as loader:
            self.assertEqual(native._bin_details((ctypes,), 'C:\\'), {'reported_bytes': 1234, 'items': 2})
            self.assertEqual(native._bin_state((ctypes,), 'C:\\'), 2)
        loader.assert_called_with('shell32.dll', winmode=0x800)

    def test_non_success_hresult_and_negative_totals_raise_strictly(self):
        for shell in (self.query(result=-1), self.query(result=1), self.query(reported_bytes=-1), self.query(items=-1)):
            with mock.patch.object(ctypes, 'WinDLL', create=True, return_value=shell):
                with self.assertRaises(native.PlatformRefused):
                    native._bin_state((ctypes,), 'C:\\')

    def test_public_query_never_reports_unavailable_as_zero(self):
        def volume(_path, buffer, _size):
            buffer.value = 'C:\\'
            return 1
        kernel = SimpleNamespace(GetVolumePathNameW=volume, GetDriveTypeW=lambda _path: 3)
        api = (ctypes, None, kernel, None, None)
        with mock.patch.object(native, '_win32', return_value=api), mock.patch.object(
                native, '_bin_details', return_value={'reported_bytes': 1234, 'items': 2}):
            answer = native.recycle_bin_info('C:\\candidate')
        self.assertEqual(answer, {'state': 'ok', 'volume': 'C:\\', 'reported_bytes': 1234, 'items': 2, 'reason_code': 'ok'})
        with mock.patch.object(native, '_win32', return_value=api), mock.patch.object(
                native, '_bin_details', side_effect=native.PlatformRefused('unavailable', 'recycle_bin_unavailable')):
            answer = native.recycle_bin_info('C:\\candidate')
        self.assertEqual(answer['state'], 'unknown')
        self.assertIsNone(answer['reported_bytes'])
        self.assertIsNone(answer['items'])
        kernel.GetDriveTypeW = lambda _path: 4
        with mock.patch.object(native, '_win32', return_value=api), mock.patch.object(native, '_bin_details') as query:
            answer = native.recycle_bin_info('C:\\candidate')
            query.assert_not_called()
        self.assertEqual(answer['reason_code'], 'unsupported_volume')
        self.assertEqual(native.recycle_bin_info('\\\\server\\share\\candidate')['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
