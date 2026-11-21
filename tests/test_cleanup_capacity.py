import datetime
import importlib.util
import math
from pathlib import Path
import unittest

from test_tools import ROOT

spec = importlib.util.spec_from_file_location('cleanup_capacity', ROOT/'skills/orbyn-worktree/scripts/cleanup_capacity.py')
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


class CapacityTests(unittest.TestCase):
    def test_snapshot_success_and_unknown(self):
        stamp = datetime.datetime(2026, 11, 15, tzinfo=datetime.timezone.utc)
        data = capacity.snapshot('.', lambda _: {'state': 'ok', 'volume': 'C:', 'reported_bytes': 8, 'items': 2},
                                 disk_usage=lambda _: (100, 70, 30), clock=lambda: stamp)
        self.assertEqual((data['state'], data['total_bytes'], data['used_bytes'], data['free_bytes']), ('ok', 100, 70, 30))
        self.assertEqual(data['recycle_bin']['reported_bytes'], 8)
        self.assertIn('2026-11-15', data['measured_at_utc'])
        def failed(_):
            raise OSError('unavailable')
        unknown = capacity.snapshot('.', failed, disk_usage=failed, clock=lambda: stamp)
        self.assertEqual(unknown['state'], 'unknown')
        self.assertIsNone(unknown['free_bytes'])
        self.assertEqual(unknown['recycle_bin']['state'], 'unknown')
        self.assertIsNone(unknown['recycle_bin']['reported_bytes'])

    def test_goal_validation(self):
        for value in (True, False, 0, -1, 'nan', math.inf, '1048577', object()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                capacity.validate_goal(value)
        self.assertEqual(capacity.validate_goal('10'), 10.0)
        self.assertEqual(capacity.validate_goal(1048576), 1048576.0)

    def test_estimate_excludes_overlap_and_incomplete(self):
        items = [dict(path='a', status='eligible', kb=10, drive='C:', complete=True),
                 dict(path='a/b', status='eligible', kb=5, drive='C:', complete=True),
                 dict(path='a', status='eligible', kb=10, drive='C:', complete=True),
                 dict(path='other', status='eligible', kb=7, drive='D:', complete=True),
                 dict(path='partial', status='eligible', kb=99, drive='C:', complete=False),
                 dict(path='held', status='held', reason='busy', kb=8, drive='C:')]
        result = capacity.plan(items, {'drive': 'C:'}, 10)
        self.assertEqual(result['eligible_logical_estimate_bytes'], 17*1024)
        self.assertEqual(result['overlap_count'], 2)
        self.assertEqual(result['held_by_reason']['busy'], 1)
        self.assertEqual(result['size_coverage']['incomplete'], 1)
        self.assertEqual(result['drive_breakdown']['d:']['eligible_logical_estimate_bytes'], 7*1024)
        self.assertEqual(result['guaranteed_immediate_reclaim_bytes'], 0)
        self.assertEqual(result['goal']['status'], 'not_guaranteed')

    def test_observation_is_not_attribution_and_dry_run_moves_zero(self):
        before = {'free_bytes': 100, 'drive': 'C:'}
        for free in (90, 100, 110):
            result = capacity.change(before, {'free_bytes': free, 'drive': 'C:'},
                                     [{'outcome': 'would trash', 'kb': 20}], 1)
            self.assertEqual(result['observed_free_space_change_bytes'], free - 100)
            self.assertTrue(result['measurement_not_attribution'])
            self.assertEqual(result['moved_logical_estimate_bytes'], 0)
            self.assertEqual(result['goal']['status'], 'not_guaranteed')
        self.assertIsNone(capacity.change(before, {'free_bytes': None}, [])['observed_free_space_change_bytes'])
        self.assertEqual(capacity.change(before, before, [{'outcome': 'trashed', 'kb': 5, 'path': 'moved'}])['moved_logical_estimate_bytes'], 5120)

    def test_bad_metrics_remain_unknown_and_logical_sizes_are_not_physical(self):
        data = capacity.snapshot('.', lambda _: {'state': 'ok', 'volume': 'C:', 'reported_bytes': -1, 'items': 3},
                                 disk_usage=lambda _: (-1, 0, 0))
        self.assertEqual(data['state'], 'unknown')
        self.assertIsNone(data['recycle_bin']['reported_bytes'])
        items = [{'path': 'first', 'status': 'eligible', 'kb': 3, 'drive': 'C:'},
                 {'path': 'second', 'status': 'eligible', 'kb': 3, 'drive': 'C:'},
                 {'path': 'missing', 'status': 'eligible', 'drive': 'C:'}]
        result = capacity.plan(items, {'drive': 'C:'})
        self.assertEqual(result['eligible_logical_estimate_bytes'], 6144)
        self.assertFalse(result['logical_is_physical_reclaim'])
        self.assertEqual(result['size_coverage']['unmeasured'], 1)
        self.assertIsNone(capacity.change({'free_bytes': 4, 'drive': 'C:'},
                                          {'free_bytes': 9, 'drive': 'D:'}, [])['observed_free_space_change_bytes'])

    def test_drive_normalization_and_separate_volume_totals(self):
        for raw in ('D:', 'd:\\', 'D:/', 'd:/folder'):
            self.assertEqual(capacity.normalized_drive(raw), 'd:')
        self.assertEqual(capacity.normalized_drive('/'), '/')
        self.assertIsNone(capacity.normalized_drive([]))
        data = capacity.plan([{'path': 'one', 'status': 'eligible', 'kb': 4, 'drive': 'D:\\'},
                              {'path': 'two', 'status': 'eligible', 'kb': 8, 'drive': 'E:'}],
                             {'drive': 'd:'}, 10)
        self.assertEqual(data['snapshot_drive_eligible_logical_estimate_bytes'], 4096)
        self.assertEqual(data['other_volume_eligible_logical_estimate_bytes'], 8192)
        self.assertEqual(data['goal']['status'], 'not_guaranteed')
        self.assertEqual(capacity.change({'drive': 'D:', 'free_bytes': 1},
                                         {'drive': 'd:\\', 'free_bytes': 3}, [])['observed_free_space_change_bytes'], 2)

    def test_invalid_recycle_callbacks_and_disk_consistency(self):
        for callback in (lambda _: [], lambda _: {'state': [], 'volume': []},
                         lambda _: {'state': 'ok', 'volume': [], 'reported_bytes': 1, 'items': 1}):
            self.assertEqual(capacity.snapshot('.', callback, disk_usage=lambda _: (10, 7, 4))['state'], 'unknown')
            self.assertEqual(capacity.snapshot('.', callback)['recycle_bin']['state'], 'unknown')
        response = capacity.snapshot('.', lambda _: {'state': 'unknown', 'reason_code': 'access_denied'})
        self.assertEqual(response['recycle_bin']['reason_code'], 'access_denied')
        response = capacity.snapshot('.', lambda _: {'state': 'unknown', 'reason_code': 'secret / path'})
        self.assertIsNone(response['recycle_bin']['reason_code'])

    def test_windows_overlap_is_case_insensitive_on_any_host(self):
        items = [{'path': 'D:\\Cache', 'status': 'eligible', 'kb': 5, 'drive': 'D:'},
                 {'path': 'd:\\cache\\child', 'status': 'eligible', 'kb': 2, 'drive': 'D:'}]
        result = capacity.plan(items, {'drive': 'd:'})
        self.assertEqual(result['eligible_logical_estimate_bytes'], 5120)
        self.assertEqual(result['overlap_count'], 1)
