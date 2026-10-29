"""One-command deployment decisions without installing packages or changing live data."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy


class DeploymentTests(unittest.TestCase):
    def test_build_only_never_reads_configuration_or_opens_database(self):
        with patch.object(deploy.launcher, 'prepare', return_value=Path('prepared-python')) as prepare, \
             patch.object(deploy, 'prepared_main', side_effect=AssertionError('Unexpected runtime/database call')), \
             patch.object(deploy.launcher, 'create_configuration', side_effect=AssertionError('Unexpected configuration prompt')):
            self.assertEqual(deploy.main(['--build-only']), 0)
            prepare.assert_called_once_with(deploy.ROOT)

    def test_one_command_reexecutes_prepared_environment_without_old_menu(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / '.env').write_text('existing-private-configuration')
            completed = subprocess.CompletedProcess([], 0)
            with patch.object(deploy, 'ROOT', root), \
                 patch.object(deploy.launcher, 'prepare', return_value=root / '.venv/python'), \
                 patch.object(deploy.subprocess, 'run', return_value=completed) as run, \
                 patch.object(deploy.launcher, 'create_configuration', side_effect=AssertionError('Existing file must be preserved')), \
                 patch.object(deploy.launcher, 'menu', side_effect=AssertionError('The old menu must not be used')):
                self.assertEqual(deploy.main(['--non-interactive']), 0)
            self.assertEqual((root / '.env').read_text(), 'existing-private-configuration')
            self.assertEqual(run.call_args.args[0], [str(root / '.venv/python'), str(root / 'deploy.py'), '--prepared', '--non-interactive'])

    def test_unconfigured_automation_stops_without_wizard_or_server(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(deploy, 'ROOT', Path(folder)), \
             patch.object(deploy.launcher, 'prepare', return_value=Path('prepared-python')), \
             patch.object(deploy.subprocess, 'run') as run, \
             patch.object(deploy.launcher, 'create_configuration') as wizard:
            self.assertEqual(deploy.main(['--non-interactive']), 1)
            wizard.assert_not_called(); run.assert_not_called()

    def test_check_initializes_without_admin_creation_or_server_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'frontend/dist').mkdir(parents=True)
            (root / 'frontend/dist/index.html').write_text('built')
            accounts = MagicMock()
            with patch.object(deploy, 'validate_configuration', return_value='private-dsn'), \
                 patch.object(deploy, 'initialize_database', return_value=(accounts, False)), \
                 patch.object(deploy.launcher, 'run') as run, patch.object(deploy, 'ensure_admin') as ensure:
                self.assertEqual(deploy.prepared_main(deploy.arguments(['--check']), root), 0)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], [sys.executable, 'server/app.py', '--check-db'])
                ensure.assert_not_called(); accounts.initialize_admin.assert_not_called()

    def test_default_runtime_preserves_existing_admin_and_starts_after_schema_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'frontend/dist').mkdir(parents=True)
            (root / 'frontend/dist/index.html').write_text('built'); (root / '.env').write_text('private')
            accounts = MagicMock()
            with patch.object(deploy, 'validate_configuration', return_value='private-dsn'), \
                 patch.object(deploy, 'initialize_database', return_value=(accounts, True)), \
                 patch.object(deploy.launcher, 'run') as run, \
                 patch.object(deploy.getpass, 'getpass', side_effect=AssertionError('Existing admin must not prompt')):
                self.assertEqual(deploy.prepared_main(deploy.arguments([]), root), 0)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0].args[0][-1], '--check-db')
                self.assertEqual(run.call_args_list[1].args[0][-2:], ['--env-file', '.env'])
                accounts.initialize_admin.assert_not_called()

    def test_hidden_admin_bootstrap_and_noninteractive_failure(self):
        accounts = MagicMock()
        with patch.dict(os.environ, {'LEDGER_ADMIN_USERNAME': '', 'LEDGER_ADMIN_PASSWORD': ''}), \
             patch.object(deploy.sys.stdin, 'isatty', return_value=True), \
             patch.object(deploy.launcher, 'ask', return_value='first_admin'), \
             patch.object(deploy.getpass, 'getpass', return_value='long-private-password'):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                deploy.ensure_admin(accounts, False)
            accounts.initialize_admin.assert_called_once_with('first_admin', 'long-private-password')
            self.assertNotIn('long-private-password', output.getvalue())
            with self.assertRaises(ValueError):
                deploy.ensure_admin(accounts, False, non_interactive=True)

    def test_explicit_environment_bootstrap_never_puts_password_in_command(self):
        accounts = MagicMock()
        with patch.dict(os.environ, {'LEDGER_ADMIN_USERNAME': 'configured_admin', 'LEDGER_ADMIN_PASSWORD': 'long-private-password'}), \
             patch.object(deploy.getpass, 'getpass', side_effect=AssertionError('Unexpected prompt')):
            deploy.ensure_admin(accounts, False, non_interactive=True)
        accounts.initialize_admin.assert_called_once_with('configured_admin', 'long-private-password')

    def test_configuration_rejects_original_database_and_insecure_production(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = {'LEDGER_DATABASE_URL': 'postgresql://unused:unused@127.0.0.1:1/blockchain_ledger',
                    'LEDGER_PUBLIC_ORIGIN': 'http://127.0.0.1:8000', 'LEDGER_DEV_MODE': 'true',
                    'LEDGER_SECURE_COOKIES': 'false'}
            with patch.dict(os.environ, base, clear=True), self.assertRaisesRegex(ValueError, 'separate chainguard'):
                deploy.validate_configuration(root)
            base.update(LEDGER_DATABASE_URL='postgresql://unused:unused@127.0.0.1:1/chainguard', LEDGER_DEV_MODE='false')
            with patch.dict(os.environ, base, clear=True), self.assertRaisesRegex(ValueError, 'Production requires'):
                deploy.validate_configuration(root)
            base.update(LEDGER_DEV_MODE='true', LEDGER_BIND='0.0.0.0')
            with patch.dict(os.environ, base, clear=True), self.assertRaisesRegex(ValueError, 'loopback'):
                deploy.validate_configuration(root)

    def test_connection_failure_output_does_not_leak_driver_secret(self):
        secret_error = RuntimeError('password authentication failed for postgresql://person:VERY_PRIVATE_PASSWORD@host/db')
        message = deploy.postgres_failure(secret_error)
        self.assertIn('password authentication failed', message)
        self.assertNotIn('VERY_PRIVATE_PASSWORD', message)
        self.assertNotIn('postgresql://', message)

    def test_database_creation_is_explicit_and_does_not_access_original(self):
        sys.path.insert(0, str(deploy.ROOT / 'server'))
        import psycopg
        with patch.object(psycopg, 'connect') as connect:
            with self.assertRaises(ValueError):
                deploy.initialize_database('postgresql://unused:unused@127.0.0.1:1/blockchain_ledger', True)
            connect.assert_not_called()
        missing = psycopg.errors.InvalidCatalogName('test missing database')
        with patch.object(psycopg, 'connect', side_effect=missing) as connect:
            with self.assertRaisesRegex(ValueError, 'explicitly use --create-database'):
                deploy.initialize_database('postgresql://unused:unused@127.0.0.1:1/chainguard')
            self.assertEqual(connect.call_count, 1)

    def test_create_database_requires_configured_role_permission(self):
        sys.path.insert(0, str(deploy.ROOT / 'server'))
        import psycopg
        maintenance = MagicMock()
        maintenance.__enter__.return_value = maintenance
        maintenance.execute.return_value.fetchone.return_value = (False,)
        with patch.object(psycopg, 'connect', side_effect=[psycopg.errors.InvalidCatalogName('test missing'), maintenance]) as connect:
            with self.assertRaisesRegex(ValueError, 'cannot create databases'):
                deploy.initialize_database('postgresql://unused:unused@127.0.0.1:1/chainguard', True)
            self.assertEqual(connect.call_count, 2)
            self.assertIn('dbname=postgres', connect.call_args.args[0])
            self.assertEqual(maintenance.execute.call_count, 1)

    def test_missing_database_startup_operational_error_is_classified_safely(self):
        sys.path.insert(0, str(deploy.ROOT / 'server'))
        import psycopg
        missing = psycopg.OperationalError('connection failed: FATAL: database "chainguard" does not exist')
        with patch.object(psycopg, 'connect', side_effect=missing):
            with self.assertRaisesRegex(ValueError, 'explicitly use --create-database'):
                deploy.initialize_database('postgresql://unused:unused@127.0.0.1:1/chainguard')

    def test_frontend_missing_stops_before_database_mutation(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(deploy, 'initialize_database') as initialize:
            with self.assertRaisesRegex(ValueError, 'Frontend build is missing'):
                deploy.prepared_main(deploy.arguments(['--skip-build']), Path(folder))
            initialize.assert_not_called()


if __name__ == '__main__':
    unittest.main()
