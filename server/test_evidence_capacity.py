"""Accepted histories must remain exportable through both existing 8 MiB formats."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from psycopg import sql
from psycopg.types.json import Jsonb
from ledger import create_new_block
from store import MAX_ACCEPTED_HISTORY_BYTES, table_names
import test_webapp as fixtures


@unittest.skipUnless(os.environ.get('LEDGER_TEST_DATABASE_URL'), 'Isolated PostgreSQL required')
class EvidenceCapacityTests(unittest.TestCase):
    setUp = fixtures.WebTests.setUp
    tearDown = fixtures.WebTests.tearDown
    request = fixtures.WebTests.request
    register = fixtures.WebTests.register

    def create_large_ledger(self):
        _, cookie, csrf = self.register()
        status, created, _ = self.request('POST','/api/ledgers',{'name':'Capacity fixture','description':'Disposable valid records',
            'fields':[{'name':'payload','type':'string','required':True}], 'visibility':'private'},cookie,csrf)
        self.assertEqual(status,201,created)
        self.ledgers.append(created['ledger']['id'])
        return created,cookie,csrf

    def offline(self, checkpoint, history):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for name,value in [('checkpoint',checkpoint),('history',history),('key',self.server.checkpoints.verification_key)]:
                (folder/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'tools/verify_checkpoint.py'),
                '--checkpoint',str(folder/'checkpoint.json'),'--public-key',str(folder/'key.json'),
                '--ledger-id',history['ledger']['id'],'--history',str(folder/'history.json')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(json.loads(result.stdout)['history_verified'])

    def test_attempted_growth_beyond_eight_mib_preserves_all_export_workflows(self):
        created,cookie,csrf=self.create_large_ledger(); identifier=created['ledger']['id']; path='/api/ledgers/'+identifier
        data={'payload':'x'*60000}
        accepted,rejected=0,0
        # All 150 requests are individually valid (<64 KiB); their submitted data exceeds 8 MiB.
        self.assertGreater(len(json.dumps(data).encode())*150,8*1024*1024)
        for index in range(150):
            try:
                self.store.append(identifier,created['write_token'],data,'capacity-'+str(index)); accepted+=1
            except OverflowError:
                rejected+=1
        self.assertGreater(accepted,1); self.assertGreater(rejected,0)
        _,duplicate=self.store.append(identifier,created['write_token'],data,'capacity-0')
        self.assertTrue(duplicate)  # Full ledgers still acknowledge committed idempotent retries.
        status,history,_=self.request('GET',path+'/history-export',cookie=cookie)
        self.assertEqual(status,200,history)
        self.assertTrue(history['verification']['valid'])
        self.assertEqual(len(history['blocks']),accepted+1)
        self.assertLessEqual(sum(len(json.dumps(block,ensure_ascii=True).encode()) for block in history['blocks']),MAX_ACCEPTED_HISTORY_BYTES)
        self.assertLess(len(json.dumps(history).encode()),8*1024*1024)
        status,snapshot,_=self.request('GET',path+'/snapshot',cookie=cookie)
        self.assertEqual(status,200)
        self.assertLess(len(snapshot),8*1024*1024)
        status,report,_=self.request('POST',path+'/validate-snapshot',snapshot,cookie,csrf,headers={'Content-Type':'application/octet-stream'})
        self.assertEqual(status,200,report); self.assertTrue(report['valid'])
        status,receipt,_=self.request('POST',path+'/signed-checkpoints',{},cookie,csrf)
        self.assertEqual(status,201,receipt)
        self.offline(receipt['checkpoint'],history)

    def test_oversized_preexisting_history_is_not_rewritten_and_has_operator_recovery(self):
        created,cookie,csrf=self.create_large_ledger(); identifier=created['ledger']['id']; path='/api/ledgers/'+identifier
        # Simulate a valid legacy ledger created before the new accepted-history cap.
        blocks=self.store.read(identifier)['blocks']; data={'payload':'L'*60000}
        for index in range(150):
            blocks.append(create_new_block(blocks[-1],{'record_id':'legacy-'+str(index),'data':data}))
        table=sql.Identifier(table_names(identifier)[0])
        with self.store.connect() as connection:
            with connection.cursor() as cursor:
                with cursor.copy(sql.SQL('COPY {} (position,record_id,record) FROM STDIN').format(table)) as copy:
                    for block in blocks[1:]:
                        copy.write_row((block['index'],block['record_id'],Jsonb(block)))
            connection.execute('UPDATE ledger_registry SET head_index=%s,head_hash=%s WHERE id=%s',(blocks[-1]['index'],blocks[-1]['hash'],identifier))
        view=self.store.read(identifier)
        self.assertTrue(view['verification']['valid'])
        self.assertGreater(len(json.dumps(view).encode()),8*1024*1024)
        self.assertEqual(self.request('GET',path+'/history-export',cookie=cookie)[0],413)
        self.assertEqual(self.request('GET',path+'/snapshot',cookie=cookie)[0],413)
        with self.assertRaises(OverflowError):
            self.store.append(identifier,created['write_token'],data,'no-growth')
        status,receipt,_=self.request('POST',path+'/signed-checkpoints',{},cookie,csrf)
        self.assertEqual(status,201,receipt)
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/'history.json'
            command=[sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'tools/export_history.py'),
                     '--test-database','--ledger-id',identifier,'--output',str(destination)]
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            recovered=json.loads(destination.read_text())
            self.assertEqual(recovered['blocks'],blocks)
            self.offline(receipt['checkpoint'],recovered)
            again=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(again.returncode,0)  # Retained evidence is never overwritten.
