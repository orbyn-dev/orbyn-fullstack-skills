"""Finished-output publication proofs and native effects in disposable repositories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from test_worktree_retire import CleanupCase, tool, ORIGINAL


class OutputCLIContracts(CleanupCase):
    def sh(self, path, *args):
        try:
            return super().sh(path, *args)
        except subprocess.CalledProcessError as error:
            self.fail(error.stderr)

    def setUp(self):
        super().setUp()
        self.write(self.app/'Cargo.toml', '[package]\nname="fixture"\nversion="0.1.0"\n')
        self.sh(self.app, 'add', 'Cargo.toml')
        self.commit(self.app, 'tracked producer source')
        self.sh(self.app, 'push', '-q', 'origin', 'HEAD:main')
        self.sh(self.app, 'fetch', '-q', 'origin')
        self.head = self.sh(self.app, 'rev-parse', 'HEAD').strip()
        self.owner = self.lib/'output/finished-fixture'
        self.target = self.owner/'cargo-target'
        self.unit = self.target/'debug/deps'
        self.write(self.unit/'fixture.rlib', 'generated cache')
        self.write(self.target/'CACHEDIR.TAG', tool.artifact_engine().CACHE_SIGNATURE+'\n')
        self.write(self.target/'.rustc_info.json', json.dumps({'rustc': 'rustc 1.99.1 (fixture 2026-10-13)'}))
        self.write(self.owner/'evidence.txt', 'required failed/successful evidence')
        self.write(self.owner/'source-snapshot/.git/HEAD', 'valuable source checkpoint')
        self.write(self.owner/'avd/userdata.img', 'emulator data')
        self.record = tool.output_engine().plan_path(self.owner, self.lib)
        self.plan = {'schema_version': 1, 'task': str(self.owner), 'repository': str(self.app),
                     'completion': {'provider': 'github', 'number': 17, 'head': self.head, 'target': 'main'},
                     'keep': ['evidence.txt', 'source-snapshot', 'avd'],
                     'units': [{'path': 'cargo-target/debug/deps', 'target': 'cargo-target',
                                'kind': 'cargo-deps', 'source': '.'}]}
        self.write(self.record, json.dumps(self.plan))
        self.clock = 20
        self.native_usage, self.native_dirs = tool.artifact_usage, tool.native.probe_dirs
        for target, name, value in ((tool, 'artifact_usage', None), (tool.native, 'probe_dirs', set())):
            patcher = mock.patch.object(target, name, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch.object(tool, 'output_completion', side_effect=lambda repo, completion, ctx:
            ({**completion, 'repository': repo, 'url': 'https://example.test/pull/17', 'merged_at': '2026-01-01T00:00:00Z'}, None))
        patcher.start(); self.addCleanup(patcher.stop)

    def options(self, **extra):
        return {'only': 'output', 'output_profile': 'aggressive', 'within': str(self.owner), **extra}

    def test_named_profile_never_retires_an_unregistered_output_folder(self):
        self.record.unlink()
        report = self.report(**self.options())
        self.assertFalse(any(row['status'] == 'eligible' for row in report['items']), report)
        self.assertFalse(self.apply(**self.options())['results'])
        self.assertTrue(self.unit.exists())

    def test_dry_run_preserves_source_evidence_and_emulator_data(self):
        before = self.snapshot()
        result = self.apply(dry_run=True, **self.options())
        self.assertEqual([r['outcome'] for r in result['results']], ['would trash'], result)
        self.assertEqual(before, self.snapshot())
        self.assertNotIn('receipt', result)

    def test_apply_moves_only_proven_child_and_records_recovery(self):
        before = self.sh(self.app, 'worktree', 'list', '--porcelain')
        result = self.apply(**self.options())
        self.assertEqual([r['outcome'] for r in result['results']], ['trashed'], result)
        self.assertFalse(self.unit.exists())
        for path in ('evidence.txt', 'source-snapshot/.git/HEAD', 'avd/userdata.img'):
            self.assertTrue((self.owner/path).is_file(), path)
        self.assertEqual(before, self.sh(self.app, 'worktree', 'list', '--porcelain'))
        receipt = json.loads(Path(result['receipt']).read_text())
        self.assertEqual(receipt['items'][0]['outcome'], 'trashed')
        self.assertEqual(receipt['items'][0]['record_digest'], tool.output_engine().discover(
            self.owner, tool.output_probes(tool.Context(self.lib, 'origin/main', output_profile='aggressive')))[0]['record_digest'])

    def test_receipt_failure_prevents_any_move(self):
        with mock.patch.object(tool, 'write_cleanup_receipt', side_effect=OSError('fixture full disk')):
            with self.assertRaisesRegex(tool.Refused, 'cannot write cleanup receipt'):
                self.apply(**self.options())
        self.assertFalse(self.trashed)
        self.assertTrue(self.unit.exists())

    def test_revoked_completion_between_plan_and_move_is_skipped(self):
        calls = 0
        def proof(repo, completion, ctx):
            nonlocal calls
            calls += 1
            return ({**completion}, None) if calls == 1 else (None, 'unmerged or unavailable')
        with mock.patch.object(tool, 'output_completion', side_effect=proof):
            result = self.apply(**self.options())
        self.assertEqual([r['outcome'] for r in result['results']], ['skipped'], result)
        self.assertTrue(self.unit.exists())

    def test_source_policy_can_disable_even_explicit_aggressive(self):
        self.write(self.app/'.orbyn/harness.json', json.dumps({'schema_version': 1, 'cleanup': {'output_profile': 'off'}}))
        self.assertFalse(self.apply(**self.options())['results'])
        self.assertTrue(self.unit.exists())

    def test_registered_linked_producer_obeys_primary_restrictions(self):
        producer = self.tree('output-producer')
        self.plan['repository'] = str(producer)
        self.write(self.record, json.dumps(self.plan))
        self.assertEqual(self.status(self.unit, self.report(**self.options())), 'eligible')
        self.write(self.app/'.orbyn/harness.json', json.dumps({'schema_version': 1, 'cleanup': {'output_profile': 'off'}}))
        self.assertFalse(self.apply(**self.options())['results'])
        self.assertTrue(self.unit.exists())

    def test_unregistered_source_copy_does_not_prove_ownership(self):
        self.plan['repository'] = str(self.owner/'source-snapshot')
        self.write(self.record, json.dumps(self.plan))
        self.assertFalse(self.apply(**self.options())['results'])
        self.assertTrue(self.unit.exists())

    def test_primary_and_linked_producer_git_operations_hold_output(self):
        producer = self.tree('output-producer')
        self.plan['repository'] = str(producer)
        self.write(self.record, json.dumps(self.plan))
        gitdir = Path(self.sh(producer, 'rev-parse', '--absolute-git-dir').strip())
        for directory, name in ((gitdir, 'index.lock'), (gitdir, 'MERGE_HEAD'),
                                (self.app/'.git', 'HEAD.lock'), (self.app/'.git', 'config.lock')):
            with self.subTest(name=name, directory=directory):
                marker = self.write(directory/name, 'busy')
                result = self.report(**self.options())
                self.assertFalse(any(row['status'] == 'eligible' for row in result['items']), result)
                self.assertFalse(self.apply(**self.options())['results'])
                self.assertTrue(self.unit.exists())
                marker.unlink()

    def test_git_operation_started_after_discovery_is_skipped(self):
        original = tool.collect
        marker = self.app/'.git/index.lock'
        def busy(ctx, finish=None):
            result = original(ctx, finish)
            self.write(marker, 'started after report')
            return result
        with mock.patch.object(tool, 'collect', side_effect=busy):
            result = self.apply(**self.options())
        marker.unlink()
        self.assertEqual([r['outcome'] for r in result['results']], ['skipped'], result)
        self.assertTrue(self.unit.exists())

    def test_cancellation_keeps_evidence_and_records_unattempted_units(self):
        with mock.patch.object(tool, 'act', side_effect=KeyboardInterrupt):
            result = self.apply(**self.options())
        self.assertTrue(result['cancelled'])
        receipt = json.loads(Path(result['receipt']).read_text())
        self.assertEqual(receipt['items'][0]['outcome'], 'cancelled')
        self.assertTrue(self.unit.exists())
        self.assertTrue((self.owner/'avd/userdata.img').is_file())

    def test_plan_mutation_between_preview_and_move_is_skipped(self):
        original = tool.collect
        def mutate(ctx, finish=None):
            result = original(ctx, finish)
            self.plan['keep'].append('cargo-target/debug/deps/fixture.rlib')
            self.write(self.record, json.dumps(self.plan))
            return result
        with mock.patch.object(tool, 'collect', side_effect=mutate):
            result = self.apply(**self.options())
        self.assertEqual([r['outcome'] for r in result['results']], ['skipped'], result)
        self.assertFalse(self.trashed)

    def test_new_records_and_receipts_are_excluded_from_discovery(self):
        result = self.report(**self.options(within=str(self.lib/'output')))
        self.assertFalse(any(Path(row['path']).name in ('cleanup-plans', 'cleanup-receipts') for row in result['items']))

    @unittest.skipUnless(os.name == 'nt' and os.environ.get('ORBYN_TEST_RECYCLE') == '1',
                         'opt-in disposable native output recycling')
    def test_native_disposable_output_recycling_preserves_kept_material(self):
        before = self.sh(self.app, 'worktree', 'list', '--porcelain')
        with mock.patch.object(tool, 'trash', ORIGINAL['trash']), mock.patch.object(
                tool, 'artifact_usage', self.native_usage), mock.patch.object(tool.native, 'probe_dirs', self.native_dirs):
            result = self.apply(**self.options())
        self.assertEqual([r['outcome'] for r in result['results']], ['trashed'], result)
        self.assertFalse(self.unit.exists())
        for path in ('evidence.txt', 'source-snapshot/.git/HEAD', 'avd/userdata.img'):
            self.assertTrue((self.owner/path).is_file(), path)
        self.assertEqual(before, self.sh(self.app, 'worktree', 'list', '--porcelain'))


class OutputProviderContracts(unittest.TestCase):
    def setUp(self):
        self.ws = Path(__file__).resolve().parents[1]
        self.ctx = mock.Mock(workspace=self.ws, memo={})
        self.head = 'a'*40
        self.request = {'provider': 'github', 'number': 17, 'head': self.head, 'target': 'main'}
        self.response = {'number': 17, 'state': 'closed', 'merged': True, 'merged_at': '2026-01-01T00:00:00Z',
                         'head': {'sha': self.head}, 'base': {'ref': 'main', 'repo': {'full_name': 'example/project'}},
                         'html_url': 'https://github.com/example/project/pull/17', 'merge_commit_sha': 'b'*40}

    def invoke(self, response, origin='https://github.com/example/project.git'):
        self.ctx.memo = {}
        def git(repo, *args, **kwargs):
            return mock.Mock(returncode=0, stdout=origin+'\n' if args[:2] == ('remote', 'get-url') else '')
        with mock.patch.object(tool, 'git', side_effect=git), mock.patch.object(tool.shutil, 'which', return_value=sys.executable), mock.patch.object(
                tool, 'run', return_value=mock.Mock(returncode=0, stdout=json.dumps(response))) as command:
            result = tool.output_completion(str(self.ws), self.request, self.ctx)
            return result, command

    def test_provider_origin_and_exact_merged_head_target_are_bound(self):
        (proof, why), command = self.invoke(self.response)
        self.assertIsNone(why)
        self.assertEqual(proof['head'], self.head)
        self.assertIn('repos/example/project/pulls/17', command.call_args.args)
        self.assertIn('github.com', command.call_args.args)

    def test_closed_unmerged_wrong_head_target_repo_or_number_is_held(self):
        for change in ({'merged': False}, {'merged_at': None}, {'head': {'sha': 'c'*40}},
                       {'base': {'ref': 'other', 'repo': {'full_name': 'example/project'}}},
                       {'base': {'ref': 'main', 'repo': {'full_name': 'other/project'}}}, {'number': 18},
                       {'html_url': 'https://other.example/example/project/pull/17'}):
            with self.subTest(change=change):
                (proof, why), _ = self.invoke({**self.response, **change})
                self.assertIsNone(proof)
                self.assertTrue(why)

    def test_origin_credentials_and_path_traversal_are_refused(self):
        for origin in ('https://user:secret@example.test/group/project.git', 'git@github.com:../project.git',
                       'file:///tmp/project', 'https://github.com/example/project.git?command=bad'):
            with self.subTest(origin=origin):
                (proof, why), command = self.invoke(self.response, origin)
                self.assertIsNone(proof)
                command.assert_not_called()

    def test_gitlab_readback_binds_nested_project_and_merge_target(self):
        self.request = {**self.request, 'provider': 'gitlab'}
        response = {'state': 'merged', 'merged_at': '2026-01-01T00:00:00Z', 'sha': self.head,
                    'target_branch': 'main', 'web_url': 'https://gitlab.example/group/sub/project/-/merge_requests/17',
                    'merge_commit_sha': 'b'*40}
        (proof, why), command = self.invoke(response, 'git@gitlab.example:group/sub/project.git')
        self.assertIsNone(why)
        self.assertEqual(proof['provider'], 'gitlab')
        self.assertIn('projects/group%2Fsub%2Fproject/merge_requests/17', command.call_args.args)
        (proof, why), _ = self.invoke({**response, 'state': 'closed'}, 'git@gitlab.example:group/sub/project.git')
        self.assertIsNone(proof)
