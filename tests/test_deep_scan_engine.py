"""Inspection depth changes resources; it never replaces proof or native guards."""
from contextlib import contextmanager, nullcontext
import ctypes
import os
from types import SimpleNamespace
import unittest
from unittest import mock

import test_artifact_cache as cache_contracts
import test_output_cleanup as output_contracts
import test_retirement_platform as native_contracts

cache, native = cache_contracts.cache, native_contracts.native


class DeepEngineContracts(unittest.TestCase):
    def fixture(self, cls):
        fixture = cls()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    @staticmethod
    def clock(step=20):
        values = [0]
        def advance():
            values[0] += step
            return values[0]
        return advance

    def probes(self, fixture, depth):
        return cache.Probes(fixture.git, fixture.usage, lambda _paths: False, fixture.now, None,
                            fixture.probes.helpers, str(fixture.workspace), scan_depth=depth)

    def test_presets_are_explicit_validated_and_independent(self):
        self.assertEqual(cache.scan_limits('standard'), {'entry_limit': 400_000, 'seconds_limit': 60, 'usage_seconds': 30})
        self.assertEqual(cache.scan_limits('deep'), {'entry_limit': 2_000_000, 'seconds_limit': 300, 'usage_seconds': 300})
        for invalid in ('unlimited', '', None, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cache.scan_limits(invalid)
        fixture = self.fixture(cache_contracts.ArtifactTests)
        standard, deep = self.probes(fixture, 'standard'), self.probes(fixture, 'deep')
        deep.limits['entry_limit'] = 123
        deep.discovery['private'] = True
        self.assertEqual(standard.new_budget().entry_limit, 400_000)
        self.assertEqual(cache.scan_limits('deep')['entry_limit'], 2_000_000)
        self.assertFalse(standard.discovery)
        self.assertFalse(standard.new_budget().scans)

    def test_scan_discovery_and_source_activity_obey_chosen_time_limits(self):
        fixture = self.fixture(cache_contracts.ArtifactTests)
        outcomes = {}
        for depth in ('standard', 'deep'):
            probes = self.probes(fixture, depth)
            with mock.patch.object(cache.time, 'monotonic', side_effect=self.clock()):
                scan = cache._scan(fixture.root/'target/debug/deps', probes, probes.new_budget())
            with mock.patch.object(cache.time, 'monotonic', side_effect=self.clock()):
                discovered = cache._cargo_specs(fixture.root, fixture.gitdir, fixture.root/'target', fixture.root, fixture.names, probes)
            spec = {**fixture.spec(), 'tracked_names': fixture.names}
            with mock.patch.object(cache.time, 'monotonic', side_effect=self.clock()):
                activity = cache._source_activity(spec, probes)
            outcomes[depth] = scan['complete'], any(s.get('discovery_error') for s in discovered), activity
        self.assertFalse(outcomes['standard'][0])
        self.assertTrue(outcomes['standard'][1])
        self.assertIn('budget', outcomes['standard'][2])
        self.assertEqual(outcomes['deep'], (True, False, None))

    def test_fresh_artifact_recheck_uses_selected_budget_and_same_proof(self):
        fixture = self.fixture(cache_contracts.ArtifactTests)
        probes = self.probes(fixture, 'deep')
        spec = fixture.spec()
        budgets = []
        original = cache._scan
        def scan(path, ap, budget):
            budgets.append((budget.entry_limit, budget.seconds_limit))
            return original(path, ap, budget)
        with mock.patch.object(cache, '_scan', side_effect=scan), mock.patch.object(cache, '_proof', wraps=cache._proof) as proof:
            planned = cache.evaluate(spec, 'aggressive', probes)
            checked = cache.recheck(spec, planned, 'aggressive', probes)
        self.assertEqual((planned['status'], checked['status']), ('eligible', 'eligible'))
        self.assertEqual(budgets, [(2_000_000, 300), (2_000_000, 300)])
        self.assertEqual(proof.call_count, 2)
        probes.usage = lambda _path: 'busy consumer'
        self.assertEqual(cache.recheck(spec, planned, 'aggressive', probes)['status'], 'skipped')

    def test_output_recheck_uses_its_producers_selected_budget(self):
        fixture = self.fixture(output_contracts.OutputTests)
        engine = output_contracts.engine
        source = fixture.probes.artifact_probes
        selected = fixture.probes.artifact.Probes(source.git, source.usage, source.dirs_busy, source.now,
                   source.run_readonly, source.helpers, source.workspace, scan_depth='deep')
        fixture.probes.artifact_probes = selected
        budgets = []
        original = fixture.probes.artifact._scan
        def scan(path, probes, budget):
            budgets.append((budget.entry_limit, budget.seconds_limit))
            return original(path, probes, budget)
        spec = fixture.spec()
        with mock.patch.object(fixture.probes.artifact, '_scan', side_effect=scan):
            planned = engine.evaluate(spec, fixture.probes)
            checked = engine.recheck(spec, planned, fixture.probes)
        self.assertEqual((planned['status'], checked['status']), ('eligible', 'eligible'))
        self.assertEqual(budgets, [(2_000_000, 300), (2_000_000, 300)])
        self.assertEqual(planned['source_proof'], checked['source_proof'])

    def test_historical_source_proof_time_budget_preserves_all_byte_comparisons(self):
        fixture = self.fixture(output_contracts.OutputTests)
        spec = fixture.spec()
        ap = fixture.probes.artifact_probes
        for depth in ('standard', 'deep'):
            ap.limits = fixture.probes.artifact.scan_limits(depth)
            with mock.patch.object(output_contracts.engine.time, 'monotonic', side_effect=self.clock(step=5)):
                if depth == 'standard':
                    with self.assertRaisesRegex(ValueError, 'source proof inspection budget'):
                        output_contracts.engine._source_proof(spec, fixture.data['completion'], fixture.probes)
                else:
                    _, hashes, _ = output_contracts.engine._source_proof(spec, fixture.data['completion'], fixture.probes)
                    self.assertEqual(set(hashes), {'Cargo.toml', 'Cargo.lock'})

    def test_native_standard_budget_unknown_deep_complete_and_guard_parity(self):
        fixture = self.fixture(native_contracts.ProbeContracts)
        states = []
        counts = []
        for depth in ('standard', 'deep'):
            limits = cache.scan_limits(depth)
            fixture.kernel.counts.clear()
            with mock.patch.object(native.time, 'monotonic', side_effect=self.clock(step=5)):
                details = native.usage_details(fixture.root, max_seconds=limits['usage_seconds'], max_entries=limits['entry_limit'])
            states.append((details['state'], details['reason_code']))
            fixture.kernel.counts.clear()
            clear = native.usage_details(fixture.root, max_seconds=limits['usage_seconds'], max_entries=limits['entry_limit'])
            self.assertEqual(clear['state'], 'clear')
            counts.append(dict(fixture.kernel.counts))
        self.assertEqual(states, [('unknown', 'budget_time'), ('clear', 'clear')])
        self.assertEqual(counts[0], counts[1])
        fixture.kernel.reparse = str(fixture.last)
        self.assertEqual(native.usage_details(fixture.root, max_seconds=300, max_entries=2_000_000)['reason_code'], 'reparse')
        fixture.kernel.reparse = None
        fixture.kernel.busy = str(fixture.last)
        self.assertEqual(native.usage_details(fixture.root, max_seconds=300, max_entries=2_000_000)['state'], 'busy')
        self.assertFalse(fixture.kernel.handles)

    def test_pending_directory_entries_share_one_cap_and_after_listing_does_not_recount(self):
        fixture = self.fixture(native_contracts.ProbeContracts)
        def directories_first(_api, handle, *, deadline=None, limit=None, admitted=None):
            # Model the retained-handle reader, admitting directory entries first
            # so the nested pending child competes with its root sibling.
            with native_contracts.RAW_SCANDIR(fixture.kernel.handles[handle]) as entries:
                found = sorted(entries, key=lambda entry: not entry.is_dir(follow_symlinks=False))
            names = []
            for entry in found:
                if limit is not None and len(names) >= limit:
                    raise native.PlatformRefused('synthetic entry bound', 'budget_entries')
                if admitted is not None:
                    admitted()
                names.append(entry.name)
            return names
        with mock.patch.object(native, 'directory_names', side_effect=directories_first):
            partial = native.usage_details(fixture.root, max_seconds=30, max_entries=3)
            complete = native.usage_details(fixture.root, max_seconds=300, max_entries=4)
        self.assertEqual((partial['state'], partial['reason_code'], partial['visited_entries']), ('unknown', 'budget_entries', 2))
        self.assertEqual((complete['state'], complete['visited_entries']), ('clear', 4))
        self.assertFalse(fixture.kernel.handles)


class RecycleBudgetContracts(unittest.TestCase):
    def run_recycle(self, bounds, unknown_guard=False):
        path = 'C:\\synthetic-cache'
        def volume(_path, buffer, _size):
            buffer.value = 'C:\\'
            return 1
        api = (ctypes, None, SimpleNamespace(GetVolumePathNameW=volume, GetDriveTypeW=lambda _path: 3), None, None)
        info = SimpleNamespace(volume=1, index_high=0, index_low=1, attributes=0x10)
        def shell(given, guard):
            guard(given)
            guard(given)
            return {'result': 0, 'aborted': False, 'reason': None, 'pre_count': 2, 'post_count': 2}
        with mock.patch.object(native, '_win32', return_value=api), mock.patch.object(
                native, '_pinned_ancestors', side_effect=lambda *_args: nullcontext()), mock.patch.object(
                native, '_handle', side_effect=lambda *_args, **_kwargs: nullcontext('retained-root')), mock.patch.object(
                native, '_identity', return_value=info), mock.patch.object(native, 'usage', return_value=set()) as usage, mock.patch.object(
                native, '_usage', side_effect=[set(), None] if unknown_guard else None, return_value=set()) as retained, mock.patch.object(
                native, '_bin_state', side_effect=[4, 5]), mock.patch.object(native, '_shell_recycle', side_effect=shell) as recycle, mock.patch.object(
                native.os, 'lstat', side_effect=FileNotFoundError):
            if unknown_guard:
                with self.assertRaises(native.PlatformRefused):
                    native.recycle([path], **bounds)
            else:
                native.recycle([path], **bounds)
        return path, usage, retained, recycle

    def test_every_recycle_and_retained_callback_receives_same_finite_bounds(self):
        bounds = {'max_seconds': 300, 'max_entries': 2_000_000}
        path, usage, retained, _ = self.run_recycle(bounds)
        self.assertEqual(usage.call_args_list, [mock.call(path, **bounds), mock.call(path, **bounds)])
        self.assertEqual(retained.call_args_list, [mock.call(path, 'retained-root', **bounds)]*3)

    def test_legacy_omitted_bounds_preserve_call_shapes_and_late_unknown_vetoes(self):
        path, usage, retained, _ = self.run_recycle({})
        self.assertEqual(usage.call_args_list, [mock.call(path), mock.call(path)])
        self.assertEqual(retained.call_args_list, [mock.call(path, 'retained-root')]*3)
        _, _, retained, _ = self.run_recycle({'max_seconds': 300, 'max_entries': 2_000_000}, unknown_guard=True)
        self.assertEqual(retained.call_count, 2)

    def test_invalid_recycle_budget_refuses_before_any_native_effect(self):
        for function in (native.recycle, native._recycle):
            with self.subTest(function=function.__name__), mock.patch.object(native, '_win32') as api, mock.patch.object(
                    native, '_shell_recycle') as shell:
                with self.assertRaises(native.PlatformRefused):
                    function(['C:\\synthetic-cache'], max_seconds=float('nan'))
                api.assert_not_called()
                shell.assert_not_called()


if __name__ == '__main__':
    unittest.main()
