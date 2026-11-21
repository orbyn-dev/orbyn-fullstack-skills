"""The usage callback is narrow; recycling remains strict without it."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / 'skills/orbyn-worktree/scripts/retirement_platform.py'
spec = importlib.util.spec_from_file_location('reparse_probe_platform', SOURCE)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


@unittest.skipUnless(os.name == 'nt', 'Windows usage contract')
class ReparseCallbackBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='orbyn-reparse-probe-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.entry = self.root / 'entry'
        self.entry.write_text('synthetic entry', encoding='utf-8')

    def mark_reparse(self, path):
        inode = path.lstat().st_ino
        original = native._reparse_info
        return mock.patch.object(native, '_reparse_info',
                                 side_effect=lambda info: info.st_ino == inode or original(info))

    def test_descendant_requires_exact_positive_verifier_and_stays_a_leaf(self):
        verified = mock.Mock(return_value=True)
        with self.mark_reparse(self.entry):
            self.assertEqual(native.usage_details(self.root)['reason_code'], 'reparse')
            clear = native.usage_details(self.root, reparse_probe=verified)
            self.assertEqual(clear['state'], 'clear')
            self.assertEqual(clear['visited_entries'], 2)
        verified.assert_called_once_with(str(self.entry))

    def test_root_reparse_never_reaches_callback(self):
        verifier = mock.Mock(return_value=True)
        with self.mark_reparse(self.root):
            self.assertEqual(native.usage_details(self.root, reparse_probe=verifier)['reason_code'], 'reparse')
        verifier.assert_not_called()

    def test_false_unknown_and_changed_verifier_remain_held(self):
        with self.mark_reparse(self.entry):
            for value in (False, None, 1, 'allowed'):
                with self.subTest(value=value):
                    self.assertEqual(native.usage_details(self.root, reparse_probe=lambda _: value)['state'], 'unknown')
            def changed(_):
                raise native.PlatformRefused('synthetic changed entry', 'junction_changed')
            self.assertEqual(native.usage_details(self.root, reparse_probe=changed)['reason_code'], 'junction_changed')

    def test_limits_and_default_strict_check_are_not_bypassed(self):
        verifier = mock.Mock(return_value=True)
        with self.mark_reparse(self.entry):
            self.assertEqual(native.usage_details(self.root, reparse_probe=verifier, max_entries=1)['reason_code'], 'budget_entries')
            self.assertEqual(native.usage_details(self.root)['state'], 'unknown')
        verifier.assert_not_called()
        self.assertEqual(native.usage_details(self.root, reparse_probe={})['reason_code'], 'invalid_policy')

    def test_usage_never_reopens_directory_path_for_enumeration(self):
        # A path can be retargeted after its handle identity was checked. Listing
        # the retained directory object avoids enumerating the replacement target.
        with mock.patch.object(native.os, 'scandir', side_effect=AssertionError('path-based enumeration')):
            result = native.usage_details(self.root, max_seconds=30, max_entries=100)
        self.assertEqual(result['state'], 'clear')
        self.assertEqual(result['visited_entries'], 2)

    def test_handle_enumeration_covers_multiple_batches(self):
        for i in range(500):
            (self.root / (f'page-{i:04d}-' + 'n' * 100 + '.txt')).write_text('synthetic', encoding='utf-8')
        result = native.usage_details(self.root, max_seconds=30, max_entries=600)
        self.assertEqual(result['state'], 'clear')
        self.assertEqual(result['visited_entries'], 502)


if __name__ == '__main__':
    unittest.main()
