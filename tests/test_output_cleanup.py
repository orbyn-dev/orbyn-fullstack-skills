"""Output ownership and completion proof on synthetic, read-only fixtures."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from test_tools import ROOT, module

engine = module('output_cleanup', ROOT/'skills/orbyn-worktree/scripts/output_cleanup.py')
artifact = module('output_cleanup_artifact', ROOT/'skills/orbyn-worktree/scripts/artifact_cache.py')
helpers = module('output_cleanup_helpers', ROOT/'skills/orbyn-worktree/scripts/worktree_retire.py')


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Windows runners can expose TEMP through an 8.3 alias; plans require
        # canonical paths, so construct the fixture from the resolved root.
        self.workspace = Path(self.temp.name).resolve()
        self.task = self.workspace/'output/task'
        self.repo = self.workspace/'repo/app'
        self.task.mkdir(parents=True)
        self.repo.mkdir(parents=True)
        self.now = time.time() + 40*artifact.DAY
        npm = json.dumps({'lockfileVersion': 3, 'packages': {'node_modules/pkg': {'version': '1.0.0'}}})
        self.write(self.repo/'Cargo.toml', '[package]\nname="fixture"\nversion="1.0.0"')
        self.write(self.repo/'Cargo.lock', 'version=3')
        self.write(self.repo/'package.json', '{"name":"fixture"}')
        self.write(self.repo/'package-lock.json', npm)
        self.write(self.task/'target/CACHEDIR.TAG', artifact.CACHE_SIGNATURE)
        self.write(self.task/'target/.rustc_info.json', '{"rustc":"rustc 1.90.0 (fixture)"}')
        self.write(self.task/'target/debug/deps/libfixture-123.rlib', 'compiled')
        self.write(self.task/'target/debug/build/fixture-123/out/generated.rs', 'generated')
        self.write(self.task/'target/debug/incremental/fixture-123/session/data.o', 'compiled')
        self.write(self.task/'node_modules/.package-lock.json', npm)
        self.write(self.task/'node_modules/pkg/index.js', 'module.exports=1')
        self.write(self.task/'evidence/report.txt', 'valuable evidence')
        self.write(self.task/'snapshot/.git/HEAD', 'source checkpoint')
        self.write(self.task/'emulator/userdata.img', 'valuable user data')
        self.head = 'a'*40
        self.data = {'schema_version': 1, 'task': str(self.task), 'repository': str(self.repo),
                     'completion': {'provider': 'github', 'number': 17, 'head': self.head, 'target': 'main'},
                     'keep': ['evidence/report.txt', 'snapshot', 'emulator'],
                     'units': [{'path': 'target/debug/deps', 'target': 'target', 'kind': 'cargo-deps', 'source': '.'}]}
        self.expected_completion = copy.deepcopy(self.data['completion'])
        self.names = ('Cargo.toml', 'Cargo.lock', 'package.json', 'package-lock.json')
        self.blobs = {name: (self.repo/name).read_bytes() for name in self.names}
        self.blob_modes = {}
        self.output_tracked, self.git_calls, self.usage_calls = [], [], []
        self.available, self.provider_ok = True, True
        self.promisor, self.partial_clone, self.config_error = None, None, False
        self.profile, self.profile_source, self.policy_reason = 'aggressive', 'owner selection', None
        self.provider_proof = {'provider': 'github', 'repository': 'example/app', 'head': self.head, 'target': 'main', 'merged': True}
        ap = artifact.Probes(self.git, self.usage, lambda paths: False, self.now, None, helpers, str(self.workspace))
        self.probes = engine.Probes(str(self.workspace), helpers, artifact, ap, self.policy, self.completion)
        self.age(20)
        self.save()

    def write(self, path, text='x'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def age(self, days):
        stamp = time.time() - days*artifact.DAY
        for here, dirs, files in os.walk(self.workspace):
            for name in dirs+files:
                os.utime(Path(here)/name, (stamp, stamp))
        os.utime(self.workspace, (stamp, stamp))

    def save(self):
        path = engine.plan_path(self.task, self.workspace)
        self.write(path, json.dumps(self.data))
        return path

    def policy(self, repo):
        self.assertEqual(str(repo), str(self.repo))
        return self.profile, self.profile_source, self.policy_reason

    def completion(self, repo, completion):
        if not self.provider_ok or completion != self.expected_completion:
            return None, 'provider is unmerged or unavailable'
        return copy.deepcopy(self.provider_proof), None

    def git(self, repo, *args):
        self.git_calls.append((str(repo), args))
        code, text = 0, ''
        if args[0] == 'check-ref-format':
            code = 1 if ' ' in args[-1] else 0
        elif args[0] == 'config':
            value = self.promisor if args[1] == '--get-regexp' else self.partial_clone
            code, text = (2, '') if self.config_error else (1, '') if value is None else (0, value)
        elif args[0] == 'ls-files':
            names = self.names if str(repo) == str(self.repo) else self.output_tracked
            text = '\0'.join(names) + ('\0' if names else '')
        elif args[:2] == ('cat-file', '-t'):
            code, text = (0, 'commit\n') if self.available else (1, '')
        elif args[0] == 'ls-tree':
            name = args[-1].removeprefix(':(literal)')
            mode = self.blob_modes.get(name, '100644')
            text = f'{mode} blob {"b"*40}\t{name}\0' if name in self.blobs and mode is not None else ''
        elif args[:2] == ('cat-file', '-s'):
            name = args[-1].split(':', 1)[1]
            code, text = (0, str(len(self.blobs[name]))) if name in self.blobs else (1, '')
        elif args[0] == 'show':
            name = args[-1].split(':', 1)[1]
            code, text = (0, self.blobs[name]) if name in self.blobs else (1, '')
        else:
            raise AssertionError(f'unexpected Git command {args}')
        return subprocess.CompletedProcess(args, code, text, '')

    def usage(self, path):
        self.usage_calls.append(path)
        return None

    def spec(self):
        return engine.discover(str(self.task), self.probes)[0]

    def evaluate(self):
        return engine.evaluate(self.spec(), self.probes)

    def test_all_typed_units_eligible_preserving_source_and_unlisted_data(self):
        before = {path: path.read_bytes() for path in (self.repo/'Cargo.toml', self.task/'evidence/report.txt', self.task/'emulator/userdata.img')}
        for kind, path in (('cargo-deps', 'target/debug/deps'), ('cargo-build', 'target/debug/build'),
                           ('cargo-incremental', 'target/debug/incremental/fixture-123'), ('node-modules', 'node_modules')):
            with self.subTest(kind=kind):
                self.data['units'] = [{'kind': kind, 'path': path, 'target': 'node_modules' if kind == 'node-modules' else 'target', 'source': '.'}]
                self.save()
                item = self.evaluate()
                self.assertEqual(item['status'], 'eligible', item)
                self.assertEqual(item['trash'], [str(self.task/path)])
                self.assertEqual(item['root'], str(self.task))
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))
        self.assertTrue(all(args[0] in ('config', 'ls-files', 'ls-tree', 'check-ref-format', 'cat-file', 'show') for _, args in self.git_calls))

    def test_missing_registry_returns_none_and_record_creation_does_not_touch_task(self):
        path = engine.plan_path(self.task, self.workspace)
        path.unlink()
        self.assertIsNone(engine.discover(str(self.task), self.probes))
        before = artifact.identity(self.task), self.task.stat().st_mtime_ns
        self.save()
        self.assertEqual((artifact.identity(self.task), self.task.stat().st_mtime_ns), before)

    def test_plan_metadata_strict_and_bounded(self):
        path = engine.plan_path(self.task, self.workspace)
        for text in ('{}', '{"schema_version":1,"schema_version":1}', '{bad', 'x'*65537):
            with self.subTest(prefix=text[:30]):
                self.write(path, text)
                self.assertEqual(self.evaluate()['status'], 'held')

    def test_path_traversal_absolute_unknown_kind_and_overlap_are_held(self):
        original = copy.deepcopy(self.data)
        cases = [('path', '../evidence'), ('path', '/absolute'), ('path', 'C:/outside'), ('path', 'target\\debug\\deps'),
                 ('path', '.'), ('kind', 'android-avd'), ('target', '.'), ('source', '../app')]
        for field, value in cases:
            self.data = copy.deepcopy(original)
            self.data['units'][0][field] = value
            self.save()
            self.assertEqual(self.evaluate()['status'], 'held', (field, value))
        self.data = copy.deepcopy(original)
        self.data['units'].append(copy.deepcopy(self.data['units'][0]))
        self.save()
        self.assertEqual(self.evaluate()['status'], 'held')
        self.data = copy.deepcopy(original)
        self.data['keep'].append('target/debug/deps/libfixture-123.rlib')
        self.save()
        self.assertEqual(self.evaluate()['status'], 'held')

    def test_missing_keep_noncanonical_repo_and_wrong_task_hold(self):
        original = copy.deepcopy(self.data)
        for key, value in (('keep', ['missing-evidence']), ('keep', []), ('repository', str(self.repo/'..')),
                           ('task', str(self.workspace/'output/other-task'))):
            self.data = copy.deepcopy(original)
            self.data[key] = value
            self.save()
            self.assertEqual(self.evaluate()['status'], 'held')

    def test_provider_unknown_missing_commit_and_source_mismatch_hold(self):
        self.provider_ok = False
        self.assertEqual(self.evaluate()['status'], 'held')
        self.provider_ok = True
        self.available = False
        self.assertEqual(self.evaluate()['status'], 'held')
        self.available = True
        self.write(self.repo/'Cargo.toml', 'changed source')
        self.assertEqual(self.evaluate()['status'], 'held')
        self.assertEqual(self.usage_calls, [])

    def test_source_crlf_only_normalization_and_missing_blobs(self):
        path = self.repo/'Cargo.toml'
        path.write_bytes(self.blobs['Cargo.toml'].replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        self.assertEqual(self.evaluate()['status'], 'eligible')
        self.blobs.pop('Cargo.lock')
        self.assertEqual(self.evaluate()['status'], 'held')

    def test_policy_off_legacy_unknown_and_strict_thresholds(self):
        for profile in ('off', 'legacy', 'unknown'):
            self.profile = profile
            self.assertEqual(self.evaluate()['status'], 'held')
        for profile, days in engine.PROFILES.items():
            self.profile = profile
            stamp = self.now - days*artifact.DAY
            unit = self.task/'target/debug/deps'
            for path in (unit, unit/'libfixture-123.rlib'):
                os.utime(path, (stamp, stamp))
            self.assertEqual(self.evaluate()['status'], 'waiting')
            for path in (unit, unit/'libfixture-123.rlib'):
                os.utime(path, (stamp-1, stamp-1))
            self.assertEqual(self.evaluate()['status'], 'eligible')

    def test_hold_expired_and_keep_ancestor_protection(self):
        for text, status in (('{}', 'held'), ('{"reason":"review","review_by":"2000-01-01"}', 'report-only')):
            hold = self.write(self.task/'HOLD.json', text)
            self.assertEqual(self.evaluate()['status'], status)
            hold.unlink()
        keep = self.write(self.task/'KEEP.txt', 'evidence/report.txt\n')
        self.assertEqual(self.evaluate()['status'], 'eligible')
        keep.write_text('target/debug/deps/libfixture-123.rlib\n', encoding='utf-8')
        self.assertEqual(self.evaluate()['status'], 'held')

    def test_secrets_nested_git_links_and_tracked_output_hold(self):
        for name in ('.env', '.git', 'KEEP.txt'):
            path = self.write(self.task/'target/debug/deps'/name)
            self.assertEqual(self.evaluate()['status'], 'held')
            path.unlink()
        original = helpers.linked
        leaf = self.task/'target/debug/deps/libfixture-123.rlib'
        with mock.patch.object(helpers, 'linked', side_effect=lambda path: str(path) == str(leaf) or original(path)):
            self.assertEqual(self.evaluate()['status'], 'held')
        self.output_tracked = ['output/task/target/debug/deps/libfixture-123.rlib']
        self.assertEqual(self.evaluate()['status'], 'held')
        self.assertEqual(self.usage_calls, [])

    def test_incomplete_scan_busy_unknown_and_recent_producer_hold(self):
        item = engine.evaluate(self.spec(), self.probes, artifact.Budget(entry_limit=1))
        self.assertEqual(item['status'], 'held')
        self.assertFalse(item['complete'])
        self.probes.artifact_probes.usage = lambda path: 'active consumer'
        self.assertEqual(self.evaluate()['status'], 'held')
        self.probes.artifact_probes.usage = self.usage
        for value in (None, True):
            self.probes.artifact_probes.dirs_busy = lambda paths, value=value: value
            self.assertEqual(self.evaluate()['status'], 'held')
        self.probes.artifact_probes.dirs_busy = lambda paths: False
        stamp = self.now - 1
        os.utime(self.task, (stamp, stamp))
        self.assertEqual(self.evaluate()['status'], 'held')

    def test_recheck_plan_identity_policy_completion_and_source_changes_skip(self):
        spec, item = self.spec(), self.evaluate()
        self.profile = 'moderate'
        self.assertEqual(engine.recheck(spec, item, self.probes)['status'], 'skipped')
        self.profile = 'aggressive'
        self.provider_proof['readback'] = 'changed'
        self.assertEqual(engine.recheck(spec, item, self.probes)['status'], 'skipped')
        self.provider_proof.pop('readback')
        self.write(self.repo/'Cargo.lock', 'changed lock')
        self.assertEqual(engine.recheck(spec, item, self.probes)['status'], 'skipped')
        self.write(self.repo/'Cargo.lock', self.blobs['Cargo.lock'].decode())
        self.save()
        self.assertEqual(engine.recheck(spec, item, self.probes)['status'], 'skipped')

    def test_replaced_candidate_and_kept_evidence_skip(self):
        spec, item = self.spec(), self.evaluate()
        unit = self.task/'target/debug/deps'
        unit.rename(unit.with_name('previous-deps'))
        unit.mkdir()
        self.assertEqual(engine.recheck(spec, item, self.probes)['status'], 'skipped')
        unit.rmdir()
        unit.with_name('previous-deps').rename(unit)
        spec, item = self.spec(), self.evaluate()
        self.write(self.task/'evidence/report.txt', 'changed valuable evidence')
        self.assertEqual(engine.recheck(spec, item, self.probes)['status'], 'skipped')

    def test_unknown_compiler_and_malformed_npm_proof_never_eligible(self):
        self.write(self.task/'target/.rustc_info.json', '{"rustc":"rustc 1.100.0 (new layout)"}')
        self.assertEqual(self.evaluate()['status'], 'report-only')
        self.data['units'] = [{'path': 'node_modules', 'target': 'node_modules', 'kind': 'node-modules', 'source': '.'}]
        self.save()
        self.write(self.task/'node_modules/.package-lock.json', '{}')
        self.assertEqual(self.evaluate()['status'], 'held')

    def test_partial_clone_and_config_failure_hold_before_object_access(self):
        for field, value in (('promisor', 'remote.origin.promisor true\n'), ('partial_clone', 'origin\n'), ('config_error', True)):
            with self.subTest(field=field):
                original = getattr(self, field)
                setattr(self, field, value)
                self.git_calls.clear()
                self.assertEqual(self.evaluate()['status'], 'held')
                self.assertFalse(any(args[0] in ('cat-file', 'show') for _, args in self.git_calls))
                setattr(self, field, original)
        self.promisor = 'remote.origin.promisor false\n'
        self.assertEqual(self.evaluate()['status'], 'eligible')

    def test_completion_schema_and_exact_provider_record_are_required(self):
        original = copy.deepcopy(self.data['completion'])
        for field, value in (('number', True), ('number', 0), ('head', 'bad'), ('target', '--option'),
                             ('target', '@{-1}'), ('target', 'bad branch'), ('provider', 'arbitrary'),
                             ('head', 'b'*40), ('target', 'other-branch'), ('number', 18)):
            self.data['completion'] = {**original, field: value}
            self.save()
            self.assertEqual(self.evaluate()['status'], 'held', (field, value))
        self.assertEqual(self.usage_calls, [])

    def test_registry_tracking_and_reparse_protections(self):
        record = engine.plan_path(self.task, self.workspace)
        self.output_tracked = [record.relative_to(self.workspace).as_posix()]
        self.assertEqual(self.evaluate()['status'], 'held')
        self.output_tracked = []
        original = helpers.linked
        for protected in (record, record.parent, self.task, self.repo/'Cargo.toml'):
            with self.subTest(path=str(protected)):
                with mock.patch.object(helpers, 'linked', side_effect=lambda path, protected=protected: str(path) == str(protected) or original(path)):
                    self.assertEqual(self.evaluate()['status'], 'held')
        self.assertEqual(self.usage_calls, [])

    def test_tool_lock_and_usage_change_at_recheck_hold(self):
        lock = self.write(self.task/'target/debug/.cargo-lock')
        paths_seen = []
        def busy(paths):
            paths_seen.extend(paths)
            return str(lock) in paths
        self.probes.artifact_probes.dirs_busy = busy
        self.assertEqual(self.evaluate()['status'], 'held')
        self.assertIn(str(lock), paths_seen)
        self.probes.artifact_probes.dirs_busy = lambda paths: False
        spec, item = self.spec(), self.evaluate()
        self.probes.artifact_probes.usage = lambda path: 'consumer appeared after report'
        self.assertEqual(engine.recheck(spec, item, self.probes)['status'], 'skipped')

    def test_multispec_accounting_and_unlisted_source_checkpoint(self):
        self.data['units'].append({'path': 'target/debug/build', 'target': 'target', 'kind': 'cargo-build', 'source': '.'})
        self.save()
        budget = artifact.Budget()
        specs = engine.discover(str(self.task), self.probes)
        items = [engine.evaluate(spec, self.probes, budget) for spec in specs]
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item['status'] == 'eligible' for item in items))
        self.assertTrue(all(item['path'] != str(self.task) for item in items))
        self.assertTrue((self.task/'snapshot/.git/HEAD').is_file())
        self.assertTrue((self.task/'emulator/userdata.img').is_file())

    def test_source_is_unchanged_after_all_readonly_checks(self):
        paths = [self.repo/name for name in self.names]
        before = {str(path): (path.read_bytes(), artifact.identity(path), path.stat().st_mtime_ns) for path in paths}
        spec, item = self.spec(), self.evaluate()
        self.assertEqual(engine.recheck(spec, item, self.probes)['status'], 'eligible')
        after = {str(path): (path.read_bytes(), artifact.identity(path), path.stat().st_mtime_ns) for path in paths}
        self.assertEqual(before, after)

    def test_historical_source_requires_regular_git_blob_mode(self):
        # Identical symlink-blob bytes must not prove an ordinary current file.
        for mode in ('120000', '160000', '040000', None):
            with self.subTest(mode=mode):
                self.blob_modes['Cargo.toml'] = mode
                self.git_calls.clear()
                item = self.evaluate()
                self.assertEqual(item['status'], 'held')
                self.assertIn('produced source', item['reason'])
                self.assertFalse(any(args[0] == 'show' for _, args in self.git_calls))
        self.blob_modes['Cargo.toml'] = '100755'
        self.assertEqual(self.evaluate()['status'], 'eligible')

    def test_absent_unit_does_not_invalidate_disjoint_sibling(self):
        self.data['units'].append({'path': 'target/debug/build', 'target': 'target', 'kind': 'cargo-build', 'source': '.'})
        self.save()
        specs = engine.discover(str(self.task), self.probes)
        before = [engine.evaluate(spec, self.probes) for spec in specs]
        self.assertTrue(all(item['status'] == 'eligible' for item in before))
        unit = self.task/'target/debug/deps'
        unit.rename(self.workspace/'moved-deps')
        resumed = engine.discover(str(self.task), self.probes)
        self.assertEqual(len(resumed), 2)
        self.assertIn('already absent', engine.evaluate(resumed[0], self.probes)['reason'])
        self.assertEqual(engine.recheck(specs[0], before[0], self.probes)['status'], 'skipped')
        self.assertEqual(engine.recheck(specs[1], before[1], self.probes)['status'], 'eligible')


if __name__ == '__main__':
    unittest.main()
