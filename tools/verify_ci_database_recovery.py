"""Whole-public-schema recovery proof, exclusively for the disposable hosted CI DB.

Never invoke this against a laptop or deployment. External private keys are
retained deliberately; this does not simulate loss of the PostgreSQL cluster.
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import tempfile
import uuid

from verify_backup_restore import (ROOT, database_environment, private_file_access,
                                   run_database_tool, test_connection)
from accounts import Accounts
from checkpoints import CheckpointService, verify_checkpoint
from integrations import IntegrationService
from multi_snapshots import SnapshotService
from shipment_access import ShipmentAccessService
from store import Store


def require_disposable_ci(database_url, explicit=False, environment=None):
    environment = os.environ if environment is None else environment
    if (not explicit or environment.get('CI') != 'true' or environment.get('GITHUB_ACTIONS') != 'true'
            or platform.system() != 'Linux' or environment.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or not re.fullmatch(r'[1-9][0-9]*', environment.get('GITHUB_RUN_ID', ''))
            or not environment.get('GITHUB_WORKSPACE')
            or Path(environment.get('GITHUB_WORKSPACE', '')).resolve() != ROOT.resolve()):
        raise ValueError('Whole-schema recovery requires an explicitly disposable GitHub-hosted Linux CI checkout')
    parameters = test_connection(database_url)
    if parameters.get('user') != 'chainguard_ci' or parameters.get('host') not in ('127.0.0.1', 'localhost', '::1') or parameters.get('port', '5432') != '5432':
        raise ValueError('Whole-schema recovery requires the isolated loopback CI role and service')
    return parameters


def verify_ci_recovery(database_url, explicit=False):
    parameters = require_disposable_ci(database_url, explicit)
    dump, restore = shutil.which('pg_dump'), shutil.which('pg_restore')
    if not dump or not restore:
        raise RuntimeError('PostgreSQL client tools are required in the disposable CI job')
    environment = database_environment(parameters)
    store, accounts = Store(database_url), Accounts(database_url)
    with store.connect() as connection:
        actual = connection.execute('SELECT current_database() AS name,current_user AS role').fetchone()
        if actual != {'name': 'chainguard_test', 'role': 'chainguard_ci'}:
            raise ValueError('Connected database identity does not match the disposable CI target')
    user = ledger_id = key_id = None
    schema_available = True
    with tempfile.TemporaryDirectory(prefix='chainguard-ci-recovery-') as directory:
        temporary = Path(directory); temporary.chmod(0o700)
        try:
            password = secrets.token_urlsafe(24)
            user = accounts.register('recover_' + uuid.uuid4().hex[:16], password)
            session_token, _ = accounts.new_session(user['id'])
            created = store.create_ledger(user['id'], 'Hosted recovery fixture', '', template_id='shipment_events')
            ledger_id = created['ledger']['id']
            integrations = IntegrationService(store, temporary/'integration.key')
            connector = integrations.create(user, {'ledger_id': ledger_id, 'name': 'Recovery HMAC source', 'auth_method': 'hmac',
                'policy': {'requests_per_minute': 7, 'allowed_source_cidrs': ['127.0.0.1/32']}})
            integration_id, secret = connector['integration']['id'], connector['secret']
            event = {'event_id': 'hosted-recovery-event', 'shipment_id': 'CI-RECOVERY', 'event_type': 'TEST_FIXTURE',
                'source_system': 'DISPOSABLE_CI', 'event_time': datetime.datetime.now(datetime.timezone.utc).isoformat()}
            raw = json.dumps(event).encode()
            headers = {'X-ChainGuard-Signature': 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}
            original, duplicate = integrations.ingest(integration_id, raw, headers, '127.0.0.1')
            if duplicate:
                raise RuntimeError('Recovery fixture was unexpectedly duplicated')
            checkpoints = CheckpointService(store, temporary/'checkpoint.key')
            key_id = checkpoints.verification_key['key_id']
            checkpoint = checkpoints.create(ledger_id)
            shipments = ShipmentAccessService(store, checkpoints)
            grant = shipments.create(ledger_id, 'CI-RECOVERY', user=user)
            history, snapshot = store.read(ledger_id), SnapshotService(store, ledger_id).export()[0]
            private_paths = [temporary/'integration.key', temporary/'checkpoint.key']
            retained = [hashlib.sha256(path.read_bytes()).digest() for path in private_paths]
            if any(private_file_access(path)['result'] != 'PASS' for path in private_paths):
                raise RuntimeError('CI recovery keys are not owner-only')
            archive, listing = temporary/'public.dump', temporary/'archive.list'
            run_database_tool([dump, '--format=custom', '--schema=public', '--no-owner', '--no-privileges', '--file='+str(archive)], environment)
            archive.chmod(0o600)
            run_database_tool([restore, '--list', '--file='+str(listing), str(archive)], environment)
            if not re.search(r'\bSCHEMA\s+-\s+public\s', listing.read_text(encoding='utf-8')):
                raise RuntimeError('Recovery archive is missing its public schema definition')
            # All guards were checked before connecting; freeze and revalidate the exact target before destruction.
            require_disposable_ci(database_url, explicit)
            with store.connect() as connection:
                actual = connection.execute('SELECT current_database() AS name,current_user AS role').fetchone()
                if actual != {'name': 'chainguard_test', 'role': 'chainguard_ci'}:
                    raise ValueError('Disposable target identity changed before recovery')
                connection.execute('DROP SCHEMA public CASCADE')
            schema_available = False
            run_database_tool([restore, '--clean', '--if-exists', '--no-owner', '--no-privileges', '--single-transaction',
                '--exit-on-error', '--dbname=chainguard_test', str(archive)], environment)
            schema_available = True
            store, accounts = Store(database_url), Accounts(database_url)
            integrations = IntegrationService(store, temporary/'integration.key')
            checkpoints = CheckpointService(store, temporary/'checkpoint.key')
            shipments = ShipmentAccessService(store, checkpoints)
            restored = store.read(ledger_id)
            if restored['blocks'] != history['blocks'] or not restored['verification']['valid']:
                raise RuntimeError('Whole-schema restore changed ledger history')
            if accounts.login(user['username'], password)['id'] != user['id'] or not accounts.session(session_token):
                raise RuntimeError('Whole-schema restore lost account/session state')
            policy = integrations.policy(integration_id, user)
            if policy['requests_per_minute'] != 7 or policy['allowed_source_cidrs'] != ['127.0.0.1/32']:
                raise RuntimeError('Whole-schema restore lost integration policy')
            recovered, duplicate = integrations.ingest(integration_id, raw, headers, '127.0.0.1')
            if not duplicate or recovered != original:
                raise RuntimeError('Restored HMAC credential/idempotency did not recover the original record')
            if not SnapshotService(store, ledger_id).validate(snapshot)['valid'] or not verify_checkpoint(checkpoint, checkpoints.verification_key, ledger_id, restored)['history_verified']:
                raise RuntimeError('Restored snapshot/checkpoint evidence does not verify')
            if not shipments.events('CI-RECOVERY', grant['access_token'])['verification']['server_records_verified']:
                raise RuntimeError('Restored shipment capability no longer works')
            if retained != [hashlib.sha256(path.read_bytes()).digest() for path in private_paths]:
                raise RuntimeError('Retained signing/HMAC keys were changed during recovery')
            return {'result': 'PASS', 'scope': 'Entire application public schema restored in disposable hosted CI',
                'accounts_sessions_policies_hmac_idempotency_grants_restored': True,
                'history_snapshot_checkpoint_verified': True, 'retained_private_keys_unchanged': True,
                'roles_cluster_or_lost_private_keys_recovery_tested': False}
        finally:
            # Only this fixture is cleaned; unrelated restored CI records remain intact.
            if schema_available and ledger_id:
                store.delete_ledger(ledger_id, test_cleanup=True)
            if schema_available:
                with store.connect() as connection:
                    if user:
                        connection.execute('DELETE FROM users WHERE id=%s', (user['id'],))
                    if ledger_id:
                        connection.execute('DELETE FROM security_events WHERE ledger_id=%s', (ledger_id,))
                    if key_id:
                        connection.execute('DELETE FROM signing_key_bindings WHERE key_id=%s', (key_id,))
                        connection.execute('DELETE FROM verification_keys WHERE key_id=%s', (key_id,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disposable-ci', action='store_true')
    arguments = parser.parse_args()
    try:
        result = verify_ci_recovery(os.environ.get('LEDGER_TEST_DATABASE_URL', ''), arguments.disposable_ci)
    except Exception as error:
        result = {'result': 'REFUSED' if isinstance(error, ValueError) else 'FAILED',
                  'reason': str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__ + '; inspect disposable CI job privately'}
    print(json.dumps(result, indent=2))
    return 0 if result['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
