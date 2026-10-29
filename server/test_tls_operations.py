"""Expected TLS disconnects stay quiet; real certificate/protocol errors remain visible."""
import datetime
import io
import ipaddress
import os
from pathlib import Path
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import Mock
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from psycopg import sql
from psycopg.types.json import Jsonb

import test_webapp as fixtures
from ledger import create_new_block
from store import table_names
from web_app import TLSClientDisconnected, WebHandler, WebServer


class TLSDisconnectTests(unittest.TestCase):
    def test_only_expected_handshake_disconnects_are_classified_quietly(self):
        for error in (ssl.SSLEOFError(), ssl.SSLZeroReturnError(), ConnectionResetError(),
                      BrokenPipeError(), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                peer = Mock(spec=ssl.SSLSocket)
                peer.makefile.return_value = io.BytesIO()
                peer.do_handshake.side_effect = error
                handler = object.__new__(WebHandler)
                handler.request = peer
                with self.assertRaises(TLSClientDisconnected):
                    handler.setup()
        peer = Mock(spec=ssl.SSLSocket)
        peer.makefile.return_value = io.BytesIO()
        peer.do_handshake.side_effect = ssl.SSLError('unexpected protocol error')
        handler = object.__new__(WebHandler); handler.request = peer
        with self.assertRaises(ssl.SSLError):
            handler.setup()

    def test_worker_reports_unexpected_failures_and_always_releases_capacity(self):
        for error, visible in ((TLSClientDisconnected(), False),
                               (ssl.SSLError('unexpected protocol'), True),
                               (RuntimeError('unexpected application error'), True)):
            server = Mock()
            server.finish_request.side_effect = error
            WebServer.process_request_thread(server, object(), ('127.0.0.1', 1))
            self.assertEqual(server.handle_error.called, visible)
            server.shutdown_request.assert_called_once()
            server.workers.release.assert_called_once()


def write_private(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(value)


@unittest.skipUnless(os.environ.get('LEDGER_TEST_DATABASE_URL'), 'Isolated PostgreSQL required')
class TrustedHTTPSOperationsTests(unittest.TestCase):
    setUp = fixtures.WebTests.setUp
    tearDown = fixtures.WebTests.tearDown
    request = fixtures.WebTests.request

    def test_history_revision_trigger_works_with_restore_empty_search_path(self):
        created=self.store.create_ledger('restore-trigger-'+uuid.uuid4().hex,'Restore trigger fixture','',fixtures.FIELDS)
        identifier=created['ledger']['id']; self.ledgers.append(identifier)
        before=self.store.read(identifier)['blocks'][-1]
        block=create_new_block(before,{'record_id':'restored-row','data':{'value':1}})
        with self.store.connect() as connection:
            revision=connection.execute('SELECT history_revision FROM public.ledger_registry WHERE id=%s',(identifier,)).fetchone()['history_revision']
            connection.execute("SET LOCAL search_path = ''")
            connection.execute(sql.SQL('INSERT INTO {} (position,record_id,record) VALUES (%s,%s,%s)')
                .format(sql.Identifier('public',table_names(identifier)[0])),(block['index'],block['record_id'],Jsonb(block)))
            connection.execute('UPDATE public.ledger_registry SET head_index=%s,head_hash=%s WHERE id=%s',(block['index'],block['hash'],identifier))
            self.assertEqual(connection.execute('SELECT history_revision FROM public.ledger_registry WHERE id=%s',(identifier,)).fetchone()['history_revision'],revision+1)
        self.assertTrue(self.store.read(identifier)['verification']['valid'])

    def test_trusted_private_ca_hostname_secure_cookie_csrf_and_abandoned_peer(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
        temporary = Path(self.temp.name)
        now = datetime.datetime.now(datetime.timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'ChainGuard disposable test CA')])
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
              .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
              .sign(ca_key, hashes.SHA256()))
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        leaf = (x509.CertificateBuilder().subject_name(leaf_name).issuer_name(ca_name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
                .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                .sign(ca_key, hashes.SHA256()))
        ca_path, cert_path, key_path = temporary/'ca.pem', temporary/'server.pem', temporary/'server.key'
        ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        write_private(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.server = WebServer(('127.0.0.1', 0), self.store, self.accounts, temporary,
                                secure_cookies=True, development=False)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        server_context.load_cert_chain(cert_path, key_path)
        self.server.socket = server_context.wrap_socket(self.server.socket, server_side=True, do_handshake_on_connect=False)
        self.server.handle_error = Mock()
        self.thread = threading.Thread(target=self.server.serve_forever); self.thread.start()
        trusted = ssl.create_default_context(cafile=str(ca_path))
        self.assertTrue(trusted.check_hostname)
        self.assertEqual(trusted.verify_mode, ssl.CERT_REQUIRED)
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.request('GET', '/api/me', tls=ssl.create_default_context())
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as raw:
            with self.assertRaises(ssl.SSLCertVerificationError):
                trusted.wrap_socket(raw, server_hostname='not-localhost.example')
        # Client trust/hostname errors are the contract above. OpenSSL/platforms
        # may deliver their abort as a TLS alert, reset or EOF to the server;
        # deterministic unit tests separately prove unexpected errors are reported.
        # Drain the bounded pool before resetting the observer so a late alert
        # from either rejected client cannot contaminate the abandoned-peer check.
        acquired=0
        deadline=time.monotonic()+3
        try:
            while acquired<32 and time.monotonic()<deadline:
                if self.server.workers.acquire(blocking=False):
                    acquired+=1
                else:
                    time.sleep(0.01)
            self.assertEqual(acquired,32,'Rejected TLS client workers did not finish')
        finally:
            for _ in range(acquired):
                self.server.workers.release()
        self.server.handle_error.reset_mock()
        for _ in range(3):
            abandoned=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=3)
            abandoned.close()
        username='https_'+uuid.uuid4().hex[:12]
        status, account, headers = self.request('POST','/api/register',
            {'username':username,'password':fixtures.PASSWORD},tls=trusted)
        self.assertEqual(status,201,account); self.users.append(account['user']['id'])
        cookie=headers['Set-Cookie'].split(';')[0]
        self.assertTrue(cookie.startswith('__Host-ledger_session='))
        self.assertIn('; Secure',headers['Set-Cookie'])
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('max-age=',headers['Strict-Transport-Security'])
        value={'name':'Trusted CA test','description':'Disposable fixture','template_id':'security_events'}
        self.assertEqual(self.request('POST','/api/ledgers',value,cookie=cookie,tls=trusted)[0],403)
        status, created, _ = self.request('POST','/api/ledgers',value,cookie=cookie,csrf=account['csrf_token'],tls=trusted)
        self.assertEqual(status,201,created); self.ledgers.append(created['ledger']['id'])
        path='/api/ledgers/'+created['ledger']['id']
        self.assertEqual(self.request('GET',path,tls=trusted)[0],404)
        self.assertEqual(self.request('GET',path,cookie=cookie,tls=trusted)[0],200)
        # All completed and abandoned handlers have had a chance to finish.
        time.sleep(0.05)
        self.assertFalse(self.server.handle_error.called)
