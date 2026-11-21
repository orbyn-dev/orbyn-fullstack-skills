"""Capacity goals stay diagnostic across report, dry-run and simulated effects."""
import copy
import io
from unittest import mock

import test_cache_cleanup_cli as cache_contracts
from test_worktree_retire import CleanupCase, tool


class CapacityCLIContracts(CleanupCase):
    options = cache_contracts.CacheCLIContracts.options

    def setUp(self):
        # Composition reuses its complete fixture without inheriting its tests
        # or calling a zero-argument super method on an unrelated test class.
        fixture = cache_contracts.CacheCLIContracts()
        fixture.template = self.template
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        for name, value in vars(fixture).items():
            if not name.startswith('_'):
                setattr(self, name, value)
        self.storage = {'state': 'ok', 'path': str(self.lib), 'drive': self.lib.anchor,
                        'total_bytes': 100*1024**3, 'used_bytes': 80*1024**3, 'free_bytes': 20*1024**3,
                        'measured_at_utc': '2026-11-15T00:00:00+00:00',
                        'recycle_bin': {'state': 'unknown', 'volume': None, 'reported_bytes': None,
                                        'items': None, 'reason_code': 'recycle_bin_unavailable'}}
        patch = mock.patch.object(tool, 'capacity_snapshot', side_effect=lambda _path: copy.deepcopy(self.storage))
        patch.start()
        self.addCleanup(patch.stop)

    def test_report_adds_diagnostic_goal_without_changing_eligibility(self):
        baseline = self.report(**self.options())
        report = self.report(reclaim_gib=10, **self.options())
        project = lambda data: [(item['path'], item['status'], item['reason']) for item in data['items']]
        self.assertEqual(project(baseline), project(report))
        self.assertEqual(self.status(self.unit, report), 'eligible', report)
        capacity = report['capacity']
        self.assertEqual(capacity['snapshot']['free_bytes'], self.storage['free_bytes'])
        self.assertEqual(capacity['snapshot']['recycle_bin']['state'], 'unknown')
        self.assertIsNone(capacity['snapshot']['recycle_bin']['reported_bytes'])
        self.assertEqual(capacity['plan']['goal'], {'requested_gib': 10.0, 'target_bytes': 10*1024**3,
                                                   'status': 'not_guaranteed'})
        self.assertEqual(capacity['plan']['guaranteed_immediate_reclaim_bytes'], 0)
        self.assertFalse(capacity['plan']['logical_is_physical_reclaim'])
        self.assertGreater(capacity['plan']['eligible_logical_estimate_bytes'], 0)
        text = tool.render_report(report)
        self.assertIn('not guaranteed', text)
        self.assertIn('unknown/unavailable; this is not zero storage', text)
        self.assertIn('authorizes no purge or Bin emptying', text)

    def test_dry_run_moves_zero_logical_bytes_and_preserves_every_file(self):
        before = self.snapshot()
        result = self.apply(dry_run=True, reclaim_gib=20, **self.options())
        self.assertTrue(result['results'])
        self.assertTrue(all(row['outcome'] == 'would trash' for row in result['results']))
        self.assertEqual(result['capacity']['moved_logical_estimate_bytes'], 0)
        self.assertEqual(result['capacity']['observed_free_space_change_bytes'], 0)
        self.assertEqual(result['capacity']['goal']['target_bytes'], 20*1024**3)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.trashed)
        self.assertNotIn('receipt', result)

    def test_negative_free_delta_is_independent_of_simulated_trashed_payload(self):
        before, after = copy.deepcopy(self.storage), copy.deepcopy(self.storage)
        after['free_bytes'] -= 4096
        after['used_bytes'] += 4096
        with mock.patch.object(tool, 'capacity_snapshot', side_effect=[before, after]):
            result = self.apply(reclaim_gib=10, **self.options())
        self.assertTrue(all(row['outcome'] == 'trashed' for row in result['results']), result)
        self.assertTrue(self.trashed)
        expected = sum(row['kb']*1024 for row in result['results'] if row['outcome'] == 'trashed')
        self.assertGreater(expected, 0)
        self.assertEqual(result['capacity']['moved_logical_estimate_bytes'], expected)
        self.assertEqual(result['capacity']['observed_free_space_change_bytes'], -4096)
        self.assertTrue(result['capacity']['measurement_not_attribution'])
        self.assertEqual(result['capacity']['guaranteed_immediate_reclaim_bytes'], 0)
        self.assertIn('measurement, not cleanup attribution', tool.render_apply(result))

    def test_invalid_api_goal_is_rejected_before_inspection_or_effects(self):
        for value in (0, -1, float('nan'), float('inf'), True, 1_048_577):
            for operation in (tool.report, tool.apply):
                with self.subTest(value=value, operation=operation.__name__), mock.patch.object(
                        tool, 'verify_workspace') as verify, mock.patch.object(tool, 'collect') as collect, mock.patch.object(
                        tool, 'capacity_snapshot') as capacity, mock.patch.object(tool, 'trash') as trash:
                    with self.assertRaises(ValueError):
                        operation(self.lib, 'origin/main', reclaim_gib=value)
                    for function in (verify, collect, capacity, trash):
                        function.assert_not_called()
        self.assertFalse(self.trashed)

    def test_invalid_cli_goal_exits_before_any_workspace_access(self):
        for command in ('report', 'apply'):
            for value in ('nan', '0', '-1', 'inf', '1048577'):
                with self.subTest(command=command, value=value), mock.patch.object(
                        tool, 'verify_workspace') as verify, mock.patch.object(tool, 'trash') as trash, mock.patch(
                        'sys.stderr', new_callable=io.StringIO) as stderr:
                    with self.assertRaises(SystemExit) as error:
                        tool.main([command, '--workspace', str(self.lib), '--reclaim-gib', value])
                    self.assertEqual(error.exception.code, 2)
                    self.assertIn('positive finite', stderr.getvalue())
                    verify.assert_not_called()
                    trash.assert_not_called()

    def test_usage_busy_and_unknown_diagnostics_reach_report_unchanged(self):
        for state, code in (('busy', 'busy'), ('unknown', 'budget_time'), ('unknown', 'access_denied')):
            details = {'state': state, 'reason_code': code, 'elapsed_seconds': 30.25,
                       'visited_entries': 1234, 'busy_paths': [str(self.unit/'fixture-abc.rlib')] if state == 'busy' else []}
            with self.subTest(code=code), mock.patch.object(tool, 'artifact_usage', self.native_usage), mock.patch.object(
                    tool.native, 'is_windows', return_value=True), mock.patch.object(tool.native, 'usage_details', return_value=details):
                report = self.report(**self.options())
                item = self.item(self.unit, report)
                self.assertEqual(item['status'], 'held', item)
                self.assertEqual(item['usage_inspection'], details)
                self.assertIn(code, item['reason'])
                if state == 'unknown':
                    self.assertIn('1234 entries / 30.25s', item['reason'])
                self.assertGreaterEqual(report['capacity']['plan']['held_by_reason'][item['reason']], 1)

    def test_native_usage_wrapper_preserves_legacy_return_and_structured_details(self):
        for state, code, expected in (('clear', 'clear', None), ('busy', 'busy', 'artifact is busy'),
                                       ('unknown', 'budget_entries', 'cannot establish bounded artifact usage')):
            details = {'state': state, 'reason_code': code, 'elapsed_seconds': 1.5,
                       'visited_entries': 19, 'busy_paths': []}
            collected = {}
            with self.subTest(state=state), mock.patch.object(tool.native, 'is_windows', return_value=True), mock.patch.object(
                    tool.native, 'usage_details', return_value=details) as probe:
                reason = self.native_usage(self.unit, set(), details=collected)
            self.assertEqual(collected, details)
            if expected is None:
                self.assertIsNone(reason)
            else:
                self.assertIn(expected, reason)
            probe.assert_called_once_with(self.unit, max_seconds=30, max_entries=400_000)


if __name__ == '__main__':
    __import__('unittest').main()
