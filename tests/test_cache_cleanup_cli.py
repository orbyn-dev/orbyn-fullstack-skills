"""Profile-to-CLI contracts with disposable repositories; Trash/native usage are faked."""
import json
import subprocess
import os
from pathlib import Path
from unittest import mock

from test_worktree_retire import CleanupCase, tool, ORIGINAL


class CacheCLIContracts(CleanupCase):
    def sh(self, path, *args):
        try:
            return super().sh(path, *args)
        except subprocess.CalledProcessError as error:
            self.fail(error.stderr)

    def setUp(self):
        super().setUp()
        self.write(self.app/'Cargo.toml', '[package]\nname="fixture"\nversion="0.1.0"\n')
        self.write(self.app/'package.json', '{"name":"fixture"}')
        lock = '{"lockfileVersion":3,"packages":{"node_modules/pkg":{"version":"1.0.0"}}}'
        self.write(self.app/'package-lock.json', lock)
        self.sh(self.app, 'add', 'Cargo.toml', 'package.json', 'package-lock.json')
        self.commit(self.app, 'locked source')
        self.sh(self.app, 'push', '-q', 'origin', 'HEAD:main')
        self.sh(self.app, 'fetch', '-q', 'origin')
        self.work = self.tree('cache-owner')
        self.target = self.work/'target'
        self.unit = self.target/'debug/deps'
        self.write(self.target/'CACHEDIR.TAG', tool.artifact_engine().CACHE_SIGNATURE+'\n')
        self.write(self.target/'.rustc_info.json', json.dumps({'outputs': {'version': {'stdout': 'rustc 1.90.0 (synthetic)\n'}}}))
        self.write(self.unit/'fixture-abc.rlib', 'compiled')
        self.write(self.work/'node_modules/.package-lock.json', lock)
        self.write(self.work/'node_modules/pkg/index.js', 'generated')
        self.write(self.work/'src.txt', 'uncommitted source must remain')
        self.clock = 20
        self.native_usage = tool.artifact_usage
        self.native_directory_probe = tool.native.probe_dirs
        patch = mock.patch.object(tool, 'artifact_usage', return_value=None)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(tool.native, 'probe_dirs', return_value=set())
        patch.start(); self.addCleanup(patch.stop)

    def options(self, **extra):
        return {'cache_profile': 'aggressive', 'only': 'artifact', 'within': str(self.work), **extra}

    def test_dry_run_preserves_all_files_and_writes_no_receipt(self):
        before = self.snapshot()
        report = self.report(**self.options())
        self.assertEqual(self.status(self.unit, report), 'eligible', report)
        result = self.apply(dry_run=True, **self.options())
        self.assertTrue(result['results'])
        self.assertTrue(all(r['outcome'] == 'would trash' for r in result['results']), result)
        self.assertEqual(before, self.snapshot())
        self.assertNotIn('receipt', result)
        self.assertFalse((self.lib/'output'/tool.RECEIPTS).exists())

    def test_cache_apply_preserves_dirty_source_and_registration(self):
        source = (self.work/'src.txt').read_bytes()
        registration = self.sh(self.app, 'worktree', 'list', '--porcelain')
        with mock.patch.object(tool, 'discard', side_effect=AssertionError('cache mode must not retire source')):
            result = self.apply(**self.options())
        self.assertTrue(result['results'], result)
        self.assertTrue(all(r['outcome'] == 'trashed' for r in result['results']), result)
        self.assertFalse(self.unit.exists())
        self.assertFalse((self.work/'node_modules').exists())
        self.assertEqual(source, (self.work/'src.txt').read_bytes())
        self.assertEqual(registration, self.sh(self.app, 'worktree', 'list', '--porcelain'))
        receipt = json.loads(Path(result['receipt']).read_text(encoding='utf-8'))
        self.assertTrue(all(r['outcome'] == 'trashed' for r in receipt['items']))
        self.assertNotIn('cleanup-receipts', [Path(s['path']).name for s in tool.discover(tool.Context(self.lib, 'origin/main'))])

    def test_receipt_failure_prevents_move(self):
        with mock.patch.object(tool, 'write_cleanup_receipt', side_effect=OSError('synthetic full disk')):
            with self.assertRaisesRegex(tool.Refused, 'cannot write cleanup receipt'):
                self.apply(**self.options())
        self.assertFalse(self.trashed)
        self.assertTrue(self.unit.exists())

    def test_pinned_base_restricts_cache_profile(self):
        policy = self.app/'.orbyn/harness.json'
        self.write(policy, json.dumps({'schema_version': 1, 'cleanup': {'cache_profile': 'off'}}))
        self.sh(self.app, 'add', '-f', '.orbyn/harness.json')
        self.commit(self.app, 'disable caches in upstream policy')
        self.sh(self.app, 'push', '-q', 'origin', 'HEAD:main')
        self.sh(self.app, 'fetch', '-q', 'origin')
        report = self.report(**self.options())
        self.assertFalse(any(i['status'] == 'eligible' for i in report['items']), report)
        result = self.apply(dry_run=True, **self.options())
        self.assertFalse(result['results'])

    def test_primary_checkout_profile_cannot_be_bypassed_by_linked_tree(self):
        self.write(self.app/'.orbyn/harness.json', json.dumps({'schema_version': 1, 'cleanup': {'cache_profile': 'off'}}))
        report = self.report(**self.options())
        self.assertFalse(any(i['status'] == 'eligible' for i in report['items']), report)
        result = self.apply(dry_run=True, **self.options())
        self.assertFalse(result['results'])
        self.assertTrue(self.unit.exists())

    def test_primary_safe_profile_cannot_enable_linked_dependency_eviction(self):
        self.write(self.app/'.orbyn/harness.json', json.dumps({'schema_version': 1, 'cleanup': {'cache_profile': 'safe'}}))
        report = self.report(**self.options())
        dependencies = self.item(self.work/'node_modules', report)
        self.assertEqual(dependencies['status'], 'report-only', dependencies)
        self.assertEqual(dependencies['profile'], 'safe')

    def test_owner_policy_and_cli_have_same_effect(self):
        self.write(self.lib/'repo/.harness/_defaults.json', json.dumps({'schema_version': 1, 'cleanup': {'cache_profile': 'aggressive'}}))
        configured = self.report(only='artifact', within=str(self.work))
        explicit = self.report(**self.options())
        project = lambda data: [(r['path'], r['status'], r['reason']) for r in data['items']]
        self.assertEqual(project(configured), project(explicit))

    def test_within_cannot_escape_or_follow_a_link(self):
        with self.assertRaises(tool.Refused):
            self.report(**self.options(within=str(self.base)))
        self.assertTrue(self.unit.exists())

    def test_cancellation_records_every_planned_item_without_source_retirement(self):
        with mock.patch.object(tool, 'act', side_effect=KeyboardInterrupt):
            result = self.apply(**self.options())
        self.assertTrue(result['cancelled'])
        self.assertTrue(all(r['outcome'] == 'cancelled' for r in result['results']), result)
        receipt = json.loads(Path(result['receipt']).read_text(encoding='utf-8'))
        self.assertTrue(all(r['outcome'] == 'cancelled' for r in receipt['items']))
        self.assertFalse(self.trashed)
        self.assertTrue(self.unit.exists())

    def test_stale_unit_identity_is_skipped(self):
        original = tool.collect
        def replaced(ctx, finish=None):
            pairs = original(ctx, finish)
            self.unit.rename(self.target/'debug/saved-deps')
            self.write(self.unit/'new.rlib', 'new data after preview')
            return pairs
        with mock.patch.object(tool, 'collect', side_effect=replaced):
            result = self.apply(**self.options())
        unit = next(r for r in result['results'] if r['path'] == str(self.unit))
        self.assertEqual(unit['outcome'], 'skipped', unit)
        self.assertTrue((self.unit/'new.rlib').exists())
        self.assertTrue((self.target/'debug/saved-deps').exists())

    def test_docker_wrapper_rejects_effects_and_repository_local_executables(self):
        probes = tool.artifact_probes(tool.Context(self.lib, 'origin/main'))
        with mock.patch.object(tool, 'run') as runner:
            with self.assertRaises(tool.Refused):
                probes.run_readonly(['docker', 'volume', 'rm', 'data'], 15)
            runner.assert_not_called()
        with mock.patch.object(tool.shutil, 'which', return_value=str(self.lib/'docker.exe')):
            with self.assertRaises(OSError):
                probes.run_readonly(list(tool.artifact_engine().DOCKER_COMMANDS[0]), 15)

    @__import__('unittest').skipUnless(os.name == 'nt' and os.environ.get('ORBYN_TEST_RECYCLE') == '1',
                                     'opt-in disposable native cache recycling')
    def test_disposable_cache_recycling_preserves_source_and_registration(self):
        registration = self.sh(self.app, 'worktree', 'list', '--porcelain')
        source = (self.work/'src.txt').read_bytes()
        with mock.patch.object(tool, 'trash', ORIGINAL['trash']), mock.patch.object(
                tool, 'artifact_usage', self.native_usage), mock.patch.object(
                tool.native, 'probe_dirs', self.native_directory_probe):
            result = self.apply(**self.options())
        self.assertTrue(result['results'], result)
        self.assertTrue(all(r['outcome'] == 'trashed' for r in result['results']), result)
        self.assertFalse(self.unit.exists())
        self.assertEqual(source, (self.work/'src.txt').read_bytes())
        self.assertEqual(registration, self.sh(self.app, 'worktree', 'list', '--porcelain'))
