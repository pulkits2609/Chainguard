import os
import unittest
import uuid
from ledger_templates import templates, validate_template_event
from store import Store, ledger_genesis
from ledger import canonical_json


class TemplateValidationTests(unittest.TestCase):
    def test_database_isolation_is_enforced_before_connecting(self):
        from settings import require_chainguard_database
        from accounts import Accounts
        for factory in (Store, Accounts, require_chainguard_database):
            for database in ('blockchain_ledger', 'postgres', 'unrelated'):
                with self.assertRaises(ValueError):
                    factory('postgresql://unused:unused@127.0.0.1:1/'+database)
    def test_timestamp_and_identifier_validation(self):
        for value in ('not-a-date', '2026-10-28', '2026-10-28T10:00:00', '2026-99-28T10:00:00Z'):
            with self.assertRaises(ValueError):
                validate_template_event({'event_time': value}, 'shipment_events')
        validate_template_event({'event_time': '2026-10-28T10:00:00+05:30'}, 'shipment_events')
        with self.assertRaises(ValueError):
            validate_template_event({'event_time':'2026-10-28T10:00:00Z','shipment_id':''}, 'shipment_events')


@unittest.skipUnless(os.environ.get('LEDGER_TEST_DATABASE_URL'), 'Isolated PostgreSQL test database required')
class TemplateStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.environ['LEDGER_TEST_DATABASE_URL'])
        self.created = []

    def tearDown(self):
        for identifier in self.created:
            self.store.delete_ledger(identifier, test_cleanup=True)

    def test_templates_use_ordinary_genesis_and_default_private(self):
        values = {'shipment_id':'SHP-test','event_type':'RESOURCE_CREATED', 'event_time':'2026-10-28T10:00:00Z',
                  'source_system':'TEST_FIXTURE','warehouse_id':'WH-test','item_id':'ITEM-test','quantity':3,
                  'custody_event':'TRANSFER'}
        for template in templates():
            result = self.store.create_ledger('test-'+uuid.uuid4().hex,template['name'],'',template_id=template['id'])
            ledger = result['ledger']; self.created.append(ledger['id'])
            self.assertEqual(ledger['visibility'],'private')
            self.assertEqual(self.store.read(ledger['id'])['blocks'][0], ledger_genesis(ledger['id'],template['fields']))
            data = {field['name']: values[field['name']] for field in template['fields'] if field['required']}
            # This fixture exercises template validation through the shared internal
            # engine. Managed credential authorization is tested separately.
            self.store.append_authorized(ledger['id'],data,'event-1')
            self.assertTrue(self.store.read(ledger['id'])['verification']['valid'])
            self.assertNotIn(ledger['id'],[row['id'] for row in self.store.list_ledgers(public_only=True)])
            original = canonical_json(self.store.read(ledger['id'])['blocks'])
            self.store.update_ledger(ledger['id'],visibility='public')
            self.assertEqual(original,canonical_json(self.store.read(ledger['id'])['blocks']))
