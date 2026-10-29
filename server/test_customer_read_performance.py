"""Cache freshness never substitutes for current authorization or mutation detection."""
from unittest.mock import patch
import json
import os
import unittest
import uuid
from psycopg import sql
from psycopg.types.json import Jsonb
import test_shipment_access as fixtures
from store import StoreForbidden, table_names, verify_ledger


@unittest.skipUnless(os.environ.get('LEDGER_TEST_DATABASE_URL'), 'Isolated PostgreSQL required')
class CustomerReadTests(unittest.TestCase):
    setUp = fixtures.ShipmentDatabaseTests.setUp
    tearDown = fixtures.ShipmentDatabaseTests.tearDown
    append = fixtures.ShipmentDatabaseTests.append

    def grant(self):
        return self.service.create(self.ledger_id,'SHP-1',user=self.user)['access_token']

    def test_repeated_poll_checks_history_once_then_invalidates_on_sql_tampering(self):
        token=self.grant()
        with patch.object(self.store,'_blocks',wraps=self.store._blocks) as read:
            first=self.service.events('SHP-1',token)
            second=self.service.events('SHP-1',token)
            self.assertEqual(read.call_count,1)
            self.assertTrue(second['verification']['server_records_verified'])
            self.assertIn('checked_at_ms',second['verification'])
            table=sql.Identifier(table_names(self.ledger_id)[0])
            block=self.store.read(self.ledger_id)['blocks'][1]
            block['data']['event_type']='TAMPERED'
            with self.store.connect() as connection:
                connection.execute(sql.SQL('UPDATE {} SET record=%s WHERE position=1').format(table),(Jsonb(block),))
            failed=self.service.events('SHP-1',token)
            self.assertFalse(failed['verification']['server_records_verified'])
            self.assertEqual(failed['verification']['status'],'verification_failed')
            self.assertEqual(first['total'],1)

    def test_periodic_recheck_and_bounded_checkpoint_work(self):
        token=self.grant()
        for _ in range(23):
            self.checkpoints.create(self.ledger_id)
        clock=[0.0]
        self.service.verification_clock=lambda:clock[0]
        with patch('shipment_access.verify_ledger',wraps=verify_ledger) as verify:
            result=self.service.events('SHP-1',token)
            self.assertEqual(verify.call_count,1)
            self.assertEqual(result['verification']['checkpoint_comparisons'],20)
            self.assertFalse(result['verification']['all_checkpoints_compared'])
            self.service.events('SHP-1',token)
            self.assertEqual(verify.call_count,1)
            clock[0]=31
            self.service.events('SHP-1',token)
            self.assertEqual(verify.call_count,2)

    def test_cached_verification_does_not_cache_access_grant_authorization(self):
        token=self.grant()
        self.service.events('SHP-1',token)
        grant=self.service.list(self.ledger_id,user=self.user)['grants'][0]
        self.service.revoke(self.ledger_id,grant['grant_id'],user=self.user)
        with self.assertRaises(StoreForbidden):
            self.service.events('SHP-1',token)

    def test_cache_expiry_is_evaluated_after_waiting_for_verification_lock(self):
        token=self.grant()
        clock=[0.0]
        self.service.verification_clock=lambda:clock[0]
        self.service.events('SHP-1',token)
        class DelayedLock:
            def __enter__(self):
                clock[0]=31.0  # Deterministically model elapsed wait time before acquiring the lock.
            def __exit__(self,*args):
                pass
        self.service._verification_lock=DelayedLock()
        with patch.object(self.store,'_blocks',wraps=self.store._blocks) as reads:
            self.service.events('SHP-1',token)
            self.assertEqual(reads.call_count,1)

    def test_over_budget_append_is_rejected_without_stranding_existing_history(self):
        blocks=self.store.read(self.ledger_id)['blocks']
        current_bytes=sum(len(json.dumps(block,ensure_ascii=True).encode()) for block in blocks)
        with patch.dict(os.environ,{'LEDGER_MAX_HISTORY_BYTES':str(max(1024,current_bytes+16))}):
            with self.assertRaises(OverflowError):
                self.append('SHP-1','over-budget')
            self.assertTrue(self.store.read(self.ledger_id)['verification']['valid'])
            self.assertEqual(self.store.read(self.ledger_id)['blocks'],blocks)

    def test_over_budget_genesis_is_rejected_without_creating_ledger(self):
        owner='budget-'+uuid.uuid4().hex
        fields=[{'name':'field_'+'x'*54+str(index),'type':'string','required':True} for index in range(32)]
        with patch.dict(os.environ,{'LEDGER_MAX_HISTORY_BYTES':'1024'}):
            with self.assertRaises(OverflowError):
                self.store.create_ledger(owner,'Too large','',fields)
        self.assertEqual(self.store.list_ledgers(owner),[])
