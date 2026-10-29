"""Safety boundaries for the opt-in real PostgreSQL recovery drill."""
import os
import subprocess
import unittest
from unittest.mock import patch
import verify_backup_restore as recovery


class BackupRestoreSafetyTests(unittest.TestCase):
    def test_only_disposable_test_database_is_allowed(self):
        for name in ('chainguard','blockchain_ledger','postgres','other'):
            with self.assertRaises(ValueError):
                recovery.test_connection('postgresql://unused:unused@127.0.0.1/'+name)
        self.assertEqual(recovery.test_connection('postgresql://unused:unused@127.0.0.1/chainguard_test')['dbname'],'chainguard_test')

    def test_database_password_is_passed_only_in_child_environment(self):
        parameters={'dbname':'chainguard_test','user':'test_role','password':'private-test-value','host':'127.0.0.1'}
        environment=recovery.database_environment(parameters)
        with patch.object(recovery.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
            recovery.run_database_tool(['pg_dump','--format=custom'],environment)
        self.assertNotIn('private-test-value',str(run.call_args.args))
        self.assertEqual(run.call_args.kwargs['env']['PGPASSWORD'],'private-test-value')

    def test_tool_errors_do_not_expose_stderr_credentials(self):
        result=subprocess.CompletedProcess([],1,'','private connection data')
        with patch.object(recovery.subprocess,'run',return_value=result):
            with self.assertRaises(RuntimeError) as error:
                recovery.run_database_tool(['pg_restore'],{})
        self.assertNotIn('private connection data',str(error.exception))
