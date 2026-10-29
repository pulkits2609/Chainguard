"""Check CI/local runner configuration without connecting to any database."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_checks


TEST_DSN = 'postgresql://ci:unused-test-password@127.0.0.1:5432/chainguard_test'


class CheckRunnerTests(unittest.TestCase):
    def invoke(self, root, environment, arguments=(), return_codes=(0, 0)):
        results = [subprocess.CompletedProcess([], code) for code in return_codes]
        with patch.object(run_checks, 'ROOT', root), \
             patch.dict(os.environ, environment, clear=True), \
             patch.object(sys, 'argv', ['run_checks.py', *arguments]), \
             patch.object(run_checks.subprocess, 'run', side_effect=results) as execute:
            result = run_checks.main()
        return result, execute

    def test_ci_environment_does_not_require_local_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, execute = self.invoke(root, {'LEDGER_TEST_DATABASE_URL': TEST_DSN})
            self.assertEqual(result, 0)
            self.assertFalse((root / '.env').exists())
            self.assertEqual(execute.call_count, 2)
            self.assertEqual(execute.call_args_list[0].args[0][-3:], ['server', '-p', 'test_*.py'])
            self.assertEqual(execute.call_args_list[1].args[0][-3:], ['tools', '-p', 'test_*.py'])
            self.assertNotIn(TEST_DSN, str(execute.call_args_list))

    def test_local_env_is_supported_but_explicit_environment_takes_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('LEDGER_TEST_DATABASE_URL=postgresql://unused@127.0.0.1/blockchain_ledger\n', encoding='utf-8')
            result, execute = self.invoke(root, {'LEDGER_TEST_DATABASE_URL': TEST_DSN})
            self.assertEqual(result, 0)
            self.assertEqual(execute.call_count, 2)
            (root / '.env').write_text('LEDGER_TEST_DATABASE_URL=' + TEST_DSN + '\n', encoding='utf-8')
            result, _ = self.invoke(root, {})
            self.assertEqual(result, 0)

    def test_missing_unsafe_and_malformed_targets_fail_before_running_checks(self):
        values = [None, '', 'postgresql://unused@127.0.0.1/chainguard',
                  'postgresql://unused@127.0.0.1/blockchain_ledger',
                  'postgresql://unused@127.0.0.1/postgres',
                  'postgresql://private-password@[invalid:connection']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for dsn in values:
                with self.subTest(dsn_configured=bool(dsn)):
                    environment = {'LEDGER_TEST_DATABASE_URL': dsn} if dsn is not None else {}
                    with patch.object(run_checks, 'ROOT', root), \
                         patch.dict(os.environ, environment, clear=True), \
                         patch.object(sys, 'argv', ['run_checks.py']), \
                         patch.object(run_checks.subprocess, 'run') as execute:
                        with self.assertRaises(SystemExit) as raised:
                            run_checks.main()
                        self.assertNotIn('private-password', str(raised.exception))
                        execute.assert_not_called()

    def test_failure_stops_dependent_checks_and_preserves_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            result, execute = self.invoke(Path(directory), {'LEDGER_TEST_DATABASE_URL': TEST_DSN}, return_codes=(7,))
            self.assertEqual(result, 7)
            self.assertEqual(execute.call_count, 1)

    def test_frontend_flag_runs_tests_then_production_build(self):
        with tempfile.TemporaryDirectory() as directory:
            result, execute = self.invoke(Path(directory), {'LEDGER_TEST_DATABASE_URL': TEST_DSN},
                arguments=('--frontend', '--pattern', 'test_scope.py'), return_codes=(0, 0, 0, 0))
            self.assertEqual(result, 0)
            commands = [call.args[0] for call in execute.call_args_list]
            self.assertEqual(commands[0][-1], 'test_scope.py')
            self.assertEqual(commands[2][1:], ['--prefix', 'frontend', 'test'])
            self.assertEqual(commands[3][1:], ['--prefix', 'frontend', 'run', 'build'])


if __name__ == '__main__':
    unittest.main()
