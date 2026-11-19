"""Cache eligibility uses disposable trees and injected idle/unknown probes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from test_tools import ROOT, module

cache = module('artifact_cache', ROOT/'skills/orbyn-worktree/scripts/artifact_cache.py')
helpers = module('artifact_cache_helpers', ROOT/'skills/orbyn-worktree/scripts/worktree_retire.py')


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Windows runners can expose TEMP through an 8.3 alias; production
        # requires canonical owner paths, so build the fixture from that path.
        self.workspace = Path(self.temp.name).resolve()
        self.root = self.workspace/'app'
        self.root.mkdir()
        self.gitdir = self.root/'.git'
        self.write('.git/index')
        self.write('.git/logs/HEAD')
        self.write('Cargo.toml', '[package]\nname="fixture"\nversion="0.1.0"')
        self.write('Cargo.lock', 'version = 3')
        self.write('package.json', '{}')
        npm_lock = json.dumps({'lockfileVersion': 3, 'packages': {'node_modules/pkg': {'version': '1.0.0'}}})
        self.write('package-lock.json', npm_lock)
        self.write('target/CACHEDIR.TAG', cache.CACHE_SIGNATURE+'\n# Cargo cache')
        self.write('target/.rustc_info.json', json.dumps({'outputs': {'key': {'stdout': 'rustc 1.90.0 (fixture)\nbinary: rustc'}}}))
        self.write('target/debug/deps/libfixture-123.rlib', 'compiled')
        self.write('target/debug/build/fixture-123/out/generated.rs', 'generated')
        self.write('target/debug/incremental/fixture-123/session/object.o', 'compiled')
        self.write('node_modules/.package-lock.json', npm_lock)
        self.write('node_modules/pkg/index.js', 'module.exports=1')
        self.names = ('Cargo.toml', 'Cargo.lock', 'package.json', 'package-lock.json')
        self.ignored = ('target/', 'node_modules/')
        self.untracked = ()
        self.git_calls, self.usage_calls = [], []
        # ctime cannot be backdated with utime on POSIX. Advance only the
        # injected inspection clock, matching the production max(mtime, ctime).
        self.now = time.time() + 20*cache.DAY
        self.probes = cache.Probes(self.git, self.usage, lambda paths: False, self.now,
                                   lambda args, timeout: subprocess.CompletedProcess(args, 0, '{}', ''), helpers,
                                   workspace=str(self.workspace))
        self.age(10)

    def write(self, name, text='x'):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def age(self, days):
        stamp = time.time() - days*cache.DAY
        for here, dirs, files in os.walk(self.workspace):
            for name in dirs + files:
                os.utime(Path(here)/name, (stamp, stamp))
        os.utime(self.workspace, (stamp, stamp))

    def git(self, path, *args):
        self.git_calls.append(args)
        text = ''
        if args[0] == 'ls-files':
            names = self.ignored if '--ignored' in args else self.untracked if '--others' in args else self.names
            text = '\0'.join(names)+'\0'
        return subprocess.CompletedProcess(args, 0, text, '')

    def usage(self, path):
        self.usage_calls.append(path)
        return None

    def specs(self):
        return cache.discover(str(self.root), str(self.gitdir), self.probes)

    def spec(self, kind='cargo-deps'):
        return next(s for s in self.specs() if s['kind'] == kind)

    def evaluate(self, kind='cargo-deps', profile='moderate', budget=None):
        return cache.evaluate(self.spec(kind), profile, self.probes, budget or cache.Budget())

    def test_compiler_units_and_dependencies_eligible_with_dirty_source(self):
        self.write('source.rs', 'uncommitted source')
        self.write('.git/refs/heads/open-pr', 'unpushed')
        self.age(10)
        before = (self.root/'source.rs').read_bytes()
        for kind in ('cargo-deps', 'cargo-build', 'cargo-incremental', 'node-modules'):
            with self.subTest(kind=kind):
                item = self.evaluate(kind)
                self.assertEqual(item['status'], 'eligible', item)
                self.assertEqual(item['trash'], [item['path']])
        self.assertEqual((self.root/'source.rs').read_bytes(), before)
        self.assertTrue(self.gitdir.is_dir())
        self.assertTrue(all(args[0] in ('ls-files', 'check-ignore') for args in self.git_calls))

    def test_safe_dependency_profile_and_strict_threshold(self):
        self.assertEqual(self.evaluate('node-modules', 'safe')['status'], 'report-only')
        spec = self.spec()
        stamp = self.now-7*cache.DAY
        for here, dirs, files in os.walk(spec['path']):
            os.utime(here, (stamp, stamp))
            for name in dirs+files:
                os.utime(Path(here)/name, (stamp, stamp))
        self.assertEqual(self.evaluate(profile='safe')['status'], 'waiting')
        self.assertEqual(self.usage_calls, [])
        self.assertEqual(self.evaluate(profile='aggressive')['status'], 'eligible')

    def test_old_mtime_with_recent_ctime_is_not_old_cache(self):
        self.probes.now = time.time() + 3600
        item = self.evaluate()
        self.assertEqual(item['status'], 'waiting')
        self.assertLess(item['age_days'], 1)
        self.assertEqual(self.usage_calls, [])

    def test_malformed_tool_proof_is_held_before_usage(self):
        for name, text in (('target/CACHEDIR.TAG', 'not a cache'), ('target/.rustc_info.json', '{bad')):
            with self.subTest(name=name):
                original = (self.root/name).read_text(encoding='utf-8')
                self.write(name, text)
                self.age(10)
                self.assertEqual(self.evaluate()['status'], 'held')
                self.assertEqual(self.usage_calls, [])
                self.write(name, original)

    def test_tracked_source_and_lock_are_required(self):
        self.names = ('Cargo.toml',)
        self.assertEqual(self.evaluate()['status'], 'held')
        self.assertEqual(self.usage_calls, [])

    def test_unknown_compiler_and_release_layout_report_only(self):
        self.write('target/.rustc_info.json', '{"rustc":"rustc 1.100.0 (new layout)"}')
        self.age(10)
        self.assertEqual(self.evaluate()['status'], 'report-only')
        self.write('target/release/deps/file.rlib')
        self.probes.discovery.clear()
        release = next(s for s in self.specs() if s['path'].endswith('release'))
        self.assertNotEqual(cache.evaluate(release, 'aggressive', self.probes, cache.Budget())['status'], 'eligible')

    def test_stable_compiler_build_date_is_not_a_prerelease(self):
        self.write('target/.rustc_info.json', json.dumps({'rustc': 'rustc 1.99.1 (abc123456 2026-10-13)'}))
        self.age(10)
        self.assertEqual(self.evaluate()['status'], 'eligible')
        for version in ('rustc 1.99.1-nightly (abc123456 2026-10-13)',
                        'rustc 1.99.1-beta.1 (abc123456 2026-10-13)',
                        'rustc 1.100.0 (abc123456 2026-10-13)',
                        'rustc 1.99.1 unknown format'):
            with self.subTest(version=version):
                self.write('target/.rustc_info.json', json.dumps({'rustc': version}))
                self.age(10)
                self.assertEqual(self.evaluate()['status'], 'report-only')

    def test_holds_keep_and_expired_ancestor(self):
        for name, text, status in (('HOLD.json', '{}', 'held'), ('KEEP.txt', 'Cargo.toml', 'held'),
                                  ('HOLD.json', '{"reason":"review","review_by":"2000-01-01"}', 'report-only')):
            with self.subTest(name=name, text=text):
                path = self.write(name, text)
                self.age(10)
                self.assertEqual(self.evaluate()['status'], status)
                self.assertEqual(self.usage_calls, [])
                path.unlink()

    def test_secret_nested_git_reparse_and_future_are_held(self):
        for name in ('target/debug/deps/.env', 'target/debug/deps/.git', 'target/debug/deps/KEEP.txt'):
            path = self.write(name)
            self.age(10)
            self.assertEqual(self.evaluate()['status'], 'held')
            path.unlink()
        original = helpers.linked
        unit = str(self.root/'target/debug/deps/libfixture-123.rlib')
        with mock.patch.object(helpers, 'linked', side_effect=lambda p: str(p) == unit or original(p)):
            self.assertEqual(self.evaluate()['status'], 'held')
        os.utime(unit, (self.now+1, self.now+1))
        self.assertEqual(self.evaluate()['status'], 'held')
        self.assertEqual(self.usage_calls, [])

    def test_busy_unknown_and_owner_quiet_window(self):
        self.probes.usage = lambda path: 'unknown usage'
        self.assertEqual(self.evaluate()['status'], 'held')
        self.probes.usage = self.usage
        for answer in (None, True, {'busy'}):
            self.probes.dirs_busy = lambda paths, answer=answer: answer
            self.assertEqual(self.evaluate()['status'], 'held')
        self.probes.dirs_busy = lambda paths: False
        stamp = self.now-cache.QUIET_SECONDS
        os.utime(self.gitdir/'index', (stamp, stamp))
        self.assertEqual(self.evaluate()['status'], 'held')
        os.utime(self.gitdir/'index', (stamp-1, stamp-1))
        self.assertEqual(self.evaluate()['status'], 'eligible')

    def test_budget_is_partial_and_memoized(self):
        budget = cache.Budget(entry_limit=1)
        first = self.evaluate(budget=budget)
        self.assertEqual(first['status'], 'held')
        self.assertFalse(first['complete'])
        self.assertIn('partial scan', first['reason'])
        with mock.patch.object(cache.os, 'scandir', side_effect=AssertionError('scan repeated')):
            second = self.evaluate(budget=budget)
        self.assertEqual(first['kb'], second['kb'])

    def test_hardlink_unique_bytes_once(self):
        source = self.root/'target/debug/deps/libfixture-123.rlib'
        try:
            os.link(source, source.with_name('second.rlib'))
        except OSError:
            self.skipTest('hardlinks unavailable')
        self.age(10)
        item = self.evaluate()
        self.assertGreater(item['kb'], item['kb_unique'])

    def test_recheck_rejects_replacement_and_new_activity(self):
        spec, item = self.spec(), self.evaluate()
        unit = Path(spec['path'])
        unit.rename(unit.with_name('previous-deps'))
        unit.mkdir()
        self.assertEqual(cache.recheck(spec, item, 'moderate', self.probes)['status'], 'skipped')
        unit.rmdir()
        unit.with_name('previous-deps').rename(unit)
        self.age(10)
        item = self.evaluate()
        self.probes.usage = lambda path: 'active handle'
        self.assertEqual(cache.recheck(spec, item, 'moderate', self.probes)['status'], 'skipped')

    def test_discovery_memo_within_and_git_failure(self):
        self.specs()
        self.specs()
        self.assertEqual(sum(args[0] == 'ls-files' for args in self.git_calls), 2)
        found = cache.discover(str(self.root), str(self.gitdir), self.probes, within=str(self.root/'node_modules'))
        self.assertEqual([s['kind'] for s in found], ['node-modules'])
        self.probes.discovery.clear()
        self.probes.git = lambda *args: subprocess.CompletedProcess(args, 1, '', '')
        held = cache.discover(str(self.root), str(self.gitdir), self.probes)[0]
        self.assertEqual(cache.evaluate(held, 'moderate', self.probes, cache.Budget())['status'], 'held')

    def test_malformed_config_and_tracked_cache_are_held(self):
        self.write('.cargo/config.toml', '[build\n')
        self.assertEqual(self.evaluate()['status'], 'held')
        (self.root/'.cargo/config.toml').unlink()
        self.probes.discovery.clear()
        self.names += ('target/debug/deps/libfixture-123.rlib',)
        self.assertEqual(self.evaluate()['status'], 'held')

    def test_install_marker_and_tool_lock_checks(self):
        self.write('node_modules/.package-lock.json', '{bad')
        self.age(10)
        self.assertEqual(self.evaluate('node-modules')['status'], 'held')
        self.assertEqual(self.usage_calls, [])
        lock = self.write('target/debug/.cargo-lock')
        self.age(10)
        observed = []
        def busy(paths):
            observed.extend(paths)
            return str(lock) in paths
        self.probes.dirs_busy = busy
        self.assertEqual(self.evaluate()['status'], 'held')
        self.assertIn(str(lock), observed)

    def test_discovery_budget_and_nested_roots_are_visible(self):
        self.ignored += ('target/debug/deps/node_modules/',)
        specs = self.specs()
        self.assertEqual(sum(s['kind'] == 'node-modules' for s in specs), 1)
        self.assertTrue(any(s.get('nested_in') for s in specs))
        self.probes.discovery.clear()
        with mock.patch.dict(self.probes.limits, {'entry_limit': 1}):
            specs = self.specs()
        partial = next(s for s in specs if s.get('discovery_error'))
        item = cache.evaluate(partial, 'moderate', self.probes, cache.Budget())
        self.assertEqual(item['status'], 'held')
        self.assertFalse(item['complete'])

    def test_external_configured_store_is_report_only(self):
        self.write('.cargo/config.toml', '[build]\ntarget-dir="../external-target"')
        specs = self.specs()
        external = next(s for s in specs if s['kind'] == 'configured-store')
        self.assertEqual(cache.evaluate(external, 'moderate', self.probes, cache.Budget())['status'], 'report-only')

    def test_unparsed_yaml_marker_is_never_proof(self):
        (self.root/'package-lock.json').unlink()
        self.write('pnpm-lock.yaml', 'lockfileVersion: 9')
        self.write('node_modules/.modules.yaml', 'malformed: [')
        self.names += ('pnpm-lock.yaml',)
        self.age(10)
        item = self.evaluate('node-modules')
        self.assertEqual(item['status'], 'report-only')
        self.assertIn('parser adapter', item['reason'])
        self.assertEqual(self.usage_calls, [])

    def test_large_valid_npm_marker_and_lock_are_supported(self):
        data = {'lockfileVersion': 3, 'packages': {f'node_modules/fixture-{i}': {'version': '1.0.0'} for i in range(3000)}}
        text = json.dumps(data)
        self.assertGreater(len(text.encode()), 65536)
        self.write('package-lock.json', text)
        self.write('node_modules/.package-lock.json', text)
        self.age(10)
        self.assertEqual(self.evaluate('node-modules')['status'], 'eligible')

    def test_npm_schema_and_lock_install_mismatches_are_held(self):
        good = {'lockfileVersion': 3, 'packages': {'node_modules/pkg': {'version': '1.0.0'}}}
        invalid = ({}, {'unrelated': True}, {'lockfileVersion': 99, 'packages': good['packages']},
                   {'lockfileVersion': 3}, {'lockfileVersion': 3, 'packages': {}},
                   {'lockfileVersion': 3, 'packages': {'node_modules/pkg': {}}},
                   {'lockfileVersion': 3, 'packages': {'node_modules/pkg': {'version': 1}}},
                   {'lockfileVersion': 3, 'packages': {'node_modules/pkg': {'version': '2.0.0'}}},
                   {'lockfileVersion': 3, 'packages': {'node_modules/pkg': []}},
                   {'lockfileVersion': 3, 'packages': {'node_modules/../personal': {'version': '1.0.0'}}},
                   {'lockfileVersion': 3, 'packages': {'node_modules/pkg': {'version': '1.0.0', 'link': True}}})
        for data in invalid:
            with self.subTest(data=data):
                self.write('package-lock.json', json.dumps(good))
                self.write('node_modules/.package-lock.json', json.dumps(data))
                self.age(10)
                self.assertEqual(self.evaluate('node-modules')['status'], 'held')
        for data in ({}, {'lockfileVersion': 99, 'packages': good['packages']},
                     {'lockfileVersion': 3, 'packages': {}}, {'lockfileVersion': 3, 'packages': {'node_modules/pkg': {}}}):
            with self.subTest(source=data):
                self.write('package-lock.json', json.dumps(data))
                self.write('node_modules/.package-lock.json', json.dumps(good))
                self.age(10)
                self.assertEqual(self.evaluate('node-modules')['status'], 'held')
        self.assertEqual(self.usage_calls, [])

    def test_npm_supported_versions_and_integrity_resolved_match(self):
        package = {'version': '1.0.0', 'integrity': 'sha512-fixture', 'resolved': 'https://example.test/pkg.tgz'}
        for source_version in (2, 3):
            source = {'lockfileVersion': source_version, 'packages': {'': {'name': 'fixture'}, 'node_modules/pkg': dict(package)}}
            marker = {'lockfileVersion': 3, 'packages': {'node_modules/pkg': dict(package)}}
            self.write('package-lock.json', json.dumps(source))
            self.write('node_modules/.package-lock.json', json.dumps(marker))
            self.age(10)
            self.assertEqual(self.evaluate('node-modules')['status'], 'eligible')
            for field in ('integrity', 'resolved'):
                changed = json.loads(json.dumps(marker))
                changed['packages']['node_modules/pkg'][field] = 'different'
                self.write('node_modules/.package-lock.json', json.dumps(changed))
                self.age(10)
                self.assertEqual(self.evaluate('node-modules')['status'], 'held')
            marker['lockfileVersion'] = 2
            self.write('node_modules/.package-lock.json', json.dumps(marker))
            self.age(10)
            self.assertEqual(self.evaluate('node-modules')['status'], 'held')

    def test_yarn_classic_integrity_is_report_only(self):
        (self.root/'package-lock.json').unlink()
        self.write('yarn.lock', '# yarn lockfile v1\npkg@1.0.0:\n  version "1.0.0"')
        self.write('node_modules/.yarn-integrity', '{}')
        self.names += ('yarn.lock',)
        self.age(10)
        item = self.evaluate('node-modules')
        self.assertEqual(item['status'], 'report-only')
        self.assertIn('lock/install parser adapter', item['reason'])
        self.assertEqual(self.usage_calls, [])

    def test_invalid_duplicate_typed_and_oversized_install_json_hold(self):
        cases = ('{"packages":{},"packages":{}}', '[1]', '{"lockfileVersion":"3"}',
                 '{"packages":[]}', '{"unknown":NaN}', '{malformed',
                 '{"padding":"' + 'x'*cache.INSTALL_JSON_MAX_BYTES + '"}')
        for text in cases:
            with self.subTest(prefix=text[:40]):
                self.write('node_modules/.package-lock.json', text)
                self.age(10)
                self.assertEqual(self.evaluate('node-modules')['status'], 'held')
                self.assertEqual(self.usage_calls, [])

    def test_sibling_worktree_common_ancestor_protections(self):
        # The move root remains this worktree, while workspace is its sibling.
        sibling_workspace = self.workspace/'library'
        sibling_workspace.mkdir()
        self.probes.workspace = str(sibling_workspace)
        self.assertEqual(self.evaluate()['status'], 'eligible')
        ancestor_hold = self.workspace/'HOLD.json'
        ancestor_hold.write_text('{}', encoding='utf-8')
        self.assertEqual(self.evaluate()['status'], 'held')
        ancestor_hold.unlink()
        ancestor_keep = self.workspace/'KEEP.txt'
        ancestor_keep.write_text('app/Cargo.toml', encoding='utf-8')
        self.assertEqual(self.evaluate()['status'], 'held')

    def test_recent_tracked_source_holds_before_usage(self):
        source = self.write('src/lib.rs', 'edited source')
        self.names += ('src/lib.rs',)
        self.age(10)
        stamp = self.now - 1
        os.utime(source, (stamp, stamp))
        item = self.evaluate()
        self.assertEqual(item['status'], 'held')
        self.assertIn('source activity', item['reason'])
        self.assertEqual(self.usage_calls, [])

    def test_recent_untracked_source_holds_and_idle_dirty_source_qualifies(self):
        source = self.write('notes/work.txt', 'untracked source')
        self.untracked = ('notes/work.txt',)
        self.age(10)
        self.assertEqual(self.evaluate()['status'], 'eligible')
        self.usage_calls.clear()
        self.probes.owner_activity.clear()
        stamp = self.now - 1
        os.utime(source, (stamp, stamp))
        item = self.evaluate()
        self.assertEqual(item['status'], 'held')
        self.assertIn('source activity', item['reason'])
        self.assertEqual(self.usage_calls, [])

    def test_owner_activity_memo_and_unknown_budget(self):
        self.assertEqual(self.evaluate()['status'], 'eligible')
        self.assertEqual(self.evaluate('cargo-build')['status'], 'eligible')
        calls = [args for args in self.git_calls if '--others' in args and '--ignored' not in args]
        self.assertEqual(len(calls), 1)
        self.probes.owner_activity.clear()
        with mock.patch.dict(self.probes.limits, {'entry_limit': 1}):
            item = self.evaluate()
        self.assertEqual(item['status'], 'held')
        self.assertIn('source activity', item['reason'])
        self.assertIn('budget', item['reason'])
        self.assertFalse(item['complete'])

    def test_recheck_refreshes_source_activity_and_listing_unknown_holds(self):
        spec, item = self.spec(), self.evaluate()
        source = self.write('new-source.txt', 'active untracked source')
        self.untracked = ('new-source.txt',)
        stamp = self.now - 1
        os.utime(source, (stamp, stamp))
        checked = cache.recheck(spec, item, 'moderate', self.probes)
        self.assertEqual(checked['status'], 'skipped')
        self.assertIn('source activity', checked['reason'])
        self.age(10)
        self.probes.owner_activity.clear()
        self.probes.source_names.clear()
        original = self.probes.git
        def unknown(path, *args):
            if '--others' in args and '--ignored' not in args:
                return subprocess.CompletedProcess(args, 1, '', 'cannot list')
            return original(path, *args)
        self.probes.git = unknown
        held = self.evaluate()
        self.assertEqual(held['status'], 'held')
        self.assertFalse(held['complete'])
        self.assertIn('cannot list', held['reason'])

    def test_docker_exact_allowlist_and_all_volumes_held(self):
        calls = []
        outputs = ['{}', '{"Type":"Images","Size":"2MB"}\n{"Type":"Local Volumes","Size":"3MB"}', 'summary']
        def runner(args, timeout):
            calls.append((tuple(args), timeout))
            return subprocess.CompletedProcess(args, 0, outputs[len(calls)-1], '')
        self.probes.run_readonly = runner
        report = cache.build_storage(self.probes, str(self.workspace/'empty-home'), {})
        self.assertEqual(calls, [(args, 15) for args in cache.DOCKER_COMMANDS])
        self.assertEqual(report['docker']['state'], 'ok')
        self.assertEqual(report['docker']['volumes']['status'], 'held')
        self.assertGreater(report['docker']['volumes']['kb'], 0)
        self.probes.run_readonly = lambda *args: subprocess.CompletedProcess([], 1, '', 'private daemon URL')
        report = cache.build_storage(self.probes, str(self.workspace/'empty-home'), {})
        self.assertEqual(report['docker']['state'], 'unknown')
        self.assertNotIn('private', report['docker']['reason'])
        self.probes.run_readonly = mock.Mock(side_effect=FileNotFoundError)
        self.assertEqual(cache.build_storage(self.probes, str(self.workspace/'empty-home'), {})['docker']['state'], 'absent')


if __name__ == '__main__':
    unittest.main()
