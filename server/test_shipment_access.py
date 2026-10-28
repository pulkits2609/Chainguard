import datetime
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from checkpoints import CheckpointService
from ledger import calculate_hash
from shipment_access import (ShipmentAccessService, ShipmentRateLimit, grant_expiration,
                             project_event, shipment_identifier)
from store import Store, StoreForbidden, table_names


DATABASE_URL = os.environ.get("LEDGER_TEST_DATABASE_URL")
FIELDS = [{"name": name, "type": "string", "required": name in ("shipment_id", "event_type", "event_time")}
          for name in ("shipment_id", "event_type", "event_time", "operator_id", "location", "source_system", "reference_id")]


class ShipmentValidationTests(unittest.TestCase):
    def test_identifiers_expiry_and_redaction(self):
        self.assertEqual(shipment_identifier("SHP-1"), "SHP-1")
        for invalid in ("", "../secret", "id with spaces", "\n", None):
            with self.assertRaises(ValueError):
                shipment_identifier(invalid)
        now = int(time.time())
        self.assertEqual(grant_expiration(None, now, 3600, 86400), now + 3600)
        self.assertEqual(grant_expiration(now + 600, now, 3600, 86400), now + 600)
        timestamp = datetime.datetime.fromtimestamp(now + 600, datetime.timezone.utc).isoformat()
        self.assertEqual(grant_expiration(timestamp, now, 3600, 86400), now + 600)
        for invalid in (now, True, now + 86401, "2026-01-01T00:00:00"):
            with self.assertRaises(ValueError):
                grant_expiration(invalid, now, 3600, 86400)
        event = project_event({"index": 2, "timestamp_ms": 123, "data": {"shipment_id": "SHP-1", "event_type": "RECORDED", "operator_id": "PRIVATE", "reference_id": "PRIVATE", "secret": "PRIVATE"}})
        self.assertNotIn("PRIVATE", json.dumps(event))
        self.assertEqual(event["event_type"], "RECORDED")


@unittest.skipUnless(DATABASE_URL, "Set LEDGER_TEST_DATABASE_URL to chainguard_test")
class ShipmentDatabaseTests(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertEqual(connection.info.dbname, "chainguard_test", "Refusing shipment tests outside disposable chainguard_test")
        self.store = Store(DATABASE_URL)
        self.temp = tempfile.TemporaryDirectory()
        self.checkpoints = CheckpointService(self.store, Path(self.temp.name) / "signing.key")
        self.service = ShipmentAccessService(self.store, self.checkpoints)
        self.user = {"id": "shipment-test-" + uuid.uuid4().hex, "is_admin": False}
        self.created = self.store.create_ledger(self.user["id"], "Shipment isolation", "", FIELDS)
        self.ledger_id = self.created["ledger"]["id"]
        for identifier in ("SHP-1", "SHP-2"):
            self.append(identifier, identifier)

    def append(self, shipment_id, record_id):
        return self.store.append(self.ledger_id, self.created["write_token"],
                  {"shipment_id": shipment_id, "event_type": "RESOURCE_CREATED", "event_time": "2026-10-29T00:00:00Z", "operator_id": "PRIVATE-EMPLOYEE", "reference_id": "PRIVATE-REFERENCE"}, record_id)

    def tearDown(self):
        self.store.delete_ledger(self.ledger_id)
        with self.store.connect() as connection:
            connection.execute("DELETE FROM signing_key_bindings WHERE key_id=%s", (self.checkpoints.verification_key["key_id"],))
            connection.execute("DELETE FROM verification_keys WHERE key_id=%s", (self.checkpoints.verification_key["key_id"],))
        self.temp.cleanup()

    def test_scope_redaction_pagination_restart_and_revoke(self):
        created = self.service.create(self.ledger_id, "SHP-1", user=self.user)
        token = created["access_token"]
        self.assertNotIn(token, json.dumps(self.service.list(self.ledger_id, user=self.user)))
        with self.store.connect() as connection:
            raw = connection.execute("SELECT * FROM shipment_access_grants WHERE grant_id=%s", (created["grant"]["grant_id"],)).fetchone()
            self.assertNotIn(token.split(".")[1], json.dumps(raw))
        events = self.service.events("SHP-1", token)
        self.assertEqual(events["total"], 1)
        self.assertFalse(events["verification"]["independently_verified"])
        self.assertNotIn("PRIVATE", json.dumps(events))
        self.assertNotIn("SHP-2", json.dumps(events))
        with self.assertRaises(StoreForbidden):
            self.service.events("SHP-2", token)
        with self.assertRaises(StoreForbidden):
            self.service.events("SHP-1", "wrong")
        self.append("SHP-1", "later")
        self.assertEqual(self.service.events("SHP-1", token, limit=1)["total"], 2)
        restarted = ShipmentAccessService(self.store, self.checkpoints)
        self.assertEqual(restarted.events("SHP-1", token)["total"], 2)
        self.service.revoke(self.ledger_id, created["grant"]["grant_id"], user=self.user)
        with self.assertRaises(StoreForbidden):
            restarted.events("SHP-1", token)

    def test_owner_authorization_expiration_and_customer_cannot_manage(self):
        for user in (None, {"id": "another-owner", "is_admin": False}):
            with self.assertRaises(StoreForbidden):
                self.service.create(self.ledger_id, "SHP-1", user=user)
            with self.assertRaises(StoreForbidden):
                self.service.list(self.ledger_id, user=user)
        created = self.service.create(self.ledger_id, "SHP-1", user={"id": "admin", "is_admin": True})
        with self.store.connect() as connection:
            connection.execute("UPDATE shipment_access_grants SET expires_at=%s WHERE grant_id=%s", (int(time.time()) - 1, created["grant"]["grant_id"]))
        with self.assertRaises(StoreForbidden):
            self.service.events("SHP-1", created["access_token"])

    def test_checkpoint_signature_is_separate_from_server_history_and_tamper_failure(self):
        created = self.service.create(self.ledger_id, "SHP-1", user=self.user)
        token = created["access_token"]
        self.assertIsNone(self.service.proof("SHP-1", token)["checkpoint"])
        checkpoint = self.checkpoints.create(self.ledger_id)
        proof = self.service.proof("SHP-1", token)
        self.assertEqual(proof["checkpoint"], checkpoint)
        self.assertTrue(proof["verification"]["checkpoint_signature_verified"])
        self.assertTrue(proof["verification"]["server_records_verified"])
        self.assertFalse(proof["verification"]["independently_verified"])
        self.assertNotIn("blocks", proof)
        block = self.store.read(self.ledger_id)["blocks"][1]
        block["data"]["event_type"] = "ALTERED"
        block["hash"] = calculate_hash(block)
        second = self.store.read(self.ledger_id)["blocks"][2]
        second["previous_hash"] = block["hash"]
        second["hash"] = calculate_hash(second)
        table = sql.Identifier(table_names(self.ledger_id)[0])
        with self.store.connect() as connection:
            for value in (block, second):
                connection.execute(sql.SQL("UPDATE {} SET record=%s WHERE position=%s").format(table), (Jsonb(value), value["index"]))
        self.assertFalse(self.store.read(self.ledger_id)["verification"]["valid"])
        # A database administrator can also rewrite the stored head reference;
        # the previously signed checkpoint must still expose that rewrite.
        with self.store.connect() as connection:
            connection.execute("UPDATE ledger_registry SET head_hash=%s WHERE id=%s", (second["hash"], self.ledger_id))
        self.assertTrue(self.store.read(self.ledger_id)["verification"]["valid"])
        failed = self.service.events("SHP-1", token)["verification"]
        self.assertEqual(failed["status"], "verification_failed")
        self.assertTrue(failed["checkpoint_signature_verified"])
        self.assertFalse(failed["server_records_verified"])

    def test_customer_rate_limit_persists_across_service_restart(self):
        created = self.service.create(self.ledger_id, "SHP-1", user=self.user)
        with self.store.connect() as connection:
            connection.execute("INSERT INTO customer_rate_windows VALUES (%s,%s,60)", (created["grant"]["grant_id"], int(time.time()) // 60))
        restarted = ShipmentAccessService(self.store, self.checkpoints)
        with self.assertRaises(ShipmentRateLimit):
            restarted.events("SHP-1", created["access_token"])

    def test_corrupt_checkpoint_metadata_is_not_disclosed_as_customer_proof(self):
        created = self.service.create(self.ledger_id, "SHP-1", user=self.user)
        checkpoint = self.checkpoints.create(self.ledger_id)
        with self.store.connect() as connection:
            connection.execute("UPDATE signed_checkpoints SET checkpoint=%s WHERE checkpoint_id=%s",
                               (Jsonb({**checkpoint, "private_payload": "PRIVATE-METADATA"}), checkpoint["checkpoint_id"]))
        proof = self.service.proof("SHP-1", created["access_token"])
        self.assertEqual(proof["verification"]["status"], "verification_failed")
        self.assertIsNone(proof["checkpoint"])
        self.assertNotIn("PRIVATE-METADATA", json.dumps(proof))

    def test_grant_creation_and_revocation_rollback_when_audit_cannot_commit(self):
        class UnavailableAudit:
            def record(self, *args, connection=None, **kwargs):
                if connection is None:
                    raise AssertionError("Audit must use the grant transaction")
                raise RuntimeError("Test audit storage unavailable")
        self.service.audit = UnavailableAudit()
        with self.assertRaises(RuntimeError):
            self.service.create(self.ledger_id, "SHP-1", user=self.user)
        self.assertEqual(self.service.list(self.ledger_id, user=self.user)["total"], 0)
        self.service.audit = None
        grant = self.service.create(self.ledger_id, "SHP-1", user=self.user)
        self.service.audit = UnavailableAudit()
        with self.assertRaises(RuntimeError):
            self.service.revoke(self.ledger_id, grant["grant"]["grant_id"], user=self.user)
        self.assertFalse(self.service.list(self.ledger_id, user=self.user)["grants"][0]["revoked"])
        self.assertTrue(self.service.events("SHP-1", grant["access_token"])["verification"]["server_records_verified"])


if __name__ == "__main__":
    unittest.main()
