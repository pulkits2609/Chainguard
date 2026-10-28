"""Phase 4 concurrency/volume/head-removal checks on the disposable database."""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest
import uuid
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from ledger import LedgerConflict
from store import Store, table_names


@unittest.skipUnless(os.environ.get('LEDGER_TEST_DATABASE_URL'), 'Isolated PostgreSQL required')
class IntegrityAcceptanceTests(unittest.TestCase):
    def test_one_hundred_events_concurrent_append_and_deleted_suffix(self):
        dsn=os.environ['LEDGER_TEST_DATABASE_URL']
        self.assertEqual(conninfo_to_dict(dsn).get('dbname'),'chainguard_test')
        store=Store(dsn)
        result=store.create_ledger('phase4-'+uuid.uuid4().hex,'Integrity volume fixture','',
            [{'name':'value','type':'integer','required':True}])
        identifier=result['ledger']['id']; token=result['write_token']
        try:
            for index in range(100):
                store.append(identifier,token,{'value':index},'sequential-'+str(index))
            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(lambda index:store.append(identifier,token,{'value':index},'concurrent-'+str(index)),range(20)))
            view=store.read(identifier)
            self.assertTrue(view['verification']['valid'])
            self.assertEqual([block['index'] for block in view['blocks']],list(range(121)))
            for previous,current in zip(view['blocks'],view['blocks'][1:]):
                self.assertEqual(current['previous_hash'],previous['hash'])
            with store.connect() as connection:
                connection.execute(sql.SQL('DELETE FROM {} WHERE position=120').format(sql.Identifier(table_names(identifier)[0])))
            self.assertFalse(store.read(identifier)['verification']['valid'])
            with self.assertRaises(LedgerConflict):
                store.append(identifier,token,{'value':200},'after-corruption')
        finally:
            store.delete_ledger(identifier)
