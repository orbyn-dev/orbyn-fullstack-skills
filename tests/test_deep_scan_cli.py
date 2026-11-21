"""Deep inspection changes bounded effort, preserving all disposal gates."""
import json
from unittest import mock

import test_cache_cleanup_cli as cache_contracts
import test_output_cleanup_cli as output_contracts
from test_worktree_retire import CleanupCase, tool


class DeepScanCLI(CleanupCase):
    options = cache_contracts.CacheCLIContracts.options

    def setUp(self):
        fixture = cache_contracts.CacheCLIContracts()
        fixture.template = self.template
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        for name, value in vars(fixture).items():
            if not name.startswith('_'):
                setattr(self, name, value)

    def test_default_and_deep_keep_the_same_eligibility_and_expose_bounds(self):
        standard = self.report(**self.options())
        deep = self.report(scan_depth='deep', **self.options())
        for data, depth, seconds in ((standard, 'standard', 30), (deep, 'deep', 300)):
            row = self.item(self.unit, data)
            self.assertEqual(row['status'], 'eligible', row)
            self.assertEqual(row['scan_depth']['effective'], depth)
            self.assertEqual(row['scan_depth']['budgets']['usage_seconds'], seconds)
            self.assertIn('per inspection stage', row['scan_depth']['scope'])
        self.assertIn('Scan depth deep', tool.render_report(deep))

    def test_standard_exhaustion_stays_held_but_complete_deep_probe_can_qualify(self):
        def inspect(path, *, max_seconds, max_entries):
            clear = max_seconds == 300
            return {'state': 'clear' if clear else 'unknown',
                    'reason_code': 'clear' if clear else 'budget_time',
                    'elapsed_seconds': 45 if clear else 30, 'visited_entries': 5000,
                    'busy_paths': []}
        with mock.patch.object(tool, 'artifact_usage', self.native_usage), mock.patch.object(
                tool.native, 'is_windows', return_value=True), mock.patch.object(
                tool.native, 'usage_details', side_effect=inspect) as probe:
            standard = self.report(**self.options())
            deep = self.report(scan_depth='deep', **self.options())
        self.assertEqual(self.status(self.unit, standard), 'held')
        self.assertEqual(self.status(self.unit, deep), 'eligible')
        self.assertEqual({c.kwargs['max_entries'] for c in probe.call_args_list}, {400000, 2000000})
        self.assertTrue(self.unit.exists())

    def test_primary_policy_ceiling_applies_to_deep_cli_for_a_linked_tree(self):
        self.write(self.app/'.orbyn/harness.json', json.dumps({'schema_version': 1, 'cleanup': {'scan_depth': 'standard'}}))
        row = self.item(self.unit, self.report(scan_depth='deep', **self.options()))
        self.assertEqual(row['scan_depth']['effective'], 'standard', row)

    def test_pinned_policy_ceiling_survives_current_file_removal(self):
        path = self.app/'.orbyn/harness.json'
        self.write(path, json.dumps({'schema_version': 1, 'cleanup': {'scan_depth': 'standard'}}))
        self.sh(self.app, 'add', '-f', '.orbyn/harness.json')
        self.commit(self.app, 'cap scan cost')
        self.sh(self.app, 'push', '-q', 'origin', 'HEAD:main')
        self.sh(self.app, 'fetch', '-q', 'origin')
        path.unlink()
        row = self.item(self.unit, self.report(scan_depth='deep', **self.options()))
        self.assertEqual(row['scan_depth']['effective'], 'standard', row)
        self.assertIn('pinned-base', row['scan_depth']['source'])

    def test_per_repository_probes_do_not_share_owner_depth(self):
        self.write(self.lib/'repo/.harness/app.json', json.dumps({'schema_version': 1, 'cleanup': {'scan_depth': 'deep'}}))
        ctx = tool.Context(self.lib, 'origin/main')
        deep = tool.artifact_probes(ctx, str(self.app), str(self.work))
        standard = tool.artifact_probes(ctx, str(self.lib), str(self.lib))
        self.assertEqual(deep.scan_depth, 'deep')
        self.assertEqual(standard.scan_depth, 'standard')
        self.assertIsNot(deep, standard)
        self.assertEqual(standard.new_budget().entry_limit, 400000)
        self.assertEqual(deep.new_budget().entry_limit, 2000000)

    def test_fresh_policy_change_skips_planned_deep_move(self):
        original = tool.collect
        def changed(ctx, finish=None):
            pairs = original(ctx, finish)
            self.write(self.app/'.orbyn/harness.json', json.dumps({'schema_version': 1, 'cleanup': {'scan_depth': 'standard'}}))
            return pairs
        with mock.patch.object(tool, 'collect', side_effect=changed):
            result = self.apply(scan_depth='deep', **self.options())
        self.assertTrue(result['results'])
        self.assertTrue(all(r['outcome'] == 'skipped' for r in result['results']), result)
        self.assertFalse(self.trashed)
        self.assertTrue(self.unit.exists())

    def test_deep_apply_passes_finite_bounds_to_final_trash(self):
        with mock.patch.object(tool, 'trash', side_effect=self.fake_trash) as move:
            result = self.apply(scan_depth='deep', **self.options())
        self.assertTrue(all(r['outcome'] == 'trashed' for r in result['results']), result)
        self.assertTrue(move.call_args_list)
        for call in move.call_args_list:
            self.assertEqual(call.kwargs, {'usage_limits': {'max_seconds': 300, 'max_entries': 2000000}})
        self.assertTrue((self.work/'src.txt').exists())

    def test_exact_scope_does_not_resolve_unrelated_tree_profiles(self):
        ctx = tool.Context(self.lib, 'origin/main', cache_profile='aggressive',
                           within=self.unit, only='artifact', scan_depth='deep')
        with mock.patch.object(ctx, 'artifact_profile', wraps=ctx.artifact_profile) as profiles:
            tool.discover(ctx)
        self.assertTrue(profiles.call_args_list)
        self.assertTrue(all(call.args[1] == str(self.work) for call in profiles.call_args_list))

    def test_invalid_depth_fails_before_inspection(self):
        with mock.patch.object(tool, 'verify_workspace') as verify:
            for func in (tool.report, tool.apply):
                with self.assertRaises(tool.Refused):
                    func(self.lib, 'origin/main', scan_depth='unbounded')
            verify.assert_not_called()


class DeepOutputCLI(CleanupCase):
    options = output_contracts.OutputCLIContracts.options

    def setUp(self):
        fixture = output_contracts.OutputCLIContracts()
        fixture.template = self.template
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        for name, value in vars(fixture).items():
            if not name.startswith('_'):
                setattr(self, name, value)

    def test_output_deep_move_still_preserves_source_evidence_and_data(self):
        registration = self.sh(self.app, 'worktree', 'list', '--porcelain')
        with mock.patch.object(tool, 'trash', side_effect=self.fake_trash) as move:
            result = self.apply(scan_depth='deep', **self.options())
        self.assertEqual([r['outcome'] for r in result['results']], ['trashed'], result)
        self.assertEqual(move.call_args.kwargs['usage_limits']['max_seconds'], 300)
        for path in ('evidence.txt', 'source-snapshot/.git/HEAD', 'avd/userdata.img'):
            self.assertTrue((self.owner/path).is_file())
        self.assertEqual(registration, self.sh(self.app, 'worktree', 'list', '--porcelain'))
