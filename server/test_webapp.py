"""Exercise real PostgreSQL permissions and HTTP/TLS endpoints, not mocked authorization."""

import datetime
import http.client
import ipaddress
import json
import os
from pathlib import Path
import ssl
import socket
import tempfile
import threading
import unittest
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from psycopg import sql
from psycopg.types.json import Jsonb

from accounts import Accounts, password_hash, password_matches
from settings import load_environment, public_origin
from store import Store, table_names
from web_app import WebServer


DATABASE_URL = os.environ.get("LEDGER_TEST_DATABASE_URL")
PASSWORD = "test-only-long-password"
FIELDS = [{"name": "value", "type": "integer", "required": True}]


class BasicTests(unittest.TestCase):
    def test_password_hashing(self):
        first = password_hash(PASSWORD)
        self.assertNotEqual(first, password_hash(PASSWORD))
        self.assertTrue(password_matches(PASSWORD, first))
        self.assertFalse(password_matches("wrong password", first))
        self.assertFalse(password_matches(PASSWORD, "broken"))

    def test_literal_configuration_and_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.env"
            path.write_text('LEDGER_TEST_LITERAL="$(not-a-command)"\n', encoding="utf-8")
            try:
                load_environment(path)
                self.assertEqual(os.environ["LEDGER_TEST_LITERAL"], "$(not-a-command)")
            finally:
                os.environ.pop("LEDGER_TEST_LITERAL", None)
        self.assertEqual(public_origin("https://ledger.example.com:8443/"), "https://ledger.example.com:8443")
        for invalid in ("https://user:secret@example.com", "https://example.com/path", "file:///tmp/key", "https://example.com:bad"):
            with self.assertRaises(ValueError):
                public_origin(invalid)


@unittest.skipUnless(DATABASE_URL, "Set LEDGER_TEST_DATABASE_URL to an isolated PostgreSQL database")
class WebTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATABASE_URL)
        self.accounts = Accounts(DATABASE_URL)
        self.users = []
        self.ledgers = []
        self.temp = tempfile.TemporaryDirectory()
        frontend = Path(self.temp.name)
        (frontend / "index.html").write_text("<html>ledger</html>")
        self.server = WebServer(("127.0.0.1", 0), self.store, self.accounts, frontend,
                                secure_cookies=False, development=True)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        for ledger_id in self.ledgers:
            try:
                self.store.delete_ledger(ledger_id, test_cleanup=True)
            except ValueError:
                pass
        with self.accounts.connect() as connection:
            for user_id in self.users:
                connection.execute("DELETE FROM users WHERE id=%s", (user_id,))
        self.temp.cleanup()

    def request(self, method, path, data=None, cookie=None, csrf=None, token=None, headers=None, tls=None):
        head = {"Content-Type": "application/json"}
        if cookie:
            head["Cookie"] = cookie
        if csrf:
            head["X-CSRF-Token"] = csrf
        if token:
            head["Authorization"] = "Bearer " + token
        head.update(headers or {})
        body = data if isinstance(data, bytes) else json.dumps(data if data is not None else {}).encode()
        head["Content-Length"] = str(len(body))
        if tls:
            connection = http.client.HTTPSConnection("127.0.0.1", self.server.server_port, context=tls, timeout=5)
        else:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body if method != "GET" else None, headers=head)
            response = connection.getresponse()
            raw = response.read()
            value = json.loads(raw) if "application/json" in response.getheader("Content-Type", "") else raw
            return response.status, value, dict(response.getheaders())
        finally:
            connection.close()

    def register(self):
        username = "u_" + uuid.uuid4().hex[:12]
        status, value, headers = self.request("POST", "/api/register", {"username": username, "password": PASSWORD})
        self.assertEqual(status, 201, value)
        self.users.append(value["user"]["id"])
        return value["user"], headers["Set-Cookie"].split(";")[0], value["csrf_token"]

    def create(self, cookie, csrf):
        status, result, _ = self.request("POST", "/api/ledgers", {"name": "Records", "description": "A test ledger", "fields": FIELDS}, cookie, csrf)
        self.assertEqual(status, 201, result)
        self.ledgers.append(result["ledger"]["id"])
        return result

    def test_public_reads_and_owner_only_tokens(self):
        user, cookie, csrf = self.register()
        _, other_cookie, other_csrf = self.register()
        status, _, _ = self.request("POST", "/api/ledgers", {"name": "No csrf", "description": "", "fields": FIELDS}, cookie)
        self.assertEqual(status, 403)
        self.assertEqual(self.request("POST", "/api/ledgers", {"name": "Bad csrf", "description": "", "fields": FIELDS},
                                      cookie, headers={"X-CSRF-Token": "\u00e9"})[0], 403)
        created = self.create(cookie, csrf)
        path = "/api/ledgers/" + created["ledger"]["id"]
        for route in ("/api/ledgers", path):
            status, result, _ = self.request("GET", route)
            self.assertEqual(status, 200)
            text = json.dumps(result)
            for secret in ("write_token", "token_hash", "snapshot_key", "write_path", user["id"]):
                self.assertNotIn(secret, text)
        self.assertEqual(self.request("GET", path + "/credentials")[0], 401)
        self.assertEqual(self.request("GET", path + "/credentials", cookie=other_cookie)[0], 403)
        self.assertEqual(self.request("GET", path + "/credentials", cookie=cookie)[0], 200)
        self.assertEqual(self.request("GET", "/api/my-ledgers", cookie=other_cookie)[1]["ledgers"], [])
        sample = {"value": 7}
        retry = {"Idempotency-Key": "record-1"}
        self.assertEqual(self.request("POST", path + "/blocks", sample)[0], 401)
        self.assertEqual(self.request("POST", path + "/blocks", sample, token="x" * 40)[0], 403)
        self.assertEqual(self.request("POST", path + "/blocks", sample, token=created["write_token"], headers=retry)[0], 201)
        self.assertEqual(self.request("POST", path + "/blocks", sample, token=created["write_token"], headers=retry)[0], 200)
        self.assertEqual(self.request("POST", path + "/blocks", {"value": True}, token=created["write_token"])[0], 400)
        status, rotated, _ = self.request("POST", path + "/rotate-token", {}, cookie, csrf)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("POST", path + "/blocks", sample, token=created["write_token"], headers=retry)[0], 403)
        self.assertEqual(self.request("POST", path + "/blocks", sample, token=rotated["write_token"], headers=retry)[0], 200)
        self.assertEqual(self.request("PATCH", path, {"state": "read_only"}, cookie, csrf)[0], 403)
        self.assertEqual(self.request("POST", "/api/blocks", sample)[0], 404)
        self.assertEqual(self.request("POST", "/api/logout", {}, cookie, csrf)[0], 200)
        self.assertEqual(self.request("GET", "/api/my-ledgers", cookie=cookie)[0], 401)

    def test_flat_custom_fields_and_optional_values(self):
        _, cookie, csrf = self.register()
        fields = [{"name": "body", "type": "string", "required": True},
                  {"name": "note", "type": "string", "required": False}]
        status, created, _ = self.request("POST", "/api/ledgers", {"name": "Messages", "description": "", "fields": fields}, cookie, csrf)
        self.assertEqual(status, 201)
        self.ledgers.append(created["ledger"]["id"])
        path = created["write_path"]
        token = created["write_token"]
        status, result, _ = self.request("POST", path, {"body": "hello"}, token=token)
        self.assertEqual(status, 201, result)
        self.assertEqual(result["block"]["data"], {"body": "hello"})
        self.assertTrue(result["block"]["record_id"].startswith("record-"))
        self.assertEqual(self.request("POST", path, {"body": "hello", "note": "optional"}, token=token)[0], 201)
        for invalid in ({}, {"body": True}, {"body": "hello", "note": None}, {"body": "hello", "other": 1}):
            self.assertEqual(self.request("POST", path, invalid, token=token)[0], 400)
        for key in ("", "with spaces", "genesis"):
            self.assertEqual(self.request("POST", path, {"body": "hello"}, token=token, headers={"Idempotency-Key": key})[0], 400)
        header = {"Idempotency-Key": "retry-message-1"}
        self.assertEqual(self.request("POST", path, {"body": "hello"}, token=token, headers=header)[0], 201)
        self.assertEqual(self.request("POST", path, {"body": "hello"}, token=token, headers=header)[0], 200)
        self.assertEqual(self.request("POST", path, {"body": "changed"}, token=token, headers=header)[0], 409)
        legacy = {"record_id": "old-client-1", "data": {"body": "legacy format"}}
        self.assertEqual(self.request("POST", path, legacy, token=token)[0], 400)
        before = self.store.get_ledger(created["ledger"]["id"])["block_count"]
        for invalid in ({"body": "bad\x00value"}, {"record_id": "bad\x00id", "data": {"body": "hello"}}):
            self.assertEqual(self.request("POST", path, invalid, token=token)[0], 400)
        self.assertEqual(self.request("POST", path, {"record_id": "with spaces", "data": {"body": "hello"}},
                                      token=token, headers={"Idempotency-Key": "with spaces"})[0], 400)
        self.assertEqual(self.store.get_ledger(created["ledger"]["id"])["block_count"], before)

    def test_admin_states_deletion_and_single_account(self):
        username = "admin_" + uuid.uuid4().hex[:12]
        admin, created = self.accounts.initialize_admin(username, PASSWORD)
        self.assertTrue(created)
        self.users.append(admin["id"])
        _, repeated = self.accounts.initialize_admin(username, "another-valid-password")
        self.assertFalse(repeated)
        self.assertEqual(self.accounts.login(username, PASSWORD)["id"], admin["id"])
        with self.assertRaises(ValueError):
            self.accounts.initialize_admin("another_admin", PASSWORD)
        _, logged, headers = self.request("POST", "/api/login", {"username": username, "password": PASSWORD})
        admin_cookie = headers["Set-Cookie"].split(";")[0]
        _, owner_cookie, csrf = self.register()
        created = self.create(owner_cookie, csrf)
        path = "/api/ledgers/" + created["ledger"]["id"]
        self.assertEqual(self.request("GET", path + "/credentials", cookie=admin_cookie)[0], 403)
        self.assertEqual(self.request("POST", path + "/rotate-token", {}, admin_cookie, logged["csrf_token"])[0], 403)
        for state in ("read_only", "archived"):
            self.assertEqual(self.request("PATCH", path, {"state": state}, admin_cookie, logged["csrf_token"])[0], 200)
            self.assertEqual(self.request("POST", path + "/blocks", {"value": 3}, token=created["write_token"])[0], 409)
        for forbidden in ({"fields": []}, {"blocks": []}, {"owner_id": admin["id"]}):
            self.assertEqual(self.request("PATCH", path, forbidden, admin_cookie, logged["csrf_token"])[0], 400)
        self.assertEqual(self.request("DELETE", path, {"confirm": "wrong"}, admin_cookie, logged["csrf_token"])[0], 400)
        self.assertEqual(self.request("DELETE", path, {"confirm": "Records"}, admin_cookie, logged["csrf_token"])[0], 200)
        self.assertEqual(self.request("GET", path)[0], 404)

    def test_snapshot_visual_status_and_tampering(self):
        _, cookie, csrf = self.register()
        created = self.create(cookie, csrf)
        path = "/api/ledgers/" + created["ledger"]["id"]
        self.request("POST", path + "/blocks", {"value": 1}, token=created["write_token"])
        status, snapshot, _ = self.request("GET", path + "/snapshot")
        self.assertEqual(status, 200)
        self.request("POST", path + "/blocks", {"value": 2}, token=created["write_token"])
        headers = {"Content-Type": "application/octet-stream"}
        status, report, _ = self.request("POST", path + "/validate-snapshot", snapshot, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual([row["status"] for row in report["block_statuses"]], ["verified", "verified", "not_in_snapshot"])
        self.assertEqual(report["snapshot_blocks"][1]["data"], {"value": 1})
        block_table, _ = table_names(created["ledger"]["id"])
        with self.store.connect() as connection:
            record = self.store.read(created["ledger"]["id"])["blocks"][1]
            record["data"]["value"] = 999
            connection.execute(sql.SQL("UPDATE {} SET record=%s WHERE position=1").format(sql.Identifier(block_table)), (Jsonb(record),))
        _, report, _ = self.request("POST", path + "/validate-snapshot", snapshot, headers=headers)
        self.assertFalse(report["valid"])
        self.assertEqual(report["block_statuses"][1]["status"], "changed")
        changed = bytearray(snapshot); changed[len(changed) // 2] ^= 1
        self.assertEqual(self.request("POST", path + "/validate-snapshot", bytes(changed), headers=headers)[0], 400)

    def test_origin_login_limits_and_static_boundaries(self):
        self.assertEqual(self.request("GET", "/api/ledgers", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/login", {}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/%2e%2e/secret")[0], 403)
        for _ in range(10):
            self.assertEqual(self.request("POST", "/api/login", {"username": "missing", "password": PASSWORD})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"username": "missing", "password": PASSWORD})[0], 429)

    def test_https_and_secure_cookie(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                       .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                       .not_valid_after(now + datetime.timedelta(days=1))
                       .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                       .sign(key, hashes.SHA256()))
        cert_path, key_path = Path(self.temp.name) / "test.pem", Path(self.temp.name) / "test.key"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.server = WebServer(("127.0.0.1", 0), self.store, self.accounts, self.temp.name, secure_cookies=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert_path, key_path)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True, do_handshake_on_connect=False)
        self.thread = threading.Thread(target=self.server.serve_forever); self.thread.start()
        client = ssl.create_default_context(cafile=str(cert_path))
        username = "tls_" + uuid.uuid4().hex[:12]
        # One unfinished TLS handshake must not stop an unrelated HTTPS client.
        stalled = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3)
        try:
            status, value, headers = self.request("POST", "/api/register", {"username": username, "password": PASSWORD}, tls=client)
        finally:
            stalled.close()
        self.assertEqual(status, 201, value)
        self.users.append(value["user"]["id"])
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertTrue(headers["Set-Cookie"].startswith("__Host-"))
        self.assertEqual(headers["Strict-Transport-Security"], "max-age=31536000")


if __name__ == "__main__":
    unittest.main()
