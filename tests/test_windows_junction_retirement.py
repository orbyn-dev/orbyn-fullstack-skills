"""Junction objects move; shared targets and their contents do not."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1]/'skills/orbyn-worktree/scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = load('retirement_platform')
junctions = load('retirement_junctions')


def buffer(target, tag=0xA0000003):
    substitute = ('\\??\\' + target).encode('utf-16-le')
    printable = target.encode('utf-16-le')
    names = substitute + b'\0\0' + printable + b'\0\0'
    return struct.pack('<IHHHHHH', tag, len(names) + 8, 0, 0, len(substitute),
                       len(substitute) + 2, len(printable)) + names


class JunctionDataContracts(unittest.TestCase):
    def test_only_local_external_directory_junction_shape_is_admitted(self):
        self.assertEqual(junctions._parse_reparse(buffer('C:\\cache\\café\\node_modules'), native,
                                                'D:\\repo-wt\\done'), 'C:\\cache\\café\\node_modules')

    def test_unknown_tag_volume_mount_unc_relative_internal_and_ancestor_are_held(self):
        cases = [buffer('C:\\shared', 0xA000000C), buffer('Volume{1234}\\'),
                 buffer('UNC\\server\\share'), buffer('relative'),
                 buffer('D:\\repo-wt\\done\\inside'), buffer('D:\\repo-wt')]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(junctions.JunctionRefused):
                junctions._parse_reparse(raw, native, 'D:\\repo-wt\\done')

    def test_malformed_reparse_lengths_and_offsets_are_held(self):
        raw = bytearray(buffer('C:\\shared'))
        for broken in (bytes(raw[:-1]), bytes(raw[:9]), bytes(raw[:8] + b'\xff\xff' + raw[10:])):
            with self.assertRaises(junctions.JunctionRefused):
                junctions._parse_reparse(broken, native, 'D:\\repo-wt\\done')

    def test_approval_is_exact_cache_path_not_an_arbitrary_source_or_git_link(self):
        for path in ('', '..\\target', 'C:\\target', 'src', '.git', 'web\\..\\node_modules'):
            with self.subTest(path=path), self.assertRaises(junctions.JunctionRefused):
                junctions._relative(path)
        self.assertEqual(junctions._relative('web/node_modules/'), 'web\\node_modules')

    def test_mapped_or_unknown_drive_is_refused_before_path_open(self):
        for kind in (0, 1, 2, 4, 5, 6):
            api = (None, None, SimpleNamespace(GetDriveTypeW=lambda path: kind))
            with self.subTest(kind=kind), self.assertRaises(junctions.JunctionRefused):
                junctions._fixed_drive(api, 'Z:\\cache\\node_modules')


@unittest.skipUnless(os.name == 'nt', 'native Windows junction contracts')
class NativeJunctionContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='orbyn-junction-contract-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source, self.archive, self.target = self.root/'worktree café', self.root/'archive', self.root/'shared sơ đồ/node_modules'
        (self.source/'web').mkdir(parents=True)
        self.archive.mkdir()
        (self.archive/'RETIRED.json').write_text('{}', encoding='utf-8')
        (self.source/'web/package.json').write_text('{}', encoding='utf-8')
        self.target.mkdir(parents=True)
        self.canary = self.target/'preserve.txt'
        self.canary.write_bytes(b'synthetic shared fixture')
        (self.source/'SYNTHETIC_TEST_FIXTURE.txt').write_text('disposable', encoding='utf-8')
        self.link = self.source/'web/node_modules'
        self.make_link(self.link, self.target)

    def make_link(self, link, target):
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def plan(self):
        return junctions.discover(self.source, ['web/node_modules'], native)

    def test_preflight_refuses_overlap_absent_empty_and_reparse_without_writes(self):
        plan = self.plan()
        empty, absent, alias = self.root/'empty', self.root/'absent', self.root/'archive-alias'
        empty.mkdir()
        self.make_link(alias, self.archive)
        before = sorted(str(p.relative_to(self.target)) for p in self.target.rglob('*'))
        for destination in (self.target/'new-receipt', self.source/'receipt', empty, absent, alias):
            with self.subTest(destination=destination), self.assertRaises(junctions.JunctionRefused):
                with junctions.preflight(plan, destination, native):
                    self.fail('unsafe destination admitted')
        self.assertEqual(sorted(str(p.relative_to(self.target)) for p in self.target.rglob('*')), before)
        self.assertFalse(absent.exists())
        self.assertEqual(list(empty.iterdir()), [])

    def test_flat_preflight_anchor_blocks_retarget_before_receipt_and_supports_prefix(self):
        plan, api = self.plan(), native._win32()
        outside = self.root/'outside-preflight'
        outside.mkdir()
        raw = buffer(str(outside))
        with junctions.preflight(plan, self.archive, native):
            with self.assertRaises(OSError):
                (self.archive/'RETIRED.json').unlink()
            c, w, kernel, _, _ = api
            with native._handle(api, str(self.archive), access=0x100, share=7) as writer:
                incoming, returned = c.create_string_buffer(raw), w.DWORD()
                self.assertFalse(kernel.DeviceIoControl(writer, 0x000900A4, incoming, len(raw), None, 0,
                                                        c.byref(returned), None))
            (self.archive/'trial-RETIRED.json').write_text('{}', encoding='utf-8')
            with junctions.escrow(plan, self.archive, native, prefix='trial-', record_name='trial-RETIRED.json') as tx:
                self.assertTrue(Path(tx.summary()['journal']).name.startswith('trial-'))
                self.assertTrue(Path(tx.summary()['entries'][0]['archive']).name.startswith('trial-junction-'))
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_plan_is_deterministic_and_never_enumerates_shared_target(self):
        original = os.scandir
        seen = []
        def scan(path):
            seen.append(junctions.path_key(path))
            self.assertFalse(junctions.path_key(path).startswith(junctions.path_key(self.target)))
            return original(path)
        with mock.patch.object(junctions.os, 'scandir', side_effect=scan):
            first, second = self.plan(), self.plan()
        self.assertEqual(first, second)
        self.assertEqual(len(first['entries']), 1)
        self.assertTrue(junctions.probe(str(self.link).lower(), first, native))
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_unapproved_junction_and_target_ancestor_junction_are_held(self):
        with self.assertRaises(junctions.JunctionRefused):
            junctions.discover(self.source, [], native)
        self.link.rmdir()  # disposable fixture entry only, never the target
        alias = self.root/'alias'
        self.make_link(alias, self.target.parent)
        self.make_link(self.link, alias/'node_modules')
        with self.assertRaises(junctions.JunctionRefused):
            self.plan()

    def test_shared_file_readers_writers_remain_usable_and_uncommitted_context_rolls_back(self):
        plan, api = self.plan(), native._win32()
        with native._handle(api, str(self.canary), access=0xC0000000, share=7):
            with junctions.escrow(plan, self.archive, native) as tx:
                self.assertFalse(os.path.lexists(self.link))
                self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')
                self.canary.write_bytes(b'legitimate concurrent shared write')
                self.assertEqual(tx.summary()['state'], 'staged')
            self.assertEqual(tx.summary()['state'], 'rolled-back')
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(self.canary.read_bytes(), b'legitimate concurrent shared write')

    def test_existing_target_directory_delete_handle_refuses_before_staging(self):
        plan, api = self.plan(), native._win32()
        with native._handle(api, str(self.target), access=0x10080, share=7):
            with self.assertRaises(junctions.JunctionRefused):
                with junctions.escrow(plan, self.archive, native):
                    self.fail('directory delete lease was ignored')
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_failure_after_staging_restores_original_entry_without_target_changes(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic refusal'):
            with junctions.escrow(self.plan(), self.archive, native):
                raise RuntimeError('synthetic refusal')
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')
        self.assertTrue((self.archive/'KEEP.txt').is_file())
        events = [json.loads(line)['event'] for line in (self.archive/'junction-escrow.jsonl').read_text().splitlines()]
        self.assertIn('restored', events)

    def test_occupied_rollback_slot_is_preserved_with_junction_in_archive(self):
        with self.assertRaises(junctions.JunctionRefused):
            with junctions.escrow(self.plan(), self.archive, native) as tx:
                self.link.mkdir()
                (self.link/'new-owner.txt').write_text('preserve', encoding='utf-8')
        self.assertEqual((self.link/'new-owner.txt').read_text(), 'preserve')
        self.assertEqual(tx.summary()['state'], 'rollback-incomplete')
        self.assertTrue(os.path.lexists(tx.summary()['entries'][0]['archive']))
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_journal_failure_before_move_leaves_source_entry_unchanged(self):
        with mock.patch.object(junctions._Escrow, '_event', side_effect=OSError('synthetic disk full')):
            with self.assertRaises(junctions.JunctionRefused):
                with junctions.escrow(self.plan(), self.archive, native):
                    self.fail('journal failure was ignored')
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_new_nested_keep_marker_after_preview_refuses_staging(self):
        plan = self.plan()
        (self.source/'web/KeEp.TxT').write_text('synthetic retained evidence', encoding='utf-8')
        with self.assertRaises(junctions.JunctionRefused):
            with junctions.escrow(plan, self.archive, native):
                self.fail('new retention protection ignored')
        self.assertTrue(os.path.lexists(self.link))

    def test_ancestor_keep_marker_after_preview_refuses_probe(self):
        plan = self.plan()
        (self.root/'KEEP.txt').write_text('synthetic parent protection', encoding='utf-8')
        with self.assertRaises(junctions.JunctionRefused):
            junctions.probe(self.link, plan, native)

    def test_empty_directory_reparse_race_never_path_enumerates_shared_target(self):
        race = self.source/'empty'
        race.mkdir()
        api = native._win32()
        with native._handle(api, str(self.link), access=0x10080, share=0) as handle:
            raw = bytes.fromhex(junctions._read_link(native, api, handle, str(self.link), str(self.source))['reparse_hex'])
        original, attempted, installed = native.directory_names, [], []
        def raced(given_api, handle, **kwargs):
            name = junctions._handle_name(given_api, handle)
            self.assertFalse(junctions.path_key(name).startswith(junctions.path_key(self.target)))
            if junctions.path_key(name) == junctions.path_key(race) and not attempted:
                attempted.append(True)
                c, w, kernel, _, _ = given_api
                try:
                    with native._handle(given_api, str(race), access=0x100, share=7) as writer:
                        incoming, returned = c.create_string_buffer(raw), w.DWORD()
                        changed = kernel.DeviceIoControl(writer, 0x000900A4, incoming, len(raw), None, 0,
                                                         c.byref(returned), None)
                        if changed:
                            installed.append(True)
                except OSError:
                    pass  # a host refusing the metadata writer is also safe
            names = original(given_api, handle, **kwargs)
            if junctions.path_key(name) == junctions.path_key(race):
                self.assertNotIn(self.canary.name, names)
            return names
        refused = False
        with mock.patch.object(native, 'directory_names', side_effect=raced), mock.patch.object(
                junctions.os, 'scandir', side_effect=AssertionError('path-based enumeration is forbidden')):
            try:
                self.plan()
            except junctions.JunctionRefused:
                refused = True
                self.assertTrue(installed)
        self.assertTrue(attempted)
        if installed:
            self.assertTrue(refused)
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_metadata_visitor_observes_only_source_and_approved_junction_leaf(self):
        seen = []
        def observe(path, info, linked):
            self.assertFalse(junctions.path_key(path).startswith(junctions.path_key(self.target)))
            seen.append((junctions.path_key(path), linked))
        first = junctions.discover(self.source, ['web/node_modules'], native, on_entry=observe)
        self.assertEqual(first, self.plan())
        self.assertEqual(len(seen), first['visited_entries'])
        self.assertIn((junctions.path_key(self.link), True), seen)

    def test_archive_inside_shared_target_is_refused_before_metadata_writes(self):
        archive = self.target/'retirement'
        archive.mkdir()
        with self.assertRaises(junctions.JunctionRefused):
            with junctions.escrow(self.plan(), archive, native):
                self.fail('shared target was used for escrow writes')
        self.assertEqual(list(archive.iterdir()), [])
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_same_junction_in_a_replaced_source_parent_is_held(self):
        plan = self.plan()
        old = self.source/'old-web'
        (self.source/'web').rename(old)
        (self.source/'web').mkdir()
        (old/'node_modules').rename(self.link)
        with self.assertRaises(junctions.JunctionRefused):
            junctions.probe(self.link, plan, native)
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_retargeted_junction_after_preview_is_held(self):
        plan = self.plan()
        replacement = self.root/'replacement/node_modules'
        replacement.mkdir(parents=True)
        self.link.rmdir()
        self.make_link(self.link, replacement)
        with self.assertRaises(junctions.JunctionRefused):
            junctions.probe(self.link, plan, native)
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_journal_failure_after_move_still_restores_the_original_object(self):
        original = junctions._Escrow._event
        def failing(transaction, event, **data):
            if event == 'staged':
                raise OSError('synthetic disk full after rename')
            return original(transaction, event, **data)
        with mock.patch.object(junctions._Escrow, '_event', new=failing):
            with self.assertRaises(junctions.JunctionRefused):
                with junctions.escrow(self.plan(), self.archive, native):
                    self.fail('post-move journal failure ignored')
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_interrupt_after_rename_before_flag_update_is_reconciled(self):
        original, calls = junctions._rename, []
        def interrupting(*args, **kwargs):
            original(*args, **kwargs)
            calls.append(True)
            if len(calls) == 1:
                raise KeyboardInterrupt
        with mock.patch.object(junctions, '_rename', side_effect=interrupting):
            with self.assertRaises(KeyboardInterrupt):
                with junctions.escrow(self.plan(), self.archive, native):
                    self.fail('interruption ignored')
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    def test_target_directory_rename_can_be_denied_while_file_writes_continue(self):
        with junctions.escrow(self.plan(), self.archive, native):
            with self.assertRaises(OSError):
                self.target.rename(self.target.with_name('renamed'))
            self.canary.write_bytes(b'legitimate concurrent shared write')
        self.assertEqual(self.canary.read_bytes(), b'legitimate concurrent shared write')

    def test_parent_attribute_retarget_and_anchor_delete_attacks_fail_at_both_rename_boundaries(self):
        outside = self.root/'outside'
        outside.mkdir()
        outside_canary = outside/'canary.txt'
        outside_canary.write_text('preserve outside', encoding='utf-8')
        raw = buffer(str(outside))
        original, attacked = junctions._rename, []
        def attack(api, handle, name, *, parent_handle, anchor):
            parent_name = junctions._handle_name(api, parent_handle)
            with self.assertRaises(OSError):
                Path(anchor['path']).unlink()
            c, w, kernel, _, _ = api
            with native._handle(api, parent_name, access=0x100, share=7) as writer:
                incoming, returned = c.create_string_buffer(raw), w.DWORD()
                changed = kernel.DeviceIoControl(writer, 0x000900A4, incoming, len(raw), None, 0,
                                                 c.byref(returned), None)
                self.assertFalse(changed, 'pinned nonempty parent was retargeted')
            attacked.append(junctions.path_key(parent_name))
            return original(api, handle, name, parent_handle=parent_handle, anchor=anchor)
        with mock.patch.object(junctions, '_rename', side_effect=attack):
            with junctions.escrow(self.plan(), self.archive, native):
                pass  # uncommitted context exercises the rollback destination too
        self.assertEqual(set(attacked), {junctions.path_key(self.archive), junctions.path_key(self.source/'web')})
        self.assertTrue(os.path.lexists(self.link))
        self.assertEqual(list(outside.iterdir()), [outside_canary])
        self.assertEqual(outside_canary.read_text(), 'preserve outside')
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')

    @unittest.skipUnless(os.environ.get('ORBYN_TEST_RECYCLE') == '1', 'opt-in disposable Recycle Bin trial')
    def test_real_recycle_after_staging_preserves_shared_target_and_escrow_object(self):
        with junctions.escrow(self.plan(), self.archive, native) as tx:
            native.recycle([self.source], max_seconds=30, max_entries=1000)
            tx.commit()
        self.assertFalse(self.source.exists())
        self.assertEqual(self.canary.read_bytes(), b'synthetic shared fixture')
        retained = Path(tx.summary()['entries'][0]['archive'])
        self.assertTrue(os.path.lexists(retained))
        self.assertEqual(tx.summary()['state'], 'committed')


if __name__ == '__main__':
    unittest.main()
