"""Integration policies and real PostgreSQL ingestion; never use the original DB."""

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from psycopg.conninfo import conninfo_to_dict

from integrations import (IntegrationService, IntegrationError, extract_path,
                          header_value, is_integration_token, scoped_record_id, timestamp_seconds,
                          validate_policy)
from ledger import LedgerConflict, canonical_json
from security_audit import AuditService
from store import Store, StoreForbidden, StoreNotFound


DATABASE_URL = os.environ.get("LEDGER_TEST_DATABASE_URL")
FIELDS = [{"name": name, "type": "string", "required": True}
          for name in ("event_type", "source_system", "event_time")]


def event_time(offset=0):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=offset)).isoformat(timespec="seconds").replace("+00:00", "Z")


class IntegrationValidationTests(unittest.TestCase):
    def test_policies_cannot_disable_auth_or_exceed_global_limits(self):
        policy = validate_policy({})
        self.assertEqual(policy["allowed_operations"], ["append"])
        for changes in ({"require_authentication": False}, {"enabled": 1}, {"requests_per_minute": True},
                        {"requests_per_minute": 601}, {"max_body_bytes": 65537}, {"unknown": 3},
                        {"allowed_operations": ["admin"]}, {"allowed_operations": ["append", "append"]},
                        {"allowed_source_cidrs": ["not an IP"]}, {"credential_expires_at": "2026-01-01"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_policy(changes)
        self.assertEqual(validate_policy({"allowed_source_cidrs": ["192.0.2.5/24", "::1"]})["allowed_source_cidrs"],
                         ["192.0.2.0/24", "::1/128"])

    def test_mapping_is_explicit_and_timestamp_has_timezone(self):
        self.assertEqual(extract_path({"a": {"b": 4}}, "a.b"), (4, True))
        self.assertEqual(extract_path({}, "a", required=False), (None, False))
        for path in ("__import__('os')", "a[0]", "a..b", "a;delete", "x" * 257):
            with self.assertRaises(ValueError):
                extract_path({}, path)
        for stamp in ("2026-10-29T01:00:00", "2026-10-29", "20261029T010000Z", "2026-10-29T01:00:00+0000", None):
            with self.assertRaises(ValueError):
                timestamp_seconds(stamp)
        self.assertEqual(timestamp_seconds("2026-10-29T01:00:00Z"), timestamp_seconds("2026-10-29T06:30:00+05:30"))

    def test_event_ids_are_integration_scoped_and_bounded(self):
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertEqual(scoped_record_id(first, "event-1"), scoped_record_id(first, "event-1"))
        self.assertNotEqual(scoped_record_id(first, "event-1"), scoped_record_id(second, "event-1"))
        self.assertLessEqual(len(scoped_record_id(first, "a" * 128)), 128)
        for value in ("", "a b", "é", "a" * 129, 5):
            with self.assertRaises(ValueError):
                scoped_record_id(first, value)
        self.assertTrue(is_integration_token("cg_" + "a" * 32 + "_" + "b" * 43))
        self.assertFalse(is_integration_token("cg_opaque"))
        self.assertFalse(is_integration_token("legacy-token"))

    def test_duplicate_authentication_headers_are_rejected(self):
        from email.message import Message
        headers = Message()
        headers.add_header("Authorization", "Bearer first")
        headers.add_header("Authorization", "Bearer second")
        with self.assertRaises(IntegrationError):
            header_value(headers, "Authorization")
        self.assertEqual(header_value({"authorization": "Bearer token"}, "Authorization"), "Bearer token")


@unittest.skipUnless(DATABASE_URL, "Set LEDGER_TEST_DATABASE_URL to chainguard_test")
class IntegrationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if conninfo_to_dict(DATABASE_URL).get("dbname") != "chainguard_test":
            raise RuntimeError("Integration tests require the isolated chainguard_test database")

    def setUp(self):
        self.store = Store(DATABASE_URL)
        self.user = {"id": "integration-test-" + uuid.uuid4().hex, "is_admin": False}
        self.admin = {"id": "integration-admin", "is_admin": True}
        self.other = {"id": "another-owner", "is_admin": False}
        self.directory = tempfile.TemporaryDirectory()
        self.key = Path(self.directory.name) / "integration.key"
        self.service = IntegrationService(self.store, self.key)
        self.created = self.store.create_ledger(self.user["id"], "Integration test", "Disposable fixture", FIELDS)
        self.ledger_id = self.created["ledger"]["id"]
        self.integration_ids = []
        self.peer = "192.0.2." + str(1 + uuid.uuid4().int % 253)

    def tearDown(self):
        self.store.delete_ledger(self.ledger_id)
        with self.store.connect() as connection:
            connection.execute("DELETE FROM security_events WHERE ledger_id=%s", (self.ledger_id,))
            identities = ["integration:" + value for value in self.integration_ids]
            identities.append("source:" + hashlib.sha256(self.peer.encode()).hexdigest())
            connection.execute("DELETE FROM integration_rate_windows WHERE identity=ANY(%s)", (identities,))
        self.directory.cleanup()

    def create(self, **settings):
        result = self.service.create(self.user, {"ledger_id": self.ledger_id, "name": "Controlled application", **settings})
        self.integration_ids.append(result["integration"]["id"])
        return result

    def event(self, **changes):
        return {"event_id": "resource-created-1", "event_type": "RESOURCE_CREATED",
                "source_system": "controlled-test-application", "event_time": event_time(), **changes}

    def ingest(self, created, value=None, secret=None):
        value = self.event() if value is None else value
        raw = canonical_json(value)
        secret = created["secret"] if secret is None else secret
        if created["integration"]["auth_method"] == "hmac":
            headers = {"X-ChainGuard-Signature": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}
        else:
            headers = {"Authorization": "Bearer " + secret}
        return self.service.ingest(created["integration"]["id"], raw, headers, self.peer)

    def test_bearer_mapping_dedup_conflict_rotation_and_restart(self):
        created = self.create()
        value = self.event()
        block, duplicate = self.ingest(created, value)
        self.assertFalse(duplicate)
        self.assertNotIn("event_id", block["data"])
        restarted = IntegrationService(self.store, self.key)
        raw = canonical_json(value)
        repeated, duplicate = restarted.ingest(created["integration"]["id"], raw,
            {"Authorization": "Bearer " + created["secret"]}, self.peer)
        self.assertTrue(duplicate)
        self.assertEqual(block, repeated)
        with self.assertRaises(LedgerConflict):
            self.ingest(created, {**value, "event_type": "LOGOUT"})
        rotated = self.service.rotate_secret(created["integration"]["id"], self.user)
        with self.assertRaises(IntegrationError) as caught:
            self.ingest(created, value)
        self.assertEqual(caught.exception.status, 401)
        self.assertTrue(self.ingest(rotated, value)[1])
        metadata = self.service.get(created["integration"]["id"], self.user)
        self.assertIsNotNone(metadata["last_accepted_at"])
        for sensitive in (created["secret"], rotated["secret"], "secret_hash", "encrypted_secret"):
            self.assertNotIn(sensitive, json.dumps(metadata))

    def test_hmac_authenticates_exact_bytes_and_bounded_signed_time(self):
        created = self.create(auth_method="hmac")
        value = self.event()
        self.assertFalse(self.ingest(created, value)[1])
        self.assertTrue(self.ingest(created, value)[1])
        for offset in (-600, 600):
            with self.assertRaises(IntegrationError) as caught:
                self.ingest(created, self.event(event_id="expired-" + str(offset), event_time=event_time(offset)))
            self.assertEqual(caught.exception.reason_code, "expired_signature")
        with self.assertRaises(IntegrationError) as caught:
            self.ingest(created, self.event(event_id="bad-signature"), secret="wrong-secret")
        self.assertEqual(caught.exception.status, 401)
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM integrations WHERE id=%s", (created["integration"]["id"],)).fetchone()
        self.assertNotIn(created["secret"], json.dumps(row))
        original_key = self.key.read_bytes()
        IntegrationService(self.store, self.key)
        self.assertEqual(self.key.read_bytes(), original_key)
        self.key.unlink()
        with self.assertRaisesRegex(ValueError, "key is missing or invalid"):
            IntegrationService(self.store, self.key)
        self.assertFalse(self.key.exists())
        self.key.write_bytes(original_key)
        self.key.write_bytes(b"invalid-key")
        with self.assertRaises(ValueError):
            IntegrationService(self.store, self.key)
        self.assertEqual(self.key.read_bytes(), b"invalid-key")
        with self.assertRaises(IntegrationError) as caught:
            self.ingest(created, self.event(event_id="missing-key"))
        self.assertEqual(caught.exception.status, 503)
        self.key.write_bytes(original_key)

    def test_authentication_precedes_json_parse_and_failed_requests_do_not_append(self):
        created = self.create()
        before = self.store.get_ledger(self.ledger_id)["block_count"]
        with self.assertRaises(IntegrationError) as caught:
            self.service.append("cg_invalid", self.ledger_id, b"not JSON", "bad", self.peer)
        self.assertEqual(caught.exception.status, 401)
        with self.assertRaises(ValueError):
            self.service.append(created["secret"], self.ledger_id, b"not JSON", "bad", self.peer)
        with self.assertRaises(ValueError):
            self.service.ingest(created["integration"]["id"], b'{"event_id":"x","event_id":"y"}',
                {"Authorization": "Bearer " + created["secret"]}, self.peer)
        self.assertEqual(self.store.get_ledger(self.ledger_id)["block_count"], before)

    def test_permissions_expiry_ip_size_and_policy_isolation(self):
        created, other = self.create(), self.create()
        identifier = created["integration"]["id"]
        with self.assertRaises(IntegrationError) as caught:
            self.service.authorize(created["secret"], self.ledger_id, "read", self.peer)
        self.assertEqual(caught.exception.status, 403)
        self.service.update_policy(identifier, self.user, {"allowed_operations": ["append", "read"]})
        with self.assertRaises(IntegrationError):
            self.service.authorize(created["secret"], self.ledger_id, "read", self.peer)
        self.service.update_policy(identifier, self.user, {"allowed_source_cidrs": ["203.0.113.0/24"]})
        with self.assertRaises(IntegrationError) as caught:
            self.ingest(created)
        self.assertEqual(caught.exception.reason_code, "source_denied")
        self.service.update_policy(identifier, self.user, {"allowed_source_cidrs": [self.peer], "max_body_bytes": 10})
        with self.assertRaises(IntegrationError) as caught:
            self.ingest(created)
        self.assertEqual(caught.exception.status, 413)
        self.service.update_policy(identifier, self.user, {"max_body_bytes": 65536, "credential_expires_at": event_time(-5)})
        with self.assertRaises(IntegrationError) as caught:
            self.ingest(created)
        self.assertEqual(caught.exception.reason_code, "credential_expired")
        self.assertIsNone(self.service.policy(other["integration"]["id"], self.user)["credential_expires_at"])
        self.assertFalse(self.ingest(other)[1])

    def test_rate_limiting_is_per_integration_and_persists_restart(self):
        created, other = self.create(policy={"requests_per_minute": 1}), self.create()
        value = self.event()
        clock = time.time()
        with patch("integrations.time.time", return_value=clock):
            self.ingest(created, value)
            restarted = IntegrationService(self.store, self.key)
            with self.assertRaises(IntegrationError) as caught:
                restarted.authorize(created["secret"], self.ledger_id, "append", self.peer)
            self.assertEqual(caught.exception.status, 429)
            self.assertGreaterEqual(caught.exception.retry_after, 1)
            self.assertFalse(self.ingest(other)[1])
        # Advancing only the clock tests the actual window reset without deleting state.
        with patch("integrations.time.time", return_value=clock + 61):
            self.assertTrue(self.ingest(created, value)[1])

    def test_anonymous_rate_limit_is_persistent(self):
        with self.store.connect() as connection:
            connection.execute("INSERT INTO integration_rate_windows VALUES (%s,%s,120)",
                ("source:" + hashlib.sha256(self.peer.encode()).hexdigest(), int(time.time()) // 60))
        with self.assertRaises(IntegrationError) as caught:
            self.service.throttle(self.peer)
        self.assertEqual(caught.exception.status, 429)

    def test_authorization_revocation_and_audit_do_not_leak_secrets(self):
        created = self.create()
        identifier = created["integration"]["id"]
        for user in (None, self.other):
            with self.assertRaises(StoreForbidden):
                self.service.get(identifier, user)
            with self.assertRaises(StoreForbidden):
                self.service.update_policy(identifier, user, {"enabled": False})
        self.assertEqual(self.service.list(self.other), [])
        self.assertEqual(self.service.get(identifier, self.admin)["id"], identifier)
        self.service.update(identifier, self.user, {"enabled": False})
        with self.assertRaises(IntegrationError):
            self.ingest(created)
        self.service.update(identifier, self.user, {"enabled": True})
        self.service.delete(identifier, self.user)
        with self.assertRaises(StoreNotFound):
            self.service.get(identifier, self.user)
        with self.assertRaises(IntegrationError):
            self.ingest(created)
        self.assertFalse(self.service.list(self.user))
        events = self.service.audit.list(self.admin, {"ledger_id": self.ledger_id})["events"]
        self.assertTrue(any(row["category"] == "integration_revoked" for row in events))
        self.assertNotIn(created["secret"], json.dumps(events))
        with self.assertRaises(StoreForbidden):
            self.service.audit.list(self.user)

    def test_concurrent_duplicate_and_distinct_producers_share_existing_engine(self):
        created = self.create()
        value = self.event()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.ingest(created, value), range(4)))
        self.assertEqual(sum(not duplicate for _, duplicate in results), 1)
        second = self.create()
        self.assertFalse(self.ingest(second, value)[1])
        self.assertEqual(self.store.get_ledger(self.ledger_id)["block_count"], 3)
        self.assertTrue(self.store.read(self.ledger_id)["verification"]["valid"])

    def test_explicit_mapping_and_audit_retention_pagination(self):
        created = self.create(field_mapping={"event_type": "activity.action", "source_system": "activity.source", "event_time": "sent_at"},
                              event_id_field="id", timestamp_field="sent_at")
        block, _ = self.ingest(created, {"id": "login-1", "sent_at": event_time(),
            "activity": {"action": "LOGIN", "source": "controlled-application"}, "ignored_private_field": "not-recorded"})
        self.assertEqual(block["data"]["event_type"], "LOGIN")
        self.assertNotIn("ignored_private_field", json.dumps(block))
        audit = AuditService(self.store)
        audit.record("verification_failed", "verify", "failure", "invalid_chain", ledger_id=self.ledger_id, severity="error")
        page = audit.list(self.admin, {"ledger_id": self.ledger_id, "limit": 1})
        self.assertEqual(len(page["events"]), 1)
        self.assertTrue(page["has_more"])
        self.assertEqual(len(audit.list(self.admin, {"ledger_id": self.ledger_id, "severity": "error"})["events"]), 1)
        with self.assertRaises(ValueError):
            audit.record("bad", "append", "failure", "secret with spaces")
        with self.store.connect() as connection:
            connection.execute("UPDATE security_events SET timestamp_ms=0 WHERE ledger_id=%s", (self.ledger_id,))
        audit.record("policy_changed", "configure", "success", "policy_updated", ledger_id=self.ledger_id)
        self.assertEqual(len(audit.list(self.admin, {"ledger_id": self.ledger_id})["events"]), 1)

    def test_read_permission_needs_credential_scope_and_policy(self):
        created = self.create(credential_scopes=["append", "read", "verify", "snapshot_export"],
                              policy={"allowed_operations": ["append", "read", "verify", "snapshot_export"]})
        for operation in ("read", "verify", "snapshot_export"):
            self.assertEqual(self.service.authorize(created["secret"], self.ledger_id, operation, self.peer)["id"],
                             created["integration"]["id"])
        self.service.update_policy(created["integration"]["id"], self.user, {"allowed_operations": ["append"]})
        for operation in ("read", "verify", "snapshot_export", "configure"):
            with self.assertRaises(IntegrationError):
                self.service.authorize(created["secret"], self.ledger_id, operation, self.peer)
        with self.assertRaises(ValueError):
            self.service.update(created["integration"]["id"], self.user, {"credential_scopes": ["read"]})


if __name__ == "__main__":
    unittest.main()
