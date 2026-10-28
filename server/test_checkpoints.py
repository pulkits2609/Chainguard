import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg import sql
from psycopg.types.json import Jsonb

from checkpoints import (CheckpointService, key_document, load_signing_key,
                         sign_checkpoint, verify_checkpoint)
from ledger import LedgerConflict, calculate_hash, create_new_block
from store import Store, ledger_genesis, table_names


FIELDS = [{"name": "value", "type": "integer", "required": True}]
DATABASE_URL = os.environ.get("LEDGER_TEST_DATABASE_URL")


class CheckpointCryptoTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = key_document(self.key.public_key())
        self.ledger_id = str(uuid.uuid4())
        genesis = ledger_genesis(self.ledger_id, FIELDS)
        block = create_new_block(genesis, {"record_id": "first", "data": {"value": 7}})
        self.history = {"ledger": {"id": self.ledger_id, "fields": FIELDS}, "blocks": [genesis, block]}
        self.checkpoint = sign_checkpoint(self.key, self.ledger_id, 1, block["hash"])

    def test_signature_and_full_history_have_distinct_guarantees(self):
        signature = verify_checkpoint(self.checkpoint, self.public, self.ledger_id)
        self.assertTrue(signature["valid"])
        self.assertTrue(signature["signature_valid"])
        self.assertFalse(signature["history_verified"])
        full = verify_checkpoint(self.checkpoint, self.public, self.ledger_id, self.history)
        self.assertTrue(full["history_verified"])
        self.assertTrue(full["prefix_verified"])
        self.assertEqual(full["anchored_prefix_index"], 1)
        self.assertEqual(full["anchored_prefix_count"], 2)
        self.assertEqual(full["unanchored_suffix_count"], 0)
        self.assertEqual(full["verification_scope"], "full_record_history")

    def test_wrong_key_identity_tampered_fields_and_invalid_structure(self):
        self.assertFalse(verify_checkpoint(self.checkpoint, key_document(Ed25519PrivateKey.generate().public_key()), self.ledger_id)["valid"])
        self.assertFalse(verify_checkpoint(self.checkpoint, self.public, str(uuid.uuid4()))["valid"])
        for field, value in (("hash", "f" * 64), ("index", 2), ("index", True), ("index", -1),
                             ("created_at_ms", 0), ("format_version", True), ("signature", "!" * 64), ("key_id", "0" * 64)):
            changed = dict(self.checkpoint, **{field: value})
            self.assertFalse(verify_checkpoint(changed, self.public, self.ledger_id)["valid"], field)
        self.assertFalse(verify_checkpoint(dict(self.checkpoint, extra="field"), self.public, self.ledger_id)["valid"])
        changed_key = dict(self.public, key_id="0" * 64)
        self.assertFalse(verify_checkpoint(self.checkpoint, changed_key, self.ledger_id)["valid"])

    def test_old_prefix_passes_new_history_but_rebuilt_history_fails(self):
        blocks = self.history["blocks"]
        blocks.append(create_new_block(blocks[-1], {"record_id": "second", "data": {"value": 8}}))
        older = verify_checkpoint(self.checkpoint, self.public, self.ledger_id, self.history)
        self.assertTrue(older["valid"])
        self.assertTrue(older["prefix_verified"])
        self.assertFalse(older["history_verified"])
        self.assertEqual(older["verification_scope"], "checkpointed_prefix")
        self.assertEqual(older["unanchored_suffix_count"], 1)
        changed = copy.deepcopy(self.history)
        changed["blocks"][1]["data"]["value"] = 99
        changed["blocks"][1]["hash"] = calculate_hash(changed["blocks"][1])
        changed["blocks"][2]["previous_hash"] = changed["blocks"][1]["hash"]
        changed["blocks"][2]["hash"] = calculate_hash(changed["blocks"][2])
        report = verify_checkpoint(self.checkpoint, self.public, self.ledger_id, changed)
        self.assertTrue(report["signature_valid"])
        self.assertFalse(report["history_verified"])
        self.assertFalse(report["valid"])
        changed["blocks"].pop(1)
        self.assertFalse(verify_checkpoint(self.checkpoint, self.public, self.ledger_id, changed)["valid"])

    def test_consistently_rewritten_suffix_is_never_claimed_as_anchored_history(self):
        blocks = self.history["blocks"]
        blocks.append(create_new_block(blocks[-1], {"record_id": "unanchored", "data": {"value": 8}}))
        blocks[-1]["data"]["value"] = 999
        blocks[-1]["hash"] = calculate_hash(blocks[-1])
        report = verify_checkpoint(self.checkpoint, self.public, self.ledger_id, self.history)
        self.assertTrue(report["valid"])
        self.assertTrue(report["prefix_verified"])
        self.assertFalse(report["history_verified"])
        self.assertEqual(report["anchored_prefix_index"], 1)
        self.assertEqual(report["anchored_prefix_count"], 2)
        self.assertEqual(report["unanchored_suffix_count"], 1)
        self.assertEqual(report["verification_scope"], "checkpointed_prefix")

    def test_keys_persist_and_invalid_or_missing_keys_are_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.key"
            first = key_document(load_signing_key(path).public_key())
            self.assertEqual(first, key_document(load_signing_key(path).public_key()))
            path.write_bytes(b"invalid")
            with self.assertRaises(ValueError):
                load_signing_key(path)
            self.assertEqual(path.read_bytes(), b"invalid")
            with self.assertRaises(ValueError):
                load_signing_key(Path(directory) / "missing.key", allow_create=False)

    def test_offline_cli_needs_explicit_key_and_checks_history_and_duplicate_keys(self):
        cli = Path(__file__).resolve().parents[1] / "tools" / "verify_checkpoint.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (("checkpoint", self.checkpoint), ("key", self.public), ("history", self.history)):
                (root / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
            command = [sys.executable, str(cli), "--checkpoint", str(root / "checkpoint.json"), "--public-key", str(root / "key.json"), "--ledger-id", self.ledger_id]
            result = subprocess.run(command + ["--history", str(root / "history.json")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["history_verified"])
            self.assertFalse(json.loads(subprocess.run(command, capture_output=True, text=True).stdout)["history_verified"])
            (root / "checkpoint.json").write_text('{"format_version":1,"format_version":2}', encoding="utf-8")
            invalid = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(invalid.returncode, 1)
            self.assertIn("Duplicate JSON key", invalid.stdout)

    def test_offline_history_envelope_preserves_allowed_record_depth(self):
        fields = [{"name": "payload", "type": "object", "required": True}]
        nested = "leaf"
        for _ in range(18):
            nested = {"nested": nested}
        genesis = ledger_genesis(self.ledger_id, fields)
        block = create_new_block(genesis, {"record_id": "deep", "data": {"payload": nested}})
        checkpoint = sign_checkpoint(self.key, self.ledger_id, 1, block["hash"])
        history = {"ledger": {"id": self.ledger_id, "fields": fields}, "blocks": [genesis, block]}
        cli = Path(__file__).resolve().parents[1] / "tools" / "verify_checkpoint.py"
        with tempfile.TemporaryDirectory() as directory:
            paths = {name: Path(directory) / (name + ".json") for name in ("checkpoint", "key", "history")}
            for name, value in (("checkpoint", checkpoint), ("key", self.public), ("history", history)):
                paths[name].write_text(json.dumps(value), encoding="utf-8")
            result = subprocess.run([sys.executable, str(cli), "--checkpoint", str(paths["checkpoint"]),
                       "--public-key", str(paths["key"]), "--ledger-id", self.ledger_id, "--history", str(paths["history"])], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(DATABASE_URL, "Set LEDGER_TEST_DATABASE_URL to chainguard_test")
class CheckpointDatabaseTests(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertEqual(connection.info.dbname, "chainguard_test", "Refusing evidence tests outside disposable chainguard_test")
        self.store = Store(DATABASE_URL)
        self.temp = tempfile.TemporaryDirectory()
        self.key_path = Path(self.temp.name) / "signing.key"
        self.service = CheckpointService(self.store, self.key_path)
        self.created = self.store.create_ledger("checkpoint-test-" + uuid.uuid4().hex, "Checkpoint tests", "", FIELDS)
        self.ledger_id = self.created["ledger"]["id"]
        self.store.append(self.ledger_id, self.created["write_token"], {"value": 7}, "first")

    def tearDown(self):
        self.store.delete_ledger(self.ledger_id)
        with self.store.connect() as connection:
            connection.execute("DELETE FROM signing_key_bindings WHERE key_id=%s", (self.service.verification_key["key_id"],))
            connection.execute("DELETE FROM verification_keys WHERE key_id=%s", (self.service.verification_key["key_id"],))
        self.temp.cleanup()

    def test_create_export_restart_and_old_prefix(self):
        checkpoint = self.service.create(self.ledger_id)
        self.assertEqual(self.service.get(self.ledger_id, checkpoint["checkpoint_id"]), checkpoint)
        self.assertEqual(self.service.list(self.ledger_id)["total"], 1)
        self.store.append(self.ledger_id, self.created["write_token"], {"value": 8}, "second")
        restarted = CheckpointService(self.store, self.key_path)
        self.assertEqual(restarted.verification_key, self.service.verification_key)
        older = restarted.verify(self.ledger_id, checkpoint)
        self.assertTrue(older["valid"])
        self.assertTrue(older["prefix_verified"])
        self.assertFalse(older["history_verified"])
        self.assertEqual(older["unanchored_suffix_count"], 1)
        self.assertEqual(restarted.public_key(checkpoint["key_id"]), self.service.verification_key)
        self.key_path.unlink()
        with self.assertRaises(ValueError):
            CheckpointService(self.store, self.key_path)
        self.assertFalse(self.key_path.exists())

    def test_checkpoint_rejects_corrupt_chain_and_detects_rebuilt_history(self):
        checkpoint = self.service.create(self.ledger_id)
        block = self.store.read(self.ledger_id)["blocks"][1]
        block["data"]["value"] = 99
        table = sql.Identifier(table_names(self.ledger_id)[0])
        with self.store.connect() as connection:
            connection.execute(sql.SQL("UPDATE {} SET record=%s WHERE position=1").format(table), (Jsonb(block),))
        with self.assertRaises(LedgerConflict):
            self.service.create(self.ledger_id)
        block["hash"] = calculate_hash(block)
        with self.store.connect() as connection:
            connection.execute(sql.SQL("UPDATE {} SET record=%s WHERE position=1").format(table), (Jsonb(block),))
        self.assertFalse(self.store.read(self.ledger_id)["verification"]["valid"])
        # Model a privileged attacker rewriting the stored head reference too.
        with self.store.connect() as connection:
            connection.execute("UPDATE ledger_registry SET head_hash=%s WHERE id=%s", (block["hash"], self.ledger_id))
        self.assertTrue(self.store.read(self.ledger_id)["verification"]["valid"])
        self.assertFalse(self.service.verify(self.ledger_id, checkpoint)["valid"])

    def test_checkpoint_and_audit_commit_or_rollback_together(self):
        class UnavailableAudit:
            def record(self, *args, connection=None, **kwargs):
                if connection is None:
                    raise AssertionError("Audit must use the checkpoint transaction")
                raise RuntimeError("Test audit storage unavailable")
        self.service.audit = UnavailableAudit()
        with self.assertRaises(RuntimeError):
            self.service.create(self.ledger_id)
        self.assertEqual(self.service.list(self.ledger_id)["total"], 0)


if __name__ == "__main__":
    unittest.main()
