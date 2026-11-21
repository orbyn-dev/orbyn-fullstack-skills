"""Whole-worktree shared-junction policy and retirement sequencing on disposable fixtures."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPT = Path(os.environ.get('ORBYN_JUNCTION_POLICY_SCRIPT',
    str(Path(__file__).resolve().parents[1]/'skills/orbyn-worktree/scripts/worktree_retire.py')))
spec = importlib.util.spec_from_file_location('junction_policy_tool', SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class JunctionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='orbyn-junction-policy-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base/'source'
        self.root.mkdir()

    def write(self, name, data='synthetic'):
        p = self.root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding='utf-8')
        return p

    def approvals(self, names, ignored=True):
        def git(_root, *args, **_kwargs):
            if args[:2] == ('ls-files', '-z'):
                return subprocess.CompletedProcess(args, 0, '\0'.join(names)+'\0', '')
            if args[:2] == ('check-ignore', '-q'):
                return subprocess.CompletedProcess(args, 0 if ignored else 1, '', '')
            raise AssertionError(args)
        return mock.patch.object(tool, 'git', side_effect=git)

    def test_opt_in_rejects_cache_output_and_unscoped_actions_before_workspace_access(self):
        with mock.patch.object(tool.native, 'is_windows', return_value=True), mock.patch.object(tool, 'verify_workspace') as verify:
            for only in (None, 'artifact', 'cache', 'output'):
                with self.subTest(only=only), self.assertRaises(tool.Refused):
                    tool.report(self.base, 'origin/main', only=only, preserve_shared_junctions=True)
                with self.subTest(apply_only=only), self.assertRaises(tool.Refused):
                    tool.apply(self.base, 'origin/main', only=only, preserve_shared_junctions=True)
            verify.assert_not_called()
        with mock.patch.object(tool.native, 'is_windows', return_value=False):
            with self.assertRaises(tool.Refused):
                tool.check_junction_scope(True, 'worktree')
        tool.check_junction_scope(False, None)

    def test_cli_routes_explicit_opt_in_and_preserves_default(self):
        for selected in (False, True):
            argv = ['report', '--only', 'worktree', '--json']
            if selected:
                argv.append('--preserve-shared-junctions')
            with mock.patch.object(tool, 'report', return_value={}) as report, mock.patch('builtins.print'):
                self.assertEqual(tool.main(argv), 0)
            self.assertIs(report.call_args.kwargs['preserve_shared_junctions'], selected)

    def test_only_ignored_untracked_generated_cache_references_are_approved(self):
        self.write('web/package.json', '{}')
        self.write('web/package-lock.json', '{}')
        link = self.root/'web/node_modules'
        link.mkdir()
        names = ['web/package.json', 'web/package-lock.json']
        with self.approvals(names), mock.patch.object(tool, 'linked', side_effect=lambda p: Path(p) == link):
            self.assertEqual(tool.approved_junction_paths(self.root), ['web/node_modules'])
        with self.approvals(names, ignored=False), mock.patch.object(tool, 'linked', side_effect=lambda p: Path(p) == link):
            self.assertEqual(tool.approved_junction_paths(self.root), [])
        with self.approvals(names+['web/NODE_MODULES/source.txt']), mock.patch.object(tool, 'linked', side_effect=lambda p: Path(p) == link):
            self.assertEqual(tool.approved_junction_paths(self.root), [])

    def test_source_links_and_missing_owner_locks_are_not_approved(self):
        self.write('web/package.json', '{}')
        (self.root/'web/src').mkdir()
        link = self.root/'web/src'
        with self.approvals(['web/package.json']), mock.patch.object(tool, 'linked', side_effect=lambda p: Path(p) == link):
            self.assertEqual(tool.approved_junction_paths(self.root), [])
        link = self.root/'web/node_modules'
        link.mkdir()
        with self.approvals(['web/package.json']), mock.patch.object(tool, 'linked', side_effect=lambda p: Path(p) == link):
            self.assertEqual(tool.approved_junction_paths(self.root), [])

    def test_cargo_target_requires_tracked_manifest_and_lock(self):
        self.write('Cargo.toml', '[workspace]')
        self.write('Cargo.lock', 'version = 3')
        link = self.root/'target'
        link.mkdir()
        with self.approvals(['Cargo.toml', 'Cargo.lock']), mock.patch.object(tool, 'linked', side_effect=lambda p: Path(p) == link):
            self.assertEqual(tool.approved_junction_paths(self.root), ['target'])

    def test_shared_target_payload_is_not_traversed_or_counted(self):
        link = self.root/'node_modules'
        link.mkdir()
        (link/'shared.bin').write_bytes(b'x' * 1024 * 1024)
        source = self.write('source.txt', 's')
        seen, observe, finish = tool.junction_scan_collector(self.root, ['source.txt'])
        # discover's no-follow handle walk emits the link entry as a leaf; the
        # collector performs no second filesystem traversal or target query.
        with mock.patch('os.walk', side_effect=AssertionError('must not walk paths')):
            observe(self.root, self.root.stat(), False)
            observe(source, source.stat(), False)
            observe(link, link.lstat(), True)
        finish()
        self.assertFalse(seen['unreadable'])
        self.assertLess(seen['kb'], 32)
        with mock.patch.object(tool.native, 'is_windows', return_value=True), mock.patch.object(tool, 'linked', side_effect=lambda p: Path(p) == link):
            self.assertTrue(tool.scan(self.root)['unreadable'])

    def test_collector_keeps_secret_future_and_bare_git_guards_without_path_queries(self):
        seen, observe, finish = tool.junction_scan_collector(self.root, ['web/package-lock.json'])
        file_info = SimpleNamespace(st_mtime=0, st_ctime=0, st_size=1, st_mode=0o100644)
        dir_info = SimpleNamespace(st_mtime=0, st_ctime=0, st_size=0, st_mode=0o040755)
        with mock.patch('os.lstat', side_effect=AssertionError('metadata comes from the retained handle walk')):
            observe(self.root/'web/node_modules/pkg/.env', file_info, False)
            observe(self.root/'web/package-lock.json', file_info, False)
            observe(self.root/'evidence/.env', file_info, False)
            observe(self.root/'nested/HEAD', file_info, False)
            observe(self.root/'nested/objects', dir_info, False)
            observe(self.root/'nested/refs', dir_info, False)
            future = SimpleNamespace(**vars(file_info))
            future.st_mtime = tool.now() + tool.DAY + 1
            observe(self.root/'future.txt', future, False)
        finish()
        self.assertEqual(seen['secrets'], [str(Path('evidence/.env'))])
        self.assertEqual(seen['git'], ['nested'])
        self.assertTrue(seen['future'])

    def test_collector_requires_cache_kind_specific_regular_lock_metadata(self):
        file_info = SimpleNamespace(st_mtime=0, st_ctime=0, st_size=1, st_mode=0o100644)
        empty = SimpleNamespace(**{**vars(file_info), 'st_size': 0})
        directory = SimpleNamespace(**{**vars(file_info), 'st_mode': 0o040755})
        cases = [
            ('requirements.txt', 'node_modules/pkg/.env', file_info, False),
            ('uv.lock', 'node_modules/pkg/.env', file_info, False),
            ('package-lock.json', '.venv/Lib/site-packages/pkg/.env', file_info, False),
            ('package-lock.json', 'node_modules/pkg/.env', empty, False),
            ('package-lock.json', 'node_modules/pkg/.env', directory, False),
            ('package-lock.json', 'node_modules/pkg/.env', file_info, True),
            ('requirements.txt', '.venv/Lib/site-packages/pkg/.env', file_info, True),
            ('uv.lock', 'venv/Lib/site-packages/pkg/.env', file_info, True),
        ]
        for lock, secret, info, exempt in cases:
            for lock_first in (False, True):
                with self.subTest(lock=lock, secret=secret, size=info.st_size, mode=info.st_mode, lock_first=lock_first):
                    seen, observe, finish = tool.junction_scan_collector(self.root, ['web/' + lock])
                    events = [(self.root/'web'/lock, info), (self.root/'web'/secret, file_info)]
                    for path, metadata in (events if lock_first else reversed(events)):
                        observe(path, metadata, False)
                    finish()
                    self.assertEqual(seen['secrets'], [] if exempt else [str(Path('web')/secret)])

    def test_collector_does_not_trust_absent_untracked_or_junction_lock(self):
        info = SimpleNamespace(st_mtime=0, st_ctime=0, st_size=1, st_mode=0o100644)
        for tracked, observed, junction in ((True, False, False), (False, True, False), (True, True, True)):
            with self.subTest(tracked=tracked, observed=observed, junction=junction):
                names = ['web/package-lock.json'] if tracked else []
                seen, observe, finish = tool.junction_scan_collector(self.root, names)
                observe(self.root/'web/node_modules/pkg/.env', info, False)
                if observed:
                    observe(self.root/'web/package-lock.json', info, junction)
                finish()
                self.assertEqual(seen['secrets'], [str(Path('web/node_modules/pkg/.env'))])

    def test_raw_registration_binding_is_preserved_and_reparse_ancestor_is_rejected(self):
        raw = str(self.base/'alias'/'task')
        output = f'worktree {raw}\0HEAD {"a" * 40}\0branch refs/heads/fix/example\0\0'
        with mock.patch.object(tool, 'git', return_value=subprocess.CompletedProcess([], 0, output, '')), mock.patch.object(tool, 'canon', return_value=str(self.root)):
            parsed = tool.worktrees(self.base)
        self.assertEqual(parsed[0]['registration_path'], raw)
        ctx = object.__new__(tool.Context)
        ctx.preserve_shared_junctions, ctx.only = True, 'worktree'
        ctx.memo = {'junction_plans': {}, 'scans': {}}
        ctx.all_repos = lambda: [str(self.base)]
        ctx.trees = lambda _repo: parsed
        with mock.patch.object(tool.native, 'is_windows', return_value=True), mock.patch.object(tool, 'linked', side_effect=lambda p: Path(p) == self.base/'alias'), mock.patch.object(tool, 'junction_engine') as engine:
            with self.assertRaisesRegex(tool.Refused, 'root or ancestor'):
                ctx.junction_plan(str(self.root))
            engine.assert_not_called()

    def test_plan_preparation_is_read_only_and_default_does_not_open_engine(self):
        self.write('source.txt', 'preserved')
        plan = {'root': str(self.root), 'entries': []}
        ctx = object.__new__(tool.Context)
        ctx.preserve_shared_junctions, ctx.only = False, 'worktree'
        ctx.memo = {'junction_plans': {}, 'scans': {}}
        with mock.patch.object(tool, 'junction_engine') as engine:
            self.assertIsNone(ctx.junction_plan(str(self.root)))
            engine.assert_not_called()
        ctx.preserve_shared_junctions = True
        ctx.all_repos = lambda: [str(self.base)]
        ctx.trees = lambda _repo: [{'worktree': str(self.root), 'registration_path': str(self.root)}]
        ctx.inspection = lambda *_args: {'budgets': {'seconds_limit': 60, 'entry_limit': 400000}}
        engine = SimpleNamespace(discover=mock.Mock(return_value=plan), escrow=mock.Mock(), JunctionRefused=ValueError)
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch.object(tool.native, 'is_windows', return_value=True), mock.patch.object(tool, 'junction_engine', return_value=engine), mock.patch.object(tool, 'approved_junction_paths', return_value=[]), mock.patch.object(tool, 'junction_source_index', return_value=frozenset(['source.txt'])):
            self.assertEqual(ctx.junction_plan(str(self.root)), plan)
        engine.escrow.assert_not_called()
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_fresh_plan_change_skips_action(self):
        spec = {'class': 'worktree', 'path': str(self.root), 'repo': str(self.base), 'tree_path': str(self.root)}
        planned = {'class': 'worktree', 'path': str(self.root), 'status': 'eligible', 'reason': 'finished', 'trash_kb': 1, 'junction_plan': {'entries': [{'identity': 1}]}}
        current = {**planned, 'junction_plan': {'entries': [{'identity': 2}]}}
        ctx = SimpleNamespace(memo={'usage_details': {}})
        capacity = SimpleNamespace(change=lambda *_args: {})
        with mock.patch.object(tool.native, 'is_windows', return_value=True), mock.patch.object(tool, 'verify_workspace', return_value=self.base), mock.patch.object(tool, 'capacity_snapshot', return_value={}), mock.patch.object(tool, 'capacity_engine', return_value=capacity), mock.patch.object(tool, 'Context', return_value=ctx), mock.patch.object(tool, 'collect', return_value=[(spec, planned)]), mock.patch.object(tool, 'evaluate', return_value=current), mock.patch.object(tool, 'act') as act, mock.patch.object(tool, 'locked', return_value=contextlib.nullcontext()):
            result = tool.apply(self.base, 'origin/main', only='worktree', preserve_shared_junctions=True)
        self.assertEqual(result['results'][0]['outcome'], 'skipped')
        act.assert_not_called()
        self.assertTrue(self.root.exists())


class RetirementSequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='orbyn-junction-sequence-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base/'source'; self.root.mkdir()
        (self.root/'source.txt').write_text('preserved', encoding='utf-8')
        self.archive = self.base/'archive'; self.archive.mkdir()
        (self.archive/'anchor.txt').write_text('synthetic existing archive anchor')
        self.gitdir = self.base/'gitdir'; (self.gitdir/'logs').mkdir(parents=True)
        (self.gitdir/'logs/HEAD').write_text('synthetic reflog', encoding='utf-8')
        self.trace = []
        self.plan = {'root': str(self.root), 'entries': [{'relative': 'web/node_modules', 'identity': [1, 2]}]}
        self.bin = self.base/'fixture-bin'
        self.removes = []

    def git(self, _repo, *args, **_kwargs):
        if args == ('rev-parse', '--absolute-git-dir'):
            output = str(self.gitdir)
        elif args == ('branch', '--show-current'):
            output = 'fix/fixture'
        elif args == ('rev-parse', 'HEAD'):
            output = 'a' * 40
        elif args[:2] == ('worktree', 'remove'):
            self.trace.append('clear-registration'); self.removes.append(args)
            self.assertFalse(self.root.exists())
            self.assertIn('commit', self.trace)
            output = ''
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, output, '')

    def engine(self, *, commit_error=False, changed=False):
        owner = self
        class JunctionRefused(Exception):
            pass
        class Tx:
            state = 'staged'
            def commit(self):
                if commit_error:
                    raise JunctionRefused('synthetic commit failure')
                owner.trace.append('commit'); self.state = 'committed'
            def summary(self):
                return {'state': self.state, 'journal': 'synthetic-journal', 'entries': []}
        def escrow(_plan, entry, _native, **options):
            class Transaction(Tx):
                def __enter__(self):
                    data = json.loads((Path(entry)/options['record_name']).read_text(encoding='utf-8'))
                    owner.assertEqual(data['junctions']['state'], 'planned')
                    owner.assertEqual(data['registration'], 'pending')
                    owner.trace.append('stage')
                    return self
                def __exit__(self, kind, value, traceback):
                    if kind is not None:
                        self.state = 'rolled-back' if owner.root.exists() else 'retained-source-gone'
                        owner.trace.append('rollback')
                    return False
            return Transaction()
        plan = {'entries': [{'changed': True}]} if changed else self.plan
        return SimpleNamespace(JunctionRefused=JunctionRefused, discover=lambda *_a, **_k: plan, escrow=escrow, preflight=lambda *_a: contextlib.nullcontext())

    def run_discard(self, engine, trash):
        with mock.patch.object(tool, 'git', side_effect=self.git), mock.patch.object(tool, 'ignored_names', return_value=['web/node_modules/']), mock.patch.object(tool, 'approved_junction_paths', return_value=['web/node_modules']), mock.patch.object(tool, 'junction_engine', return_value=engine), mock.patch.object(tool, 'trash', side_effect=trash):
            return tool.discard(self.base, self.root, self.archive, 'finished', junction_plan=self.plan, usage_limits={'max_seconds': 30, 'max_entries': 1000})

    def test_record_precedes_stage_and_registration_follows_confirmed_recycle(self):
        def trash(_paths, **_kwargs):
            self.trace.append('trash'); self.root.rename(self.bin)
        result = self.run_discard(self.engine(), trash)
        self.assertEqual(self.trace, ['stage', 'trash', 'commit', 'clear-registration'])
        self.assertEqual(result['registration'], 'cleared')
        self.assertEqual(result['junctions']['state'], 'committed')
        self.assertEqual((self.bin/'source.txt').read_text(), 'preserved')

    def test_refused_recycle_rolls_back_and_keeps_registration(self):
        def trash(_paths, **_kwargs):
            raise tool.Refused('synthetic busy')
        with self.assertRaises(tool.Refused):
            self.run_discard(self.engine(), trash)
        self.assertEqual(self.trace, ['stage', 'rollback'])
        self.assertFalse(self.removes)
        record = json.loads(next(self.archive.glob('*RETIRED.json')).read_text())
        self.assertTrue(record['source_present'])
        self.assertEqual(record['junctions']['state'], 'rolled-back')
        self.assertEqual(record['registration'], 'pending')

    def test_partial_move_and_journal_failure_do_not_clear_registration(self):
        def trash(_paths, **_kwargs):
            self.root.rename(self.bin)
        with self.assertRaises(tool.Refused):
            self.run_discard(self.engine(commit_error=True), trash)
        self.assertFalse(self.removes)
        record = json.loads(next(self.archive.glob('*RETIRED.json')).read_text())
        self.assertFalse(record['source_present'])
        self.assertEqual(record['junctions']['state'], 'retained-source-gone')
        self.assertEqual(record['registration'], 'pending')

    def test_plan_change_before_staging_keeps_source_and_registration(self):
        with self.assertRaises(tool.Refused):
            self.run_discard(self.engine(changed=True), lambda *_a, **_k: self.fail('must not recycle'))
        self.assertEqual(self.trace, [])
        self.assertFalse(self.removes)
        self.assertTrue(self.root.exists())

    def test_partial_staging_enter_failure_records_its_recovery_state(self):
        engine = self.engine()
        class EnterFailure:
            def __enter__(self):
                raise engine.JunctionRefused('synthetic partial staging rollback conflict')
            def __exit__(self, *_args):
                raise AssertionError('failed __enter__ does not invoke __exit__')
            def summary(self):
                return {'state': 'rollback-incomplete', 'journal': 'synthetic retained journal', 'entries': []}
        engine.escrow = lambda *_args, **_kwargs: EnterFailure()
        with self.assertRaises(tool.Refused):
            self.run_discard(engine, lambda *_a, **_k: self.fail('must not recycle'))
        self.assertFalse(self.removes)
        record = json.loads(next(self.archive.glob('*RETIRED.json')).read_text())
        self.assertTrue(record['source_present'])
        self.assertEqual(record['junctions']['state'], 'rollback-incomplete')
        self.assertEqual(record['registration'], 'pending')

    def test_recycle_without_source_disappearance_is_not_success(self):
        with self.assertRaises(tool.Refused):
            self.run_discard(self.engine(), lambda *_a, **_k: None)
        self.assertFalse(self.removes)
        self.assertTrue(self.root.exists())


@unittest.skipUnless(os.name == 'nt', 'native Windows junction integration')
class NativeGitJunctionIntegration(unittest.TestCase):
    def test_exact_retire_preserves_target_and_reference_with_real_git(self):
        # Library identity/settings and provider lookup are synthetic fixture
        # boundaries. Git ownership/landing, native junction inspection/escrow,
        # source move and registration clearing are real disposable operations.
        with tempfile.TemporaryDirectory(prefix='orbyn-junction-git-') as directory:
            base = Path(directory).resolve()
            ws = base/'library'; primary = ws/'repo/app'; primary.mkdir(parents=True)
            source = ws/'repo/app-wt/done'; source.parent.mkdir()
            target = base/'shared/node_modules'; target.mkdir(parents=True)
            canary = target/'preserve.txt'; canary.write_bytes(b'synthetic shared target')
            moved = base/'fixture-bin'
            def git(path, *args):
                result = subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-C', str(path), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            git(primary, 'init', '-q', '-b', 'main')
            git(primary, 'config', 'user.name', 'Fixture')
            git(primary, 'config', 'user.email', 'fixture@example.test')
            (primary/'web').mkdir()
            (primary/'.gitignore').write_text('web/node_modules/\n', encoding='utf-8')
            (primary/'web/package.json').write_text('{"name":"synthetic"}', encoding='utf-8')
            (primary/'web/package-lock.json').write_text('{"lockfileVersion":3}', encoding='utf-8')
            git(primary, 'add', '.'); git(primary, 'commit', '-qm', 'fixture base')
            git(primary, 'worktree', 'add', '-qb', 'fix/done', str(source), 'HEAD')
            (source/'source.txt').write_text('landed synthetic source', encoding='utf-8')
            git(source, 'add', '.'); git(source, 'commit', '-qm', 'fixture task')
            head = git(source, 'rev-parse', 'HEAD').strip()
            git(primary, 'merge', '--ff-only', 'fix/done')
            git(primary, 'update-ref', 'refs/remotes/origin/main', head)
            link = source/'web/node_modules'
            made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True, text=True, timeout=15)
            self.assertEqual(made.returncode, 0, made.stderr)
            values = {'enabled': True, 'worktree_idle_days': 1, 'cache_idle_days': 7,
                      'scan_depth': 'standard', 'cache_profile': 'legacy', 'output_profile': 'legacy'}
            harness = SimpleNamespace(Invalid=ValueError, CannotRun=ValueError,
                resolve=lambda *_args, **_kwargs: {'cleanup': {k: {'value': v, 'source': 'synthetic fixture'} for k, v in values.items()}})
            def trash(paths, **_kwargs):
                self.assertEqual(paths, [source])
                self.assertFalse(os.path.lexists(link), 'junction must be outside Shell/source before moving source')
                source.rename(moved)
            with mock.patch.object(tool, 'verify_workspace', return_value=ws), mock.patch.object(tool, 'load_harness', return_value=harness), mock.patch.object(tool, 'open_prs', return_value=[]), mock.patch.object(tool, 'trash', side_effect=trash):
                before_target = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()}
                with self.assertRaises(tool.Refused):
                    tool.retire(str(primary), str(source), 'origin/main', target/'must-not-create', workspace=ws, preserve_shared_junctions=True)
                self.assertEqual(sorted(p.name for p in target.iterdir()), ['preserve.txt'])
                self.assertEqual(before_target, {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()})
                self.assertTrue(tool.native.is_reparse(link))
                self.assertTrue(source.exists())
                self.assertIn(str(source).replace('\\', '/'), git(primary, 'worktree', 'list', '--porcelain').replace('\\', '/'))
                archive = ws/'output/worktree-archive'
                with self.assertRaises(tool.Refused):
                    tool.retire(str(primary), str(source), 'origin/main', archive, workspace=ws, preserve_shared_junctions=True)
                self.assertFalse(archive.exists(), 'missing archive must remain absent')
                archive.mkdir(parents=True)
                with self.assertRaises(tool.Refused):
                    tool.retire(str(primary), str(source), 'origin/main', archive, workspace=ws, preserve_shared_junctions=True)
                self.assertEqual(list(archive.iterdir()), [], 'empty archive must remain empty')
                (archive/'existing-anchor.txt').write_text('ordinary existing archive anchor')
                result = tool.retire(str(primary), str(source), 'origin/main', archive, workspace=ws, preserve_shared_junctions=True)
            self.assertEqual(result['registration'], 'cleared')
            self.assertEqual(result['junctions']['state'], 'committed')
            self.assertFalse(source.exists())
            self.assertEqual(canary.read_bytes(), b'synthetic shared target')
            retained = Path(result['junctions']['entries'][0]['archive'])
            self.assertTrue(tool.native.is_reparse(retained))
            self.assertEqual(git(primary, 'rev-parse', 'refs/heads/fix/done').strip(), head)
            self.assertNotIn(str(source).replace('\\', '/'), git(primary, 'worktree', 'list', '--porcelain').replace('\\', '/'))
            self.assertEqual((moved/'source.txt').read_text(), 'landed synthetic source')


if __name__ == '__main__':
    unittest.main()
