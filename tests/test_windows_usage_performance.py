"""Windows usage-probe access and sharing contracts on disposable files."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/orbyn-worktree/scripts/retirement_platform.py'
spec = importlib.util.spec_from_file_location('usage_perf_native', SCRIPT)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


@unittest.skipUnless(os.name == 'nt', 'native Windows contracts')
class WindowsProbeAccessContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='orbyn-usage-access-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.file = self.root / 'sample.txt'
        self.file.write_text('synthetic', encoding='utf-8')

    def test_candidate_probes_do_not_request_file_data_read(self):
        real_open = native._open
        accesses = []
        def record(api, path, access, share, creation=3):
            if os.path.normcase(path) in (os.path.normcase(str(self.root)), os.path.normcase(str(self.file))) and share == 0:
                accesses.append(access)
            return real_open(api, path, access, share, creation)
        with mock.patch.object(native, '_open', side_effect=record):
            self.assertEqual(native.usage(self.root, max_seconds=30, max_entries=100), set())
            self.assertEqual(native.probe_dirs([self.root, self.file]), set())
        self.assertGreaterEqual(len(accesses), 4)
        for access in accesses:
            self.assertEqual(access & 0x80000001, 0, 'probe must not request generic or file data read')
            self.assertTrue(access & 0x10000, 'DELETE access retains Windows share conflict detection')
            self.assertTrue(access & 0x80, 'file attributes are needed for identity')

    def test_fully_shared_read_write_and_delete_handles_are_busy(self):
        api = native._win32()
        for desired_access in (0x80000000, 0x40000000, 0x10000):
            with self.subTest(desired_access=desired_access):
                with native._handle(api, str(self.file), desired_access, 7):
                    details = native.usage_details(self.root, max_seconds=30, max_entries=100)
                    self.assertEqual(details['state'], 'busy')
                    self.assertIn(str(self.file), details['busy_paths'])
                    self.assertEqual(native.probe_dirs([self.file]), {str(self.file)})
                self.assertEqual(native.usage(self.root, max_seconds=30, max_entries=100), set())

    def test_process_working_directory_is_busy_and_release_is_clear(self):
        process = subprocess.Popen([sys.executable, '-u', '-c',
                                    'import sys; print("ready",flush=True); sys.stdin.readline()'],
                                   cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertTrue(native.usage(self.root, max_seconds=30, max_entries=100))
            self.assertEqual(native.probe_dirs([self.root]), {str(self.root)})
        finally:
            process.communicate('\n', timeout=15)
        self.assertEqual(native.usage(self.root, max_seconds=30, max_entries=100), set())

    def test_denial_and_exhaustion_remain_unknown(self):
        with mock.patch.object(native, '_open', side_effect=PermissionError('synthetic denial')):
            self.assertEqual(native.usage_details(self.root)['reason_code'], 'access_denied')
            self.assertIsNone(native.probe_dirs([self.root]))
        self.assertEqual(native.usage_details(self.root, max_seconds=0)['reason_code'], 'budget_time')
        self.assertEqual(native.usage_details(self.root, max_entries=1)['reason_code'], 'budget_entries')
        self.assertEqual(native.usage_details(self.root / 'missing')['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
