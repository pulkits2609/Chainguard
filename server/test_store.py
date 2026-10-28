"""Schema checks always run; database checks use an explicitly selected test DB."""

import copy
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import Fernet
from psycopg import sql
from psycopg.types.json import Jsonb

from field_schema import validate_data, validate_fields
from ledger import LedgerConflict, calculate_hash, create_new_block
from multi_snapshots import SnapshotService
from store import Store, StoreForbidden, StoreNotFound, ledger_genesis, table_names, verify_ledger, prepare_submission


FIELDS = [{"name": "value", "type": "integer", "required": True},
          {"name": "label", "type": "string", "required": False}]


class FieldTests(unittest.TestCase):
    def test_direct_fields_optional_omission_and_metadata(self):
        fields = [{"name": "body", "type": "string", "required": True},
                  {"name": "note", "type": "string", "required": False}]
        first = prepare_submission({"body": "hello"}, fields)
        self.assertEqual(first["data"], {"body": "hello"})
        self.assertNotEqual(first["record_id"], prepare_submission({"body": "hello"}, fields)["record_id"])
        self.assertEqual(prepare_submission({"body": "hello"}, fields, "retry-1")["record_id"], "retry-1")
        for invalid in ({}, {"body": 4}, {"body": "hello", "note": 4}, {"body": "hello", "extra": 1}):
            with self.assertRaises(ValueError):
                prepare_submission(invalid, fields)
        legacy = {"record_id": "wrapper-is-not-data", "data": {"body": "hello"}}
        with self.assertRaises(ValueError):
            prepare_submission(legacy, fields)
        for identifier in ("with spaces", "\u00e9", "", "x" * 129):
            with self.assertRaises(ValueError):
                prepare_submission({"record_id": identifier, "data": {"body": "hello"}}, fields, identifier)
        for invalid in ({"body": "hello\x00world"}, {"record_id": "bad\x00id", "data": {"body": "hello"}}):
            with self.assertRaises(ValueError):
                prepare_submission(invalid, fields)
        nested_fields = [{"name": "payload", "type": "object", "required": True}]
        for payload in ({"nested": ["bad\x00text"]}, {"bad\x00key": "value"}):
            with self.assertRaises(ValueError):
                prepare_submission({"payload": payload}, nested_fields)
        with self.assertRaises(ValueError):
            prepare_submission(legacy, fields, "different-id")
        optional = [{"name": "body", "type": "string", "required": False}]
        self.assertEqual(prepare_submission({}, optional)["data"], {})
        reserved = [{"name": "record_id", "type": "string", "required": True},
                    {"name": "data", "type": "object", "required": True}]
        self.assertEqual(prepare_submission({"record_id": "user-field", "data": {}}, reserved)["data"],
                         {"record_id": "user-field", "data": {}})

    def test_fixed_fields_and_strict_types(self):
        fields = validate_fields(FIELDS)
        validate_data({"value": 7}, fields)
        validate_data({"value": 7, "label": "Entry"}, fields)
        for value in ({}, {"value": True}, {"value": 1.5}, {"value": 7, "extra": 1}, {"value": 7, "label": None}):
            with self.assertRaises(ValueError):
                validate_data(value, fields)
        for fields in ([], [FIELDS[0], FIELDS[0]], [{"name": "bad field", "type": "integer"}],
                       [{"name": "x", "type": "float"}], [{"name": "x", "type": "string", "required": 1}]):
            with self.assertRaises(ValueError):
                validate_fields(fields)

    def test_genesis_binds_ledger_and_structure(self):
        ledger_id = str(uuid.uuid4())
        genesis = ledger_genesis(ledger_id, FIELDS)
        self.assertEqual(genesis, ledger_genesis(ledger_id, copy.deepcopy(FIELDS)))
        self.assertTrue(verify_ledger([genesis], {"id": ledger_id, "fields": FIELDS})["valid"])
        self.assertFalse(verify_ledger([genesis], {"id": str(uuid.uuid4()), "fields": FIELDS})["valid"])
        self.assertFalse(verify_ledger([genesis], {"id": ledger_id, "fields": FIELDS[:1]})["valid"])

    def test_table_names_only_accept_canonical_uuid(self):
        identifier = str(uuid.uuid4())
        blocks, secret_table = table_names(identifier)
        self.assertTrue(blocks.endswith("_blocks") and secret_table.endswith("_secrets"))
        self.assertNotIn("-", blocks)
        for invalid in ("ledger; DROP TABLE users", identifier.upper(), identifier.replace("-", ""), None):
            with self.assertRaises(StoreNotFound):
                table_names(invalid)


class SnapshotComparisonTests(unittest.TestCase):
    def setUp(self):
        ledger_id = str(uuid.uuid4())
        metadata = {"id": ledger_id, "fields": copy.deepcopy(FIELDS)}
        genesis = ledger_genesis(ledger_id, FIELDS)
        blocks = [genesis, create_new_block(genesis, {"record_id": "one", "data": {"value": 4}})]

        class MemoryView:
            """Only the read/key boundary is replaced; production encryption stays real."""
            def __init__(self):
                self.key = Fernet.generate_key()
                self.blocks = blocks

            def get_snapshot_key(self, selected_id):
                return self.key

            def read(self, selected_id):
                return {"ledger": metadata, "blocks": copy.deepcopy(self.blocks),
                        "verification": verify_ledger(self.blocks, metadata)}

        self.store = MemoryView()
        self.service = SnapshotService(self.store, ledger_id)

    def test_valid_prefix_and_changed_data_have_distinct_statuses(self):
        token, _ = self.service.export()
        self.store.blocks.append(create_new_block(self.store.blocks[-1], {"record_id": "two", "data": {"value": 8}}))
        report = self.service.validate(token)
        self.assertTrue(report["valid"])
        self.assertEqual([value["status"] for value in report["block_statuses"]], ["verified", "verified", "not_in_snapshot"])
        self.store.blocks[1]["data"]["value"] = 99
        report = self.service.validate(token)
        self.assertFalse(report["valid"])
        self.assertEqual(report["block_statuses"][1]["status"], "changed")
        # Malformed current data should still produce a useful comparison, not crash.
        self.store.blocks[1]["data"]["value"] = 1.5
        self.assertFalse(self.service.validate(token)["valid"])

    def test_encrypted_file_and_ledger_identity_are_authenticated(self):
        token, _ = self.service.export()
        self.store.key = Fernet.generate_key()
        with self.assertRaisesRegex(ValueError, "could not be authenticated"):
            self.service.validate(token)
        token, _ = self.service.export()
        other_ledger = SnapshotService(self.store, str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, "another ledger"):
            other_ledger.validate(token)

    def test_missing_middle_block_does_not_rename_a_later_block(self):
        self.store.blocks.append(create_new_block(self.store.blocks[-1], {"record_id": "two", "data": {"value": 8}}))
        token, _ = self.service.export()
        del self.store.blocks[1]
        report = self.service.validate(token)
        self.assertFalse(report["valid"])
        statuses = {entry["index"]: entry for entry in report["block_statuses"]}
        self.assertEqual(statuses[0]["status"], "verified")
        self.assertIn("missing", statuses[1]["message"])
        self.assertEqual(statuses[2]["status"], "changed")
        self.assertNotIn("missing", statuses[2]["message"])
        self.assertEqual(report["new_blocks"], 0)

    def test_duplicate_current_index_never_receives_a_verified_status(self):
        token, _ = self.service.export()
        self.store.blocks[1]["index"] = 0
        report = self.service.validate(token)
        self.assertFalse(report["valid"])
        statuses = {entry["index"]: entry for entry in report["block_statuses"]}
        self.assertEqual(statuses[0]["status"], "changed")
        self.assertIn("claims this block index", statuses[0]["message"])
        self.assertIn("missing", statuses[1]["message"])
        self.assertFalse(any(entry["status"] == "verified" for entry in report["block_statuses"]))

    def test_malformed_current_indices_are_not_used_as_verified_identities(self):
        token, _ = self.service.export()
        original = copy.deepcopy(self.store.blocks)
        for malformed in (None, True, -1, 1.5, "1", [], {}, 9007199254740992):
            with self.subTest(index=malformed):
                self.store.blocks = copy.deepcopy(original)
                self.store.blocks[1]["index"] = malformed
                report = self.service.validate(token)
                self.assertFalse(report["valid"])
                statuses = {entry["index"]: entry for entry in report["block_statuses"]}
                self.assertEqual(statuses[0]["status"], "verified")
                self.assertEqual(statuses[1]["status"], "changed")
                self.assertIn("missing", statuses[1]["message"])
                self.assertTrue(any(error["index"] is None for error in report["errors"]))


@unittest.skipUnless(os.environ.get("LEDGER_TEST_DATABASE_URL"), "Set LEDGER_TEST_DATABASE_URL to run PostgreSQL tests")
class PostgreSQLStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.environ["LEDGER_TEST_DATABASE_URL"])
        self.owner = "test-" + str(uuid.uuid4())
        self.created = []

    def tearDown(self):
        for ledger_id in self.created:
            try:
                self.store.delete_ledger(ledger_id)
            except StoreNotFound:
                pass

    def create(self, name="Test ledger"):
        result = self.store.create_ledger(self.owner, name, "Temporary integration test", FIELDS)
        self.created.append(result["ledger"]["id"])
        return result

    def append(self, created, record_id="record-1", value=7):
        return self.store.append(created["ledger"]["id"], created["write_token"],
                                 {"value": value}, record_id=record_id)

    def test_isolated_tables_tokens_and_restart(self):
        first, second = self.create(), self.create()
        first_id, second_id = first["ledger"]["id"], second["ledger"]["id"]
        self.assertNotEqual(self.store.get_snapshot_key(first_id), self.store.get_snapshot_key(second_id))
        self.assertNotEqual(table_names(first_id), table_names(second_id))
        block, duplicate = self.append(first)
        self.assertFalse(duplicate)
        self.assertEqual(self.store.get_ledger(second_id)["block_count"], 1)
        restarted = Store(self.store.database_url)
        self.assertEqual(restarted.read(first_id)["blocks"][-1], block)
        repeated, duplicate = self.append(first)
        self.assertTrue(duplicate)
        self.assertEqual(repeated, block)
        with self.assertRaises(LedgerConflict):
            self.append(first, value=8)
        with self.assertRaises(StoreForbidden):
            self.store.append(second_id, first["write_token"], {"value": 7}, record_id="other")
        for ledger in self.store.list_ledgers(owner_id=self.owner):
            self.assertNotIn("token_hash", ledger)
            self.assertNotIn("write_token", ledger)
            self.assertNotIn("snapshot_key", ledger)

    def test_owner_credentials_rotation_states_and_deletion(self):
        created = self.create()
        ledger_id = created["ledger"]["id"]
        self.assertEqual(self.store.credentials(ledger_id, self.owner)["write_path"], created["write_path"])
        with self.assertRaises(StoreForbidden):
            self.store.credentials(ledger_id, "another-user", is_admin=True)
        rotated = self.store.rotate_token(ledger_id, self.owner)
        with self.assertRaises(StoreForbidden):
            self.append(created)
        created["write_token"] = rotated["write_token"]
        self.store.update_ledger(ledger_id, state="read_only", name="Updated label")
        with self.assertRaises(LedgerConflict):
            self.append(created)
        for forbidden in ({"fields": []}, {"state": "deleted"}, {"state": {}}):
            with self.assertRaises(ValueError):
                self.store.update_ledger(ledger_id, forbidden)
        self.store.update_ledger(ledger_id, state="active")
        self.append(created)
        self.store.delete_ledger(ledger_id)
        with self.assertRaises(StoreNotFound):
            self.store.read(ledger_id)
        with self.store.connect() as connection:
            for table in table_names(ledger_id):
                self.assertIsNone(connection.execute("SELECT to_regclass(%s) AS name", (table,)).fetchone()["name"])

    def test_schema_rejects_bad_records_and_concurrent_append(self):
        created = self.create()
        ledger_id = created["ledger"]["id"]
        with self.assertRaises(ValueError):
            self.store.append(ledger_id, created["write_token"], {"value": True}, record_id="bad")
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda value: self.append(created, "record-" + str(value), value), range(8)))
        view = self.store.read(ledger_id)
        self.assertTrue(view["verification"]["valid"])
        self.assertEqual([block["index"] for block in view["blocks"]], list(range(9)))

    def test_snapshot_old_prefix_colours_and_cross_ledger_rejection(self):
        created, other = self.create(), self.create()
        ledger_id = created["ledger"]["id"]
        service = SnapshotService(self.store, ledger_id)
        self.append(created)
        token, _ = service.export()
        self.append(created, "record-2", 8)
        report = service.validate(token)
        self.assertTrue(report["valid"])
        self.assertEqual([entry["status"] for entry in report["block_statuses"]], ["verified", "verified", "not_in_snapshot"])
        self.assertEqual(report["new_blocks"], 1)
        self.assertEqual(report["snapshot_blocks"][1]["data"], {"value": 7})
        block_token, _ = service.export(1)
        report = service.validate(block_token)
        self.assertEqual([entry["status"] for entry in report["block_statuses"]], ["not_in_snapshot", "verified", "not_in_snapshot"])
        with self.assertRaises(ValueError):
            SnapshotService(self.store, other["ledger"]["id"]).validate(token)
        changed = bytearray(token)
        changed[len(changed) // 2] = ord("A") if changed[len(changed) // 2] != ord("A") else ord("B")
        with self.assertRaises(ValueError):
            service.validate(bytes(changed))

    def test_snapshot_identifies_changed_block_and_missing_history(self):
        created = self.create()
        ledger_id = created["ledger"]["id"]
        service = SnapshotService(self.store, ledger_id)
        self.append(created)
        self.append(created, "record-2", 8)
        token, _ = service.export()
        blocks = self.store.read(ledger_id)["blocks"]
        blocks[1]["data"]["value"] = 999
        table = sql.Identifier(table_names(ledger_id)[0])
        with self.store.connect() as connection:
            connection.execute(sql.SQL("UPDATE {} SET record = %s WHERE position = 1").format(table), (Jsonb(blocks[1]),))
        report = service.validate(token)
        self.assertFalse(report["valid"])
        self.assertEqual(report["block_statuses"][0]["status"], "verified")
        self.assertEqual(report["block_statuses"][1]["status"], "changed")
        with self.assertRaises(LedgerConflict):
            self.append(created, "record-3")
        with self.store.connect() as connection:
            connection.execute(sql.SQL("DELETE FROM {} WHERE position = 2").format(table))
        report = service.validate(token)
        self.assertTrue(any(entry["index"] == 2 and entry["status"] == "changed" for entry in report["block_statuses"]))

    def test_rebuilt_but_consistent_history_still_differs_from_receipt(self):
        created = self.create()
        ledger_id = created["ledger"]["id"]
        service = SnapshotService(self.store, ledger_id)
        self.append(created)
        token, _ = service.export()
        blocks = self.store.read(ledger_id)["blocks"]
        blocks[1]["data"]["value"] = 10
        blocks[1]["hash"] = calculate_hash(blocks[1])
        with self.store.connect() as connection:
            connection.execute(sql.SQL("UPDATE {} SET record = %s WHERE position = 1")
                               .format(sql.Identifier(table_names(ledger_id)[0])), (Jsonb(blocks[1]),))
        # The registry head catches a rewritten suffix even when its links are consistent.
        self.assertFalse(self.store.read(ledger_id)["verification"]["valid"])
        # A database administrator can also replace registry metadata. The external
        # encrypted receipt must still detect this complete, internally consistent rewrite.
        with self.store.connect() as connection:
            connection.execute('UPDATE ledger_registry SET head_hash=%s WHERE id=%s', (blocks[1]['hash'],ledger_id))
        self.assertTrue(self.store.read(ledger_id)["verification"]["valid"])
        self.assertFalse(service.validate(token)["valid"])

    def test_middle_database_row_deletion_preserves_later_block_identity(self):
        created = self.create()
        ledger_id = created["ledger"]["id"]
        service = SnapshotService(self.store, ledger_id)
        self.append(created)
        self.append(created, "record-2", 8)
        token, _ = service.export()
        with self.store.connect() as connection:
            connection.execute(sql.SQL("DELETE FROM {} WHERE position = 1").format(sql.Identifier(table_names(ledger_id)[0])))
        report = service.validate(token)
        self.assertFalse(report["valid"])
        statuses = {entry["index"]: entry for entry in report["block_statuses"]}
        self.assertIn("missing", statuses[1]["message"])
        self.assertNotIn("missing", statuses[2]["message"])


if __name__ == "__main__":
    unittest.main()
