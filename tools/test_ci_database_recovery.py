"""The destructive recovery path must be unreachable outside explicit hosted CI."""
import os
import unittest
from unittest.mock import patch
import verify_ci_database_recovery as recovery


class CIRecoveryGuardTests(unittest.TestCase):
    DSN = 'postgresql://chainguard_ci:fake-only@127.0.0.1:5432/chainguard_test'

    def environment(self):
        return {'CI': 'true', 'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                'GITHUB_RUN_ID': '123456', 'GITHUB_WORKSPACE': str(recovery.ROOT)}

    def assert_refused_before_access(self, dsn, explicit, environment):
        with patch.dict(os.environ, environment, clear=True), patch.object(recovery.platform, 'system', return_value='Linux'), \
             patch.object(recovery, 'Store') as store, patch.object(recovery, 'Accounts') as accounts, patch.object(recovery.shutil, 'which') as tools, \
             patch.object(recovery, 'run_database_tool') as command:
            with self.assertRaises(ValueError):
                recovery.verify_ci_recovery(dsn, explicit)
            store.assert_not_called(); accounts.assert_not_called(); tools.assert_not_called(); command.assert_not_called()

    def test_every_explicit_hosted_context_guard_is_required_before_access(self):
        self.assert_refused_before_access(self.DSN, False, self.environment())
        for key in self.environment():
            environment = self.environment(); environment.pop(key)
            self.assert_refused_before_access(self.DSN, True, environment)
        environment = self.environment(); environment['RUNNER_ENVIRONMENT'] = 'self-hosted'
        self.assert_refused_before_access(self.DSN, True, environment)

    def test_application_original_other_roles_and_remote_targets_are_refused(self):
        for database in ('chainguard', 'blockchain_ledger', 'postgres', 'other'):
            self.assert_refused_before_access(self.DSN.replace('/chainguard_test', '/'+database), True, self.environment())
        self.assert_refused_before_access(self.DSN.replace('chainguard_ci:', 'other_role:'), True, self.environment())
        self.assert_refused_before_access(self.DSN.replace('127.0.0.1', 'database.example'), True, self.environment())

    def test_laptop_platform_is_refused_even_with_ci_flags(self):
        with patch.object(recovery.platform, 'system', return_value='Windows'):
            with self.assertRaises(ValueError):
                recovery.require_disposable_ci(self.DSN, True, self.environment())

    def test_valid_hosted_context_can_be_classified_without_connecting(self):
        with patch.object(recovery.platform, 'system', return_value='Linux'):
            self.assertEqual(recovery.require_disposable_ci(self.DSN, True, self.environment())['dbname'], 'chainguard_test')
