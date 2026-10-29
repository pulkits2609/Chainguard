"""New template ledgers cannot bypass managed integration policy with legacy tokens."""
import datetime
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from psycopg.conninfo import conninfo_to_dict

from integrations import IntegrationError, IntegrationService
from ledger import canonical_json
from ledger_templates import template_fields, templates
from store import Store, StoreForbidden, token_hash
import test_webapp as legacy_tests


DATABASE_URL = os.environ.get('LEDGER_TEST_DATABASE_URL')


def shipment_event():
    return {'shipment_id': 'POLICY-TEST', 'event_type': 'RESOURCE_CREATED',
            'event_time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'source_system': 'AUTOMATED_TEST'}


@unittest.skipUnless(DATABASE_URL, 'Isolated chainguard_test required')
class ManagedCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(conninfo_to_dict(DATABASE_URL).get('dbname'), 'chainguard_test')
        self.store = Store(DATABASE_URL)
        self.user = {'id': 'managed-test-' + uuid.uuid4().hex, 'is_admin': False}
        self.created = []
        self.temporary = tempfile.TemporaryDirectory()
        self.integrations = IntegrationService(self.store, Path(self.temporary.name) / 'hmac.key')

    def tearDown(self):
        for identifier in self.created:
            self.store.delete_ledger(identifier, test_cleanup=True)
        self.temporary.cleanup()

    def create(self, **kwargs):
        result = self.store.create_ledger(self.user['id'], 'Managed write fixture', '', **kwargs)
        self.created.append(result['ledger']['id'])
        return result

    def test_new_templates_never_issue_or_accept_legacy_secrets(self):
        for template in templates():
            with self.subTest(template=template['id']):
                result = self.create(template_id=template['id'])
                ledger = result['ledger']
                self.assertTrue(ledger['managed_writes_required'])
                self.assertNotIn('write_token', result)
                metadata = self.store.credentials(ledger['id'], self.user['id'])
                self.assertTrue(metadata['managed_writes_required'])
                self.assertNotIn('write_token', metadata)
                self.assertIn('managed integration', metadata['token_hint'].lower())
                with self.assertRaises(StoreForbidden):
                    self.store.rotate_token(ledger['id'], self.user['id'])
                # Even a matching stored token hash cannot provide an unmanaged path.
                legacy_token = 'legacy-test-' + uuid.uuid4().hex
                with self.store.connect() as connection:
                    connection.execute('UPDATE ledger_registry SET token_hash=%s WHERE id=%s',
                                       (token_hash(legacy_token), ledger['id']))
                before = canonical_json(self.store.read(ledger['id'])['blocks'])
                with self.assertRaises(StoreForbidden):
                    self.store.append(ledger['id'], legacy_token, {}, 'unmanaged')
                self.assertEqual(before, canonical_json(self.store.read(ledger['id'])['blocks']))

    def test_existing_generic_and_pre_remediation_template_credentials_remain_compatible(self):
        result = self.create(fields=template_fields('shipment_events'))
        identifier = result['ledger']['id']
        self.assertFalse(result['ledger']['managed_writes_required'])
        # Model a pre-remediation template ledger: existing metadata/default flag stays false.
        with self.store.connect() as connection:
            connection.execute("UPDATE ledger_registry SET template_id='shipment_events' WHERE id=%s", (identifier,))
        before = canonical_json(self.store.read(identifier)['blocks'][0])
        restarted = Store(DATABASE_URL)
        block, duplicate = restarted.append(identifier, result['write_token'], shipment_event(), 'legacy-event')
        self.assertFalse(duplicate)
        self.assertEqual(block['index'], 1)
        self.assertEqual(before, canonical_json(restarted.read(identifier)['blocks'][0]))
        rotated = restarted.rotate_token(identifier, self.user['id'])
        self.assertIn('write_token', rotated)
        self.assertFalse(restarted.get_ledger(identifier)['managed_writes_required'])

    def test_new_template_managed_write_policies_enforce_source_expiry_and_disable(self):
        result = self.create(template_id='shipment_events')
        identifier = result['ledger']['id']
        connector = self.integrations.create(self.user, {'ledger_id': identifier, 'name': 'Policy fixture',
            'auth_method': 'bearer', 'policy': {'allowed_source_cidrs': ['192.0.2.0/24']}})
        token, integration_id = connector['secret'], connector['integration']['id']
        with self.assertRaises(IntegrationError) as denied:
            self.integrations.append(token, identifier, shipment_event(), 'denied-source', '198.51.100.3')
        self.assertEqual(denied.exception.status, 403)
        self.integrations.append(token, identifier, shipment_event(), 'accepted', '192.0.2.3')
        self.integrations.update_policy(integration_id, self.user,
            {'credential_expires_at': '2000-01-01T00:00:00Z'})
        with self.assertRaises(IntegrationError) as expired:
            self.integrations.append(token, identifier, shipment_event(), 'expired', '192.0.2.3')
        self.assertEqual(expired.exception.status, 403)
        self.integrations.update_policy(integration_id, self.user, {'credential_expires_at': None})
        self.integrations.update(integration_id, self.user, {'enabled': False})
        with self.assertRaises(IntegrationError) as disabled:
            self.integrations.append(token, identifier, shipment_event(), 'disabled', '192.0.2.3')
        self.assertEqual(disabled.exception.status, 403)
        self.assertEqual(self.store.read(identifier)['ledger']['block_count'], 2)


@unittest.skipUnless(DATABASE_URL, 'Isolated chainguard_test required')
class ManagedCredentialHttpTests(unittest.TestCase):
    setUp = legacy_tests.WebTests.setUp
    tearDown = legacy_tests.WebTests.tearDown
    request = legacy_tests.WebTests.request
    register = legacy_tests.WebTests.register

    def test_http_new_template_rejects_legacy_policy_bypass(self):
        _, cookie, csrf = self.register()
        status, created, _ = self.request('POST', '/api/ledgers',
            {'name': 'Managed HTTP fixture', 'description': '', 'template_id': 'shipment_events'}, cookie, csrf)
        self.assertEqual(status, 201, created)
        identifier = created['ledger']['id']; self.ledgers.append(identifier)
        self.assertNotIn('write_token', created)
        path = '/api/ledgers/' + identifier
        legacy_token = 'legacy-test-' + uuid.uuid4().hex
        with self.store.connect() as connection:
            connection.execute('UPDATE ledger_registry SET token_hash=%s WHERE id=%s',
                               (token_hash(legacy_token), identifier))
        self.assertEqual(self.request('POST', path + '/blocks', shipment_event(), token=legacy_token)[0], 403)
        self.assertEqual(self.request('POST', path + '/rotate-token', {}, cookie, csrf)[0], 403)
        _, metadata, _ = self.request('GET', path + '/credentials', cookie=cookie)
        self.assertTrue(metadata['managed_writes_required'])
        status, connector, _ = self.request('POST', '/api/integrations',
            {'ledger_id': identifier, 'name': 'Managed HTTP source', 'auth_method': 'bearer',
             'policy': {'allowed_source_cidrs': ['192.0.2.0/24']}}, cookie, csrf)
        self.assertEqual(status, 201, connector)
        self.assertEqual(self.request('POST', path + '/blocks', shipment_event(), token=connector['secret'])[0], 403)
        self.assertEqual(self.request('POST', path + '/blocks', shipment_event(), token=legacy_token)[0], 403)
        self.assertEqual(self.store.read(identifier)['ledger']['block_count'], 1)


if __name__ == '__main__':
    unittest.main()
