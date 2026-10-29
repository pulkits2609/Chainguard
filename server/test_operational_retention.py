"""Application administrators archive populated operational ledgers instead of erasing them."""
import datetime
import os
import unittest
import uuid
import test_webapp as fixtures


@unittest.skipUnless(os.environ.get('LEDGER_TEST_DATABASE_URL'), 'Isolated PostgreSQL required')
class OperationalRetentionTests(unittest.TestCase):
    setUp=fixtures.WebTests.setUp
    tearDown=fixtures.WebTests.tearDown
    request=fixtures.WebTests.request
    register=fixtures.WebTests.register
    create=fixtures.WebTests.create

    def admin_session(self):
        username='retention_'+uuid.uuid4().hex[:12]
        admin,_=self.accounts.initialize_admin(username,fixtures.PASSWORD)
        self.users.append(admin['id'])
        status,result,headers=self.request('POST','/api/login',{'username':username,'password':fixtures.PASSWORD})
        self.assertEqual(status,200,result)
        return headers['Set-Cookie'].split(';')[0],result['csrf_token']

    def test_archived_checkpointed_operational_ledger_cannot_be_hard_deleted(self):
        _,cookie,csrf=self.register()
        status,created,_=self.request('POST','/api/ledgers',{'name':'Retained operations','description':'Fixture','template_id':'shipment_events'},cookie,csrf)
        self.assertEqual(status,201,created)
        identifier=created['ledger']['id']; self.ledgers.append(identifier); path='/api/ledgers/'+identifier
        _,connector,_=self.request('POST','/api/integrations',{'ledger_id':identifier,'name':'Retention fixture','auth_method':'bearer'},cookie,csrf)
        event={'shipment_id':'RETENTION-TEST','event_type':'TEST_RECORD','source_system':'AUTOMATED_TEST','event_time':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        self.assertEqual(self.request('POST',path+'/blocks',event,token=connector['secret'])[0],201)
        _,receipt,_=self.request('POST',path+'/signed-checkpoints',{},cookie,csrf)
        checkpoint=receipt['checkpoint']
        admin_cookie,admin_csrf=self.admin_session()
        self.assertEqual(self.request('PATCH',path,{'state':'archived'},admin_cookie,admin_csrf)[0],200)
        for suffix in ('','?test_cleanup=true'):
            status,denied,_=self.request('DELETE',path+suffix,{'confirm':'Retained operations'},admin_cookie,admin_csrf)
            self.assertEqual(status,409,denied)
        self.assertEqual(self.store.get_ledger(identifier)['state'],'archived')
        self.assertEqual(self.server.checkpoints.get(identifier,checkpoint['checkpoint_id']),checkpoint)
        self.assertTrue(self.store.read(identifier)['verification']['valid'])
        status,events,_=self.request('GET','/api/security/events?category=ledger_retention',cookie=admin_cookie)
        self.assertEqual(status,200,events)
        self.assertTrue(any(row['ledger_id']==identifier and row['reason_code']=='hard_delete_blocked' for row in events['events']))

    def test_populated_generic_legacy_deletion_remains_compatible(self):
        _,cookie,csrf=self.register()
        created=self.create(cookie,csrf); identifier=created['ledger']['id']; path='/api/ledgers/'+identifier
        self.assertEqual(self.request('POST',path+'/blocks',{'value':7},token=created['write_token'])[0],201)
        admin_cookie,admin_csrf=self.admin_session()
        self.assertEqual(self.request('DELETE',path,{'confirm':'Records'},admin_cookie,admin_csrf)[0],200)
        self.assertEqual(self.request('GET',path,cookie=cookie)[0],404)

    def test_disposable_cleanup_cannot_be_enabled_against_application_database(self):
        from store import Store,StoreForbidden
        # Guard executes before a connection or mutation, even if a caller supplies the internal keyword.
        application=Store.__new__(Store)
        application.database_url='postgresql://unused:unused@127.0.0.1:1/chainguard'
        with self.assertRaises(StoreForbidden):
            application.delete_ledger(str(uuid.uuid4()),test_cleanup=True)
