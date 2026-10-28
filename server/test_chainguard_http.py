"""Actual HTTP/DB security boundaries; fixtures are not a genuine external integration."""
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_webapp as legacy_tests


@unittest.skipUnless(os.environ.get('LEDGER_TEST_DATABASE_URL'), 'Isolated PostgreSQL required')
class ChainGuardHttpTests(unittest.TestCase):
    setUp = legacy_tests.WebTests.setUp
    tearDown = legacy_tests.WebTests.tearDown
    request = legacy_tests.WebTests.request
    register = legacy_tests.WebTests.register

    def ledger(self, cookie, csrf, template='shipment_events'):
        status, result, _ = self.request('POST','/api/ledgers',
            {'name':'Private test ledger','description':'Automated test fixtures','template_id':template}, cookie,csrf)
        self.assertEqual(status,201,result)
        self.ledgers.append(result['ledger']['id'])
        return result

    def test_private_routes_listing_visibility_and_snapshot_permissions(self):
        _,cookie,csrf=self.register(); _,other,_=self.register()
        created=self.ledger(cookie,csrf); identifier=created['ledger']['id']; path='/api/ledgers/'+identifier
        self.assertNotIn(identifier,json.dumps(self.request('GET','/api/ledgers')[1]))
        for suffix in ('','/snapshot','/checkpoint','/signed-checkpoints','/shipment-access','/history-export'):
            for session in (None,other):
                status,_,_=self.request('GET',path+suffix,cookie=session)
                self.assertIn(status,(401,404),(suffix,status))
        self.assertEqual(self.request('GET',path,cookie=cookie)[0],200)
        self.assertEqual(self.request('GET',path+'/snapshot',cookie=cookie)[0],200)
        _,snapshot,_=self.request('GET',path+'/snapshot',cookie=cookie)
        binary={'Content-Type':'application/octet-stream'}
        self.assertEqual(self.request('POST',path+'/validate-snapshot',snapshot,headers=binary)[0],404)
        self.assertEqual(self.request('POST',path+'/validate-snapshot',snapshot,cookie=other,headers=binary)[0],404)
        self.assertEqual(self.request('POST',path+'/validate-snapshot',snapshot,cookie=cookie,csrf=csrf,headers=binary)[0],200)
        self.assertEqual(self.request('POST',path+'/verify',{},cookie=cookie)[0],403)
        self.assertEqual(self.request('POST',path+'/verify',{},cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual(self.request('PATCH',path,{'visibility':'public'},cookie,csrf)[0],200)
        self.assertEqual(self.request('GET',path)[0],200)
        self.assertEqual(self.request('PATCH',path,{'state':'archived'},cookie,csrf)[0],403)

    def test_global_body_cap_covers_legacy_tokens_and_integrity_metrics(self):
        user,cookie,csrf = self.register()
        created = self.ledger(cookie,csrf,template='security_events')
        identifier=created['ledger']['id']; path='/api/ledgers/'+identifier
        self.server.integrations.max_body_bytes=128
        event={'event_type':'TEST','source_system':'TEST','event_time':'2026-10-29T00:00:00Z','details':{'padding':'x'*256}}
        self.assertEqual(self.request('POST',path+'/blocks',event,token=created['write_token'])[0],413)
        self.assertEqual(self.server.store.read(identifier)['ledger']['block_count'],1)
        self.server.integrations.max_body_bytes=65536
        # A real rejected append must contribute to the integrity metric.
        self.assertEqual(self.request('POST',path+'/blocks',event,token=created['write_token'])[0],201)
        from psycopg import sql
        from psycopg.types.json import Jsonb
        from store import table_names
        changed=self.server.store.read(identifier)['blocks'][1]
        changed['data']['event_type']='TAMPERED'
        with self.server.store.connect() as connection:
            connection.execute(sql.SQL('UPDATE {} SET record=%s WHERE position=1').format(sql.Identifier(table_names(identifier)[0])),(Jsonb(changed),))
        _,connector,_=self.request('POST','/api/integrations',{'ledger_id':identifier,'name':'Corrupt chain check','auth_method':'bearer'},cookie,csrf)
        self.assertEqual(self.request('POST',path+'/blocks',event,token=connector['secret'])[0],409)
        import uuid
        from test_webapp import PASSWORD
        username='admin_'+uuid.uuid4().hex[:12]
        admin,_=self.accounts.initialize_admin(username,PASSWORD); self.users.append(admin['id'])
        _,_,headers=self.request('POST','/api/login',{'username':username,'password':PASSWORD})
        status,overview,_=self.request('GET','/api/overview',cookie=headers['Set-Cookie'].split(';')[0])
        self.assertEqual(status,200,overview)
        self.assertGreaterEqual(overview['recent_verification_failures'],1)

    def test_verify_credentials_respect_body_caps_and_legacy_cg_prefix(self):
        from store import token_hash
        _,cookie,csrf=self.register()
        created=self.ledger(cookie,csrf); identifier=created['ledger']['id']; path='/api/ledgers/'+identifier
        config={'ledger_id':identifier,'name':'Verify only','auth_method':'bearer',
                'credential_scopes':['verify'],'policy':{'allowed_operations':['verify'],'max_body_bytes':1}}
        status,result,_=self.request('POST','/api/integrations',config,cookie,csrf)
        self.assertEqual(status,201,result)
        token=result['secret']
        self.assertEqual(self.request('POST',path+'/verify',{},token=token)[0],413)
        self.assertEqual(self.request('POST',path+'/validate-snapshot',b'larger',token=token,
            headers={'Content-Type':'application/octet-stream'})[0],413)
        legacy_token='cg_'+'x'*40
        with self.server.store.connect() as connection:
            connection.execute('UPDATE ledger_registry SET token_hash=%s WHERE id=%s',(token_hash(legacy_token),identifier))
        event={'shipment_id':'PREFIX-TEST','event_type':'TEST','source_system':'TEST','event_time':'2026-10-29T00:00:00Z'}
        self.assertEqual(self.request('POST',path+'/blocks',event,token=legacy_token)[0],201)

    def test_integration_policy_rest_hmac_replay_rotation_and_checkpoint_customer(self):
        user,cookie,csrf=self.register(); created=self.ledger(cookie,csrf)
        identifier=created['ledger']['id']; path='/api/ledgers/'+identifier
        config={'ledger_id':identifier,'name':'HTTP test connector','auth_method':'bearer'}
        self.assertEqual(self.request('POST','/api/integrations',config,cookie=cookie)[0],403)
        status,result,_=self.request('POST','/api/integrations',config,cookie,csrf)
        self.assertEqual(status,201,result)
        token=result['secret']; integration=result['integration']; ipath='/api/integrations/'+integration['id']
        self.assertNotIn(token,json.dumps(self.request('GET',ipath,cookie=cookie)[1]))
        event={'shipment_id':'TEST-1','event_type':'RESOURCE_CREATED','source_system':'AUTOMATED_TEST',
               'event_time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'operator_id':'INTERNAL-EMPLOYEE'}
        retry={'Idempotency-Key':'event-http-1'}
        self.assertEqual(self.request('POST',path+'/blocks',event,token=token,headers=retry)[0],201)
        self.assertEqual(self.request('POST',path+'/blocks',event,token=token,headers=retry)[0],200)
        self.assertEqual(self.request('POST',path+'/blocks',{**event,'event_type':'CHANGED'},token=token,headers=retry)[0],409)
        self.assertEqual(self.request('GET',path,token=token)[0],403)
        self.assertEqual(self.request('POST',path+'/verify',{},token=token)[0],403)
        self.assertEqual(self.request('PATCH',ipath+'/policy',{'require_authentication':False},cookie,csrf)[0],400)
        self.assertEqual(self.request('PATCH',ipath+'/policy',{'requests_per_minute':1},cookie,csrf)[0],200)
        self.assertEqual(self.request('POST',path+'/blocks',event,token=token,headers={'Idempotency-Key':'rate-hit'})[0],429)
        self.request('PATCH',ipath+'/policy',{'requests_per_minute':60},cookie,csrf)
        _,rotated,_=self.request('POST',ipath+'/rotate-secret',{},cookie,csrf)
        self.assertEqual(self.request('POST',path+'/blocks',event,token=token)[0],401)
        token=rotated['secret']
        self.assertEqual(self.request('POST',path+'/blocks',event,token=token,headers={'Idempotency-Key':'event-http-2'})[0],201)
        hconfig={'ledger_id':identifier,'name':'Signed HTTP test','auth_method':'hmac',
                 'field_mapping':{key:key for key in event},'event_id_field':'event_id','timestamp_field':'event_time'}
        status,hresult,_=self.request('POST','/api/integrations',hconfig,cookie,csrf)
        self.assertEqual(status,201,hresult)
        webhook='/api/integrations/'+hresult['integration']['id']+'/webhook'
        raw=json.dumps({**event,'event_id':'hmac-test-1'}).encode()
        signature='sha256='+hmac.new(hresult['secret'].encode(),raw,hashlib.sha256).hexdigest()
        headers={'X-ChainGuard-Signature':signature}
        self.assertEqual(self.request('POST',webhook,raw,headers=headers)[0],201)
        self.assertEqual(self.request('POST',webhook,raw,headers=headers)[0],200)
        self.assertEqual(self.request('POST',webhook,raw,headers={'X-ChainGuard-Signature':'sha256='+'0'*64})[0],401)
        status,receipt,_=self.request('POST',path+'/signed-checkpoints',{},cookie,csrf)
        self.assertEqual(status,201,receipt); checkpoint=receipt['checkpoint']
        _,key,_=self.request('GET','/api/verification-keys/'+checkpoint['key_id'])
        _,history,_=self.request('GET',path+'/history-export',cookie=cookie)
        with tempfile.TemporaryDirectory() as directory:
            temporary=Path(directory)
            for name,value in [('checkpoint',checkpoint),('key',key),('history',history)]:
                (temporary/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
            cli=Path(__file__).resolve().parents[1]/'tools/verify_checkpoint.py'
            result=subprocess.run([sys.executable,'-B',str(cli),'--checkpoint',str(temporary/'checkpoint.json'),
                '--public-key',str(temporary/'key.json'),'--ledger-id',identifier,'--history',str(temporary/'history.json')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(json.loads(result.stdout)['history_verified'])
        status,grant,_=self.request('POST',path+'/shipment-access',{'shipment_id':'TEST-1'},cookie,csrf)
        self.assertEqual(status,201,grant)
        capability=grant['access_token']; customer='/api/customer/shipments/TEST-1'
        status,timeline,_=self.request('GET',customer+'/events',token=capability)
        self.assertEqual(status,200,timeline)
        self.assertNotIn('INTERNAL-EMPLOYEE',json.dumps(timeline))
        self.assertNotIn('operator_id',json.dumps(timeline))
        self.assertEqual(self.request('GET','/api/customer/shipments/TEST-2/events',token=capability)[0],403)
        self.assertEqual(self.request('GET',path,token=capability)[0],403)
        self.assertEqual(self.request('GET',customer+'/proof',token=capability)[0],200)
        self.assertEqual(self.request('GET','/api/security/events',cookie=cookie)[0],403)
        self.request('DELETE',path+'/shipment-access/'+grant['grant']['grant_id'],{},cookie,csrf)
        self.assertEqual(self.request('GET',customer+'/events',token=capability)[0],403)
        self.request('DELETE',ipath,{},cookie,csrf)
        self.assertEqual(self.request('POST',path+'/blocks',event,token=token)[0],403)
