"""Compression routing stays separate from retirement and refreshes owner policy."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from test_tools import ROOT, module

helper = module('compression_cli_helpers', ROOT/'skills/orbyn-worktree/scripts/worktree_retire.py')


class CompressionRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.root = self.workspace/'target/debug/deps'
        self.policy = {'enabled': True, 'profile': 'aggressive', 'roots': [str(self.root)], 'source': 'owner'}

    def test_preview_never_enters_retirement_or_apply_or_writes_receipt(self):
        engine = mock.Mock()
        engine.report.return_value = {'roots': [], 'totals': {}}
        with mock.patch.object(helper, 'compression_policy', return_value=self.policy), \
             mock.patch.object(helper, 'compression_engine', return_value=engine), \
             mock.patch.object(helper, 'apply') as retiring:
            result = helper.compress(self.workspace, None, 'origin/main', dry_run=True)
        self.assertTrue(result['dry_run'])
        engine.apply.assert_not_called()
        retiring.assert_not_called()
        self.assertFalse((self.workspace/'output').exists())

    def test_callback_holds_changed_policy_before_effect(self):
        engine = mock.Mock()
        engine.report.return_value = {'roots': [], 'status': 'eligible'}
        def run(plan, **kwargs):
            self.assertFalse(kwargs['policy_check'](self.root, None))
            return {'status': 'held'}
        engine.apply.side_effect = run
        revoked = dict(self.policy, enabled=False)
        with mock.patch.object(helper, 'compression_policy', side_effect=[self.policy, revoked]), \
             mock.patch.object(helper, 'compression_engine', return_value=engine), \
             mock.patch.object(helper, 'compression_receipt_path', return_value=self.workspace/'receipt.json'):
            helper.compress(self.workspace, None, 'origin/main')

    def test_linked_owner_opt_out_holds_even_when_target_allows(self):
        engine = mock.Mock()
        def report(roots, **kwargs):
            self.assertFalse(kwargs['policy_check'](self.root, self.workspace/'other-repo'))
            return {'roots': []}
        engine.report.side_effect = report
        with mock.patch.object(helper, 'compression_policy', side_effect=[self.policy, self.policy, dict(self.policy, enabled=False)]), \
             mock.patch.object(helper, 'compression_engine', return_value=engine):
            helper.compress(self.workspace, None, 'origin/main', dry_run=True)

    def test_safe_profile_is_passed_unchanged_to_engine(self):
        engine = mock.Mock()
        with mock.patch.object(helper, 'compression_policy', return_value=dict(self.policy, profile='safe')), \
             mock.patch.object(helper, 'compression_engine', return_value=engine):
            helper.compress(self.workspace, None, 'origin/main', dry_run=True)
        self.assertEqual(engine.report.call_args.kwargs['profile'], 'safe')

    def test_cli_compress_never_routes_to_trash_apply(self):
        with mock.patch.object(helper, 'compress', return_value={'dry_run': True, 'plan': {}}) as compress, \
             mock.patch.object(helper, 'apply') as retiring, mock.patch('builtins.print'):
            status = helper.main(['compress', '--workspace', str(self.workspace), '--dry-run', '--cache-profile', 'aggressive'])
        self.assertEqual(status, 0)
        compress.assert_called_once()
        retiring.assert_not_called()


if __name__ == '__main__':
    unittest.main()
