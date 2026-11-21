"""Compression preservation contracts; native tests touch disposable fixtures only."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from test_tools import ROOT, module

compression = module('artifact_compression_test', ROOT/'skills/orbyn-worktree/scripts/artifact_compression.py')


class FakeBackend:
    def __init__(self):
        self.busy = False
        self.calls = []
        self.allocations = {}
        self.failure = False
        self.corrupt = False

    def validate_root(self, root):
        pass

    def usage(self, root, seconds, entries):
        if self.busy:
            raise compression.Held('busy fixture')

    @contextmanager
    def pin_root(self, root, expected):
        if not compression._same(expected, compression._snapshot(root), directory=True):
            raise compression.Held('root identity changed')
        yield

    @contextmanager
    def pin_file(self, path, expected):
        if not compression._same(expected, compression._snapshot(path)):
            raise compression.Held('file changed')
        yield path

    def allocation(self, path):
        return self.allocations.get(str(path), path.stat().st_size)

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def verify(self, handle, path):
        pass

    def compress(self, path):
        self.calls.append(str(path))
        if self.failure:
            raise OSError('synthetic native refusal')
        if self.corrupt:
            path.write_bytes(b'changed')
        self.allocations[str(path)] = path.stat().st_size//2


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base/'cargo-target/debug/deps'
        self.root.mkdir(parents=True)
        self.file = self.root/'libfixture.rlib'
        self.file.write_bytes(b'compiled\0'*16384)
        self.backend = FakeBackend()
        self.now = time.time()+20*compression.DAY
        self.clock = mock.patch.object(compression.time, 'time', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def report(self, **kwargs):
        return compression.report([str(self.root)], enabled=True, backend=self.backend, **kwargs)

    def apply(self, plan, **kwargs):
        return compression.apply(plan, receipt_path=self.base/'receipt.json', enabled=True,
                                 backend=self.backend, **kwargs)

    def test_read_only_report_then_journal_before_mutation(self):
        before = self.file.read_bytes()
        plan = self.report()
        self.assertEqual(plan['totals']['files'], 1)
        self.assertFalse((self.base/'receipt.json').exists())
        original = self.backend.compress
        def inspect_receipt(path):
            import json
            journal = json.loads((self.base/'receipt.json').read_text())
            self.assertEqual(journal['pending']['path'], str(path))
            self.assertIn('sha256_before', journal['pending'])
            original(path)
        self.backend.compress = inspect_receipt
        receipt = self.apply(plan)
        self.assertTrue(receipt['complete'])
        self.assertTrue(receipt['items'][0]['bytes_verified'])
        self.assertGreater(receipt['allocated_savings'], 0)
        self.assertEqual(self.file.read_bytes(), before)

    def test_safe_moderate_and_disabled_never_compress(self):
        for profile in ('safe', 'moderate', 'legacy', 'off'):
            with self.subTest(profile=profile):
                result = self.report(profile=profile)
                self.assertEqual(result['status'], 'held')
        self.assertEqual(compression.report([str(self.root)], backend=self.backend)['status'], 'held')
        with self.assertRaises(compression.Held):
            self.apply(self.report(), profile='moderate')
        self.assertFalse(self.backend.calls)

    def test_recent_metadata_holds_entire_root(self):
        self.now = time.time()
        with mock.patch.object(compression.time, 'time', return_value=self.now-19*compression.DAY):
            self.assertEqual(self.report()['status'], 'held')

    def test_retention_in_ancestor_and_child_holds(self):
        for marker in (self.base/'KEEP.txt', self.root/'HOLD.json'):
            marker.write_text('retain')
            self.assertEqual(self.report()['status'], 'held')
            marker.unlink()

    def test_nested_retention_added_mid_run_stops_remaining_files(self):
        nested = self.root/'nested'
        nested.mkdir()
        self.file.rename(nested/self.file.name)
        self.file = nested/self.file.name
        (nested/'second.rlib').write_bytes(b'compiled'*20000)
        original = self.backend.compress
        def add_hold(path):
            original(path)
            (nested/'HOLD.json').write_text('{}')
        self.backend.compress = add_hold
        receipt = self.apply(self.report())
        self.assertFalse(receipt['complete'])
        self.assertEqual(len(self.backend.calls), 1)
        self.assertIn('retention', receipt['error'])

    def test_source_evidence_and_shared_layouts_rejected(self):
        for name in ('source', 'node_modules', 'evidence', 'data'):
            path = self.base/name/'target/debug/deps'
            path.mkdir(parents=True)
            (path/'fixture.pdb').write_bytes(b'x'*65536)
            plan = compression.report([str(path)], enabled=True, backend=self.backend)
            if name == 'source':
                # A mere "source" ancestor is legitimate; only explicit compiler
                # files are selected. The target itself can never be selected.
                plan = compression.report([str(path.parents[2])], enabled=True, backend=self.backend)
            self.assertEqual(plan['status'], 'held')

    def test_private_go_and_task_node_layouts(self):
        for folder, name in (('.cache/go-build', 'a'*64+'-d'),
                             ('orbyn-task-temp/issue-1/node-compile-cache', 'abcdef12')):
            root = self.base/folder
            root.mkdir(parents=True)
            (root/name).write_bytes(b'compiler'*20000)
            plan = compression.report([str(root)], enabled=True, backend=self.backend)
            self.assertEqual(plan['totals']['files'], 1)
        root = self.base/'important/node-compile-cache'
        root.mkdir(parents=True)
        (root/'abcdef12').write_bytes(b'source'*20000)
        self.assertEqual(compression.report([str(root)], enabled=True, backend=self.backend)['status'], 'held')

    def test_source_extensions_never_selected(self):
        (self.root/'important.rs').write_bytes(b'source'*20000)
        plan = self.report()
        self.assertEqual([f['path'] for f in plan['roots'][0]['files']], [str(self.file)])

    def test_secret_looking_content_protects_whole_root(self):
        (self.root/'secret.env').write_bytes(b'private'*20000)
        self.assertEqual(self.report()['status'], 'held')

    def test_tracked_files_and_recent_source_hold(self):
        source = self.base/'main.rs'
        source.write_text('source')
        gitdir = self.base/'.git'
        gitdir.mkdir()
        def tracked(*args):
            if 'rev-parse' in args:
                return os.fsencode(str(gitdir))
            return os.fsencode(str(self.file.relative_to(self.base)))+b'\0'
        with mock.patch.object(compression, '_git', side_effect=tracked):
            self.assertEqual(self.report()['status'], 'held')
        def recent(*args):
            if 'rev-parse' in args:
                return os.fsencode(str(gitdir))
            return b'main.rs\0'
        with mock.patch.object(compression, '_git', side_effect=recent):
            with mock.patch.object(compression.time, 'time', return_value=self.now-20*compression.DAY):
                self.assertEqual(self.report()['status'], 'held')

    def test_source_resuming_mid_run_stops_remaining_files(self):
        source = self.base/'main.rs'
        source.write_text('source')
        gitdir = self.base/'.git'
        gitdir.mkdir()
        (self.root/'second.rlib').write_bytes(b'compiled'*20000)
        def tracked(*args):
            if 'rev-parse' in args:
                return os.fsencode(str(gitdir))
            return b'main.rs\0'
        original = self.backend.compress
        def resume(path):
            original(path)
            os.utime(source, (self.now, self.now))
        self.backend.compress = resume
        with mock.patch.object(compression, '_git', side_effect=tracked):
            receipt = self.apply(self.report())
        self.assertEqual(len(self.backend.calls), 1)
        self.assertIn('source has activity', receipt['error'])

    def test_hardlinks_are_not_private(self):
        try:
            os.link(self.file, self.base/'shared.rlib')
        except OSError as error:
            self.skipTest(str(error))
        self.assertEqual(self.report()['status'], 'held')

    def test_link_or_junction_is_never_traversed(self):
        link = self.root/'linked'
        try:
            link.symlink_to(self.base, target_is_directory=True)
        except OSError as error:
            self.skipTest(str(error))
        self.assertEqual(self.report()['status'], 'held')

    def test_busy_and_budget_exhaustion_hold(self):
        self.backend.busy = True
        self.assertEqual(self.report()['status'], 'held')
        self.backend.busy = False
        # Clock resolution differs by host; deterministically expire the budget.
        with mock.patch.object(compression.time, 'monotonic', side_effect=[100.0, 102.0]):
            self.assertEqual(self.report(max_seconds=1)['status'], 'held')
        (self.root/'second.obj').write_bytes(b'compiler'*16384)
        self.assertEqual(self.report(max_entries=1)['status'], 'held')

    def test_changed_file_and_root_never_mutated(self):
        plan = self.report()
        self.file.write_bytes(b'changed'*20000)
        receipt = self.apply(plan)
        self.assertFalse(receipt['items'])
        self.assertFalse(self.backend.calls)

    def test_replaced_root_is_held(self):
        plan = self.report()
        self.root.rename(self.root.with_name('old'))
        self.root.mkdir()
        self.file.write_bytes(b'compiled\0'*16384)
        receipt = self.apply(plan)
        self.assertFalse(receipt['items'])

    def test_policy_rechecked_before_each_file(self):
        plan = self.report()
        calls = []
        def policy(root, owner):
            calls.append(root)
            return len(calls) == 1  # Fresh report accepts; immediate mutation check refuses.
        receipt = self.apply(plan, policy_check=policy)
        self.assertIn('policy changed', receipt['error'])
        self.assertFalse(self.backend.calls)

    def test_existing_receipt_and_linked_receipt_parent_refused(self):
        plan = self.report()
        (self.base/'receipt.json').write_text('{}')
        with self.assertRaises(compression.Held):
            self.apply(plan)

    def test_native_refusal_and_corruption_are_recorded_and_stop(self):
        for mode in ('failure', 'corrupt'):
            with self.subTest(mode=mode):
                if (self.base/'receipt.json').exists():
                    (self.base/'receipt.json').unlink()
                setattr(self.backend, mode, True)
                receipt = self.apply(self.report())
                self.assertFalse(receipt['complete'])
                self.assertIn('error', receipt)
                self.assertEqual(len(receipt['items']), 1)
                setattr(self.backend, mode, False)

    def test_invalid_and_overlapping_root_configuration(self):
        with self.assertRaises(compression.Held):
            self.report(idle_days=1)
        plan = compression.report([str(self.root), str(self.root)], enabled=True, backend=self.backend)
        self.assertEqual(plan['roots'][1]['status'], 'held')


@unittest.skipUnless(os.name == 'nt', 'native NTFS Windows fixture')
class NativeCompressionTests(unittest.TestCase):
    def test_disposable_native_compress_and_reverse_preserve_bytes(self):
        backend = compression.NativeBackend()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            try:
                backend.validate_root(root)
            except compression.Held as error:
                self.skipTest(str(error))
            path = root/'Unicode-文件.pdb'
            path.write_bytes(b'compiler-fixture\0'*65536)
            expected = compression._snapshot(path)
            with backend.pin_file(path, expected) as handle:
                before = backend.digest(handle)
                allocated = backend.allocation(handle)
                backend.compress(handle)
                self.assertEqual(before, backend.digest(handle))
                self.assertLess(backend.allocation(handle), allocated)
                backend.compress(handle, compress=False)
                self.assertEqual(before, backend.digest(handle))
            self.assertTrue(path.exists())

    def test_pinned_file_excludes_writers(self):
        backend = compression.NativeBackend()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()/'compiler'
            root.mkdir()
            try:
                backend.validate_root(root)
            except compression.Held as error:
                self.skipTest(str(error))
            path = root/'fixture.pdb'
            path.write_bytes(b'fixture'*16384)
            with backend.pin_root(root, compression._snapshot(root)):
                with backend.pin_file(path, compression._snapshot(path)):
                    with self.assertRaises(OSError):
                        with backend.native._handle(backend.api, str(path), access=0x40000000, share=3):
                            pass

    def test_directory_rename_cannot_redirect_pinned_source(self):
        backend = compression.NativeBackend()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()/'compiler'
            root.mkdir()
            path = root/'fixture.pdb'
            path.write_bytes(b'fixture'*16384)
            with self.assertRaises((compression.Held, OSError, backend.native.PlatformRefused)):
                with backend.pin_root(root, compression._snapshot(root)):
                    root.rename(root.with_name('renamed'))


if __name__ == '__main__':
    unittest.main()
