"""PostgreSQL registry and separate block/key tables for each ledger."""

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

import psycopg
from cryptography.fernet import Fernet
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from field_schema import validate_data, validate_fields, reject_nul
from ledger_templates import template_fields, validate_template_event
from settings import database_options, require_chainguard_database
from ledger import (LedgerConflict, MAX_BODY_BYTES, calculate_hash, canonical_json,
                    create_genesis_block, create_new_block, validate_submission, verify_chain)


LEDGER_STATES = {"active", "read_only", "archived"}


def history_byte_budget():
    maximum = int(os.environ.get('LEDGER_MAX_HISTORY_BYTES', str(32 * 1024 * 1024)))
    if not 1024 <= maximum <= 64 * 1024 * 1024:
        raise ValueError('Interactive history budget must be 1 KiB–64 MiB')
    return maximum


class StoreNotFound(ValueError):
    pass


class StoreForbidden(ValueError):
    pass


def table_names(ledger_id):
    try:
        identifier = uuid.UUID(ledger_id)
    except (ValueError, TypeError, AttributeError):
        raise StoreNotFound("Ledger not found") from None
    if str(identifier) != ledger_id:
        raise StoreNotFound("Ledger not found")
    # Only a generated UUID enters a table name; labels never become SQL identifiers.
    prefix = "ledger_" + identifier.hex
    return prefix + "_blocks", prefix + "_secrets"


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def prepare_submission(value, fields, record_id=None):
    """Clients send schema fields directly; metadata is generated separately."""
    if record_id is not None:
        if not isinstance(record_id, str) or not 1 <= len(record_id) <= 128 or not record_id.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in record_id):
            raise ValueError("Idempotency-Key must contain 1–128 printable characters without spaces")
    reject_nul(value)
    validate_data(value, fields)
    submission = {"record_id": record_id if record_id is not None else "record-" + uuid.uuid4().hex,
                  "data": value}
    validate_submission(submission)
    return submission


def ledger_genesis(ledger_id, fields):
    block = create_genesis_block()
    block["data"]["ledger_id"] = ledger_id
    block["data"]["fields"] = fields
    block["hash"] = calculate_hash(block)
    return block


def verify_ledger(blocks, metadata):
    try:
        fields = validate_fields(metadata["fields"])
        report = verify_chain(blocks, genesis=ledger_genesis(metadata["id"], fields))
    except (ValueError, TypeError, KeyError) as error:
        return {"valid": False, "checked_blocks": len(blocks),
                "errors": [{"index": 0, "message": "Invalid fixed ledger schema: " + str(error)}], "head_hash": None}
    for position, block in enumerate(blocks[1:], 1):
        try:
            validate_data(block["data"], fields)
        except (ValueError, TypeError, KeyError) as error:
            report["errors"].append({"index": position, "message": "Data schema: " + str(error)})
    report["valid"] = not report["errors"]
    if metadata.get('head_index') is not None:
        head = blocks[-1] if blocks and isinstance(blocks[-1], dict) else {}
        if (metadata['head_index'] != len(blocks) - 1 or metadata.get('head_hash') != head.get('hash')):
            report['errors'].append({'index': None, 'message': 'Stored chain head does not match the available history'})
            report['valid'] = False
    return report


def validate_label(value, label, maximum, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ValueError(label + " must be text of at most " + str(maximum) + " characters")
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise ValueError(label + " contains unsupported control characters")
    return value.strip()


class Store:
    def __init__(self, database_url):
        require_chainguard_database(database_url)
        self.database_url = database_url
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS ledger_registry (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('active', 'read_only', 'archived')),
                fields JSONB NOT NULL,
                created_at BIGINT NOT NULL,
                token_hash TEXT NOT NULL
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS ledger_owner_index ON ledger_registry (owner_id)")
            connection.execute("ALTER TABLE ledger_registry ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private'))")
            connection.execute("ALTER TABLE ledger_registry ADD COLUMN IF NOT EXISTS template_id TEXT")
            connection.execute("ALTER TABLE ledger_registry ADD COLUMN IF NOT EXISTS managed_writes_required BOOLEAN NOT NULL DEFAULT FALSE")
            connection.execute("ALTER TABLE ledger_registry ADD COLUMN IF NOT EXISTS head_index BIGINT")
            connection.execute("ALTER TABLE ledger_registry ADD COLUMN IF NOT EXISTS head_hash TEXT")
            connection.execute("ALTER TABLE ledger_registry ADD COLUMN IF NOT EXISTS history_revision BIGINT NOT NULL DEFAULT 0")
            connection.execute("""CREATE OR REPLACE FUNCTION chainguard_history_changed() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    UPDATE ledger_registry SET history_revision=history_revision+1 WHERE id=TG_ARGV[0];
                    RETURN NULL;
                END $$""")
            # Initialize metadata only for verifiable legacy histories. Never rewrite blocks.
            for row in connection.execute('SELECT * FROM ledger_registry WHERE head_index IS NULL FOR UPDATE').fetchall():
                blocks = self._blocks(connection, row['id'])
                if verify_ledger(blocks, row)['valid']:
                    connection.execute('UPDATE ledger_registry SET head_index=%s,head_hash=%s WHERE id=%s',
                                       (blocks[-1]['index'], blocks[-1]['hash'], row['id']))
            for row in connection.execute('SELECT id FROM ledger_registry ORDER BY id').fetchall():
                self._install_history_index(connection, row['id'])

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=5, options=database_options())

    def _install_history_index(self, connection, ledger_id):
        blocks, _ = table_names(ledger_id)
        connection.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ((record->'data'->>'shipment_id'), position) WHERE position > 0")
                           .format(sql.Identifier(blocks + '_shipment'), sql.Identifier(blocks)))
        installed = connection.execute("SELECT 1 FROM pg_trigger WHERE tgrelid=%s::regclass AND tgname='cg_history_revision'", (blocks,)).fetchone()
        if not installed:
            connection.execute(sql.SQL("CREATE TRIGGER cg_history_revision AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {} FOR EACH STATEMENT EXECUTE FUNCTION chainguard_history_changed({})")
                               .format(sql.Identifier(blocks), sql.Literal(ledger_id)))

    def _metadata(self, connection, ledger_id, lock=False):
        table_names(ledger_id)
        query = "SELECT * FROM ledger_registry WHERE id = %s"
        query += " FOR UPDATE" if lock else " FOR SHARE"
        row = connection.execute(query, (ledger_id,)).fetchone()
        if row is None:
            raise StoreNotFound("Ledger not found")
        return row

    def _public(self, connection, row):
        blocks, _ = table_names(row["id"])
        count = connection.execute(sql.SQL("SELECT COUNT(*) AS count FROM {}").format(sql.Identifier(blocks))).fetchone()["count"]
        return {key: row[key] for key in ("id", "owner_id", "name", "description", "state", "fields", "created_at", "visibility", "template_id", "managed_writes_required")} | {"block_count": count}

    def create_ledger(self, owner_id, name, description, fields=None, template_id=None, visibility=None):
        owner_id = validate_label(owner_id, "Owner ID", 128)
        name = validate_label(name, "Ledger name", 80)
        description = validate_label(description, "Description", 1000, True)
        if template_id is not None:
            expected = template_fields(template_id)
            if fields is not None and validate_fields(fields) != expected:
                raise ValueError("Template fields cannot be changed; choose a custom ledger instead")
            fields = expected
        fields = validate_fields(fields)
        visibility = visibility if visibility is not None else ('private' if template_id else 'public')
        if visibility not in ('public', 'private'):
            raise ValueError("Visibility must be public or private")
        ledger_id = str(uuid.uuid4())
        blocks, secret_table = table_names(ledger_id)
        managed_writes_required = template_id is not None
        write_token = None if managed_writes_required else secrets.token_urlsafe(32)
        genesis = ledger_genesis(ledger_id, fields)
        if len(json.dumps(genesis, ensure_ascii=True).encode('utf-8')) > history_byte_budget():
            raise OverflowError('Genesis exceeds the configured interactive history budget')
        with self.connect() as connection:
            connection.execute("""INSERT INTO ledger_registry
                (id, owner_id, name, description, state, fields, created_at, token_hash, visibility, template_id, managed_writes_required)
                VALUES (%s, %s, %s, %s, 'active', %s, %s, %s, %s, %s, %s)""",
                (ledger_id, owner_id, name, description, Jsonb(fields), time.time_ns() // 1000000,
                 '' if managed_writes_required else token_hash(write_token), visibility, template_id, managed_writes_required))
            connection.execute(sql.SQL("""CREATE TABLE {} (
                position BIGINT PRIMARY KEY CHECK (position >= 0),
                record_id TEXT UNIQUE NOT NULL,
                record JSONB NOT NULL
            )""").format(sql.Identifier(blocks)))
            connection.execute(sql.SQL("""CREATE TABLE {} (
                singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
                snapshot_key TEXT NOT NULL
            )""").format(sql.Identifier(secret_table)))
            connection.execute(sql.SQL("INSERT INTO {} (position, record_id, record) VALUES (0, 'genesis', %s)")
                               .format(sql.Identifier(blocks)), (Jsonb(genesis),))
            connection.execute(sql.SQL("INSERT INTO {} (snapshot_key) VALUES (%s)").format(sql.Identifier(secret_table)),
                               (Fernet.generate_key().decode("ascii"),))
            connection.execute('UPDATE ledger_registry SET head_index=0,head_hash=%s WHERE id=%s', (genesis['hash'], ledger_id))
            self._install_history_index(connection, ledger_id)
            metadata = self._public(connection, self._metadata(connection, ledger_id))
        result = {"ledger": metadata, "write_path": "/api/ledgers/" + ledger_id + "/blocks"}
        if managed_writes_required:
            result['integration_hint'] = 'Create a managed integration to submit events under its configured policy.'
        else:
            result['write_token'] = write_token
        return result

    def list_ledgers(self, owner_id=None, admin=False, public_only=False):
        with self.connect() as connection:
            query = "SELECT * FROM ledger_registry"
            parameters = ()
            if public_only:
                query += " WHERE visibility = 'public'"
            elif owner_id is not None and not admin:
                query += " WHERE owner_id = %s"
                parameters = (owner_id,)
            # Share locks keep tables alive while counting, even during admin deletion.
            rows = connection.execute(query + " ORDER BY created_at, id FOR SHARE", parameters).fetchall()
            return [self._public(connection, row) for row in rows]

    def get_ledger(self, ledger_id):
        with self.connect() as connection:
            return self._public(connection, self._metadata(connection, ledger_id))

    def credentials(self, ledger_id, user_id, is_admin=False):
        metadata = self.get_ledger(ledger_id)
        if metadata["owner_id"] != user_id:
            raise StoreForbidden("Only the ledger creator can access its integration details")
        if metadata['managed_writes_required']:
            return {"ledger_id": ledger_id, "managed_writes_required": True,
                    "write_path": "/api/ledgers/" + ledger_id + "/blocks",
                    "integration_path": "/api/integrations",
                    "token_hint": "Create a managed integration. This ledger has no legacy write token; its writes require integration policy enforcement."}
        return {"ledger_id": ledger_id, "write_path": "/api/ledgers/" + ledger_id + "/blocks",
                "token_hint": "The secret is shown once at creation or rotation. Rotate it if lost."}

    def rotate_token(self, ledger_id, user_id):
        write_token = secrets.token_urlsafe(32)
        with self.connect() as connection:
            row = self._metadata(connection, ledger_id, lock=True)
            if row["owner_id"] != user_id:
                raise StoreForbidden("Only the ledger creator can rotate its write token")
            if row['managed_writes_required']:
                raise StoreForbidden('This ledger requires a managed integration; legacy write-token rotation is disabled')
            connection.execute("UPDATE ledger_registry SET token_hash = %s WHERE id = %s",
                               (token_hash(write_token), ledger_id))
        return {"ledger_id": ledger_id, "write_token": write_token, "write_path": "/api/ledgers/" + ledger_id + "/blocks"}

    def update_ledger(self, ledger_id, changes=None, **updates):
        changes = dict(changes or {}) | updates
        if not changes or set(changes) - {"name", "description", "state", "visibility"}:
            raise ValueError("Only ledger name, description, state and visibility can be changed")
        if "visibility" in changes and changes['visibility'] not in ('public', 'private'):
            raise ValueError("Visibility must be public or private")
        if "name" in changes:
            changes["name"] = validate_label(changes["name"], "Ledger name", 80)
        if "description" in changes:
            changes["description"] = validate_label(changes["description"], "Description", 1000, True)
        if "state" in changes and (not isinstance(changes["state"], str) or changes["state"] not in LEDGER_STATES):
            raise ValueError("State must be active, read_only or archived")
        with self.connect() as connection:
            self._metadata(connection, ledger_id, lock=True)
            assignments = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(key)) for key in changes)
            connection.execute(sql.SQL("UPDATE ledger_registry SET {} WHERE id = %s").format(assignments),
                               (*changes.values(), ledger_id))
            return self._public(connection, self._metadata(connection, ledger_id))

    def delete_ledger(self, ledger_id):
        with self.connect() as connection:
            self._metadata(connection, ledger_id, lock=True)
            for name in table_names(ledger_id):
                connection.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(name)))
            connection.execute("DELETE FROM ledger_registry WHERE id = %s", (ledger_id,))

    def _blocks(self, connection, ledger_id, with_budget=False):
        blocks, _ = table_names(ledger_id)
        maximum_bytes = history_byte_budget()
        result, size = [], 0
        # A server-side cursor avoids buffering an unbounded complete result before checking its budget.
        with connection.cursor(name='cg_history_' + uuid.uuid4().hex) as cursor:
            cursor.execute(sql.SQL('SELECT record FROM {} ORDER BY position LIMIT 100001').format(sql.Identifier(blocks)))
            for row in cursor:
                size += len(json.dumps(row['record'], ensure_ascii=True).encode('utf-8'))
                if size > maximum_bytes or len(result) >= 100000:
                    raise OverflowError('History exceeds the interactive verification budget; partition operational ledgers')
                result.append(row['record'])
        return (result, size, maximum_bytes) if with_budget else result

    def shipment_records(self, connection, ledger_id, shipment_id, limit, offset):
        blocks, _ = table_names(ledger_id)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('Shipment pagination is out of range')
        table = sql.Identifier(blocks)
        summary = connection.execute(sql.SQL("""SELECT COUNT(*) AS total,
            MAX(CASE WHEN jsonb_typeof(record->'timestamp_ms')='number' AND record->>'timestamp_ms' ~ '^[0-9]{{1,16}}$'
                THEN (record->>'timestamp_ms')::bigint END) AS last_recorded_at_ms,
            MAX(position) AS last_index FROM {} WHERE position>0 AND record->'data'->>'shipment_id'=%s""").format(table), (shipment_id,)).fetchone()
        rows = connection.execute(sql.SQL("SELECT record FROM {} WHERE position>0 AND record->'data'->>'shipment_id'=%s ORDER BY position LIMIT %s OFFSET %s").format(table), (shipment_id, limit, offset)).fetchall()
        return [row['record'] for row in rows], summary

    def read(self, ledger_id):
        with self.connect() as connection:
            row = self._metadata(connection, ledger_id)
            blocks = self._blocks(connection, ledger_id)
            metadata = self._public(connection, row)
            return {"ledger": metadata, "blocks": blocks, "verification": verify_ledger(blocks, row),
                    "updated_at_ms": time.time_ns() // 1000000}

    def get_snapshot_key(self, ledger_id):
        with self.connect() as connection:
            self._metadata(connection, ledger_id)
            _, secret_table = table_names(ledger_id)
            row = connection.execute(sql.SQL("SELECT snapshot_key FROM {} WHERE singleton = TRUE")
                                     .format(sql.Identifier(secret_table))).fetchone()
            if row is None:
                raise LedgerConflict("The ledger snapshot key is missing; restore it from a backup")
            return row["snapshot_key"].encode("ascii")

    def append(self, ledger_id, token, submission, record_id=None):
        if len(canonical_json(submission)) > MAX_BODY_BYTES:
            raise ValueError("Submission is too large")
        with self.connect() as connection:
            row = self._metadata(connection, ledger_id, lock=True)
            if row['managed_writes_required']:
                raise StoreForbidden('This ledger requires a managed integration credential')
            if not isinstance(token, str) or not 20 <= len(token) <= 256 or not hmac.compare_digest(token_hash(token), row["token_hash"]):
                raise StoreForbidden("Invalid ledger write token")
            return self.append_authorized(ledger_id, submission, record_id, connection)

    def append_authorized(self, ledger_id, submission, record_id=None, connection=None):
        """Internal trusted service boundary; HTTP callers must authenticate first."""
        if connection is None:
            with self.connect() as transaction:
                return self.append_authorized(ledger_id, submission, record_id, transaction)
        if len(canonical_json(submission)) > MAX_BODY_BYTES:
            raise ValueError("Submission is too large")
        row = self._metadata(connection, ledger_id, lock=True)
        return self._append_event(connection, row, ledger_id, submission, record_id)

    def _append_event(self, connection, row, ledger_id, submission, record_id):
        if row["state"] != "active":
            raise LedgerConflict("This ledger does not accept new blocks in its current state")
        submission = prepare_submission(submission, row["fields"], record_id)
        validate_template_event(submission['data'], row.get('template_id'))
        blocks, history_bytes, maximum_bytes = self._blocks(connection, ledger_id, with_budget=True)
        if not verify_ledger(blocks, row)["valid"]:
            raise LedgerConflict("Ledger verification failed; no data was changed")
        for block in blocks:
            if block["record_id"] == submission["record_id"]:
                if canonical_json(block["data"]) != canonical_json(submission["data"]):
                    raise LedgerConflict("This record_id already has different data")
                return block, True
        block = create_new_block(blocks[-1], submission)
        if len(blocks) >= 100000 or history_bytes + len(json.dumps(block, ensure_ascii=True).encode('utf-8')) > maximum_bytes:
            raise OverflowError('Append would exceed the interactive history budget; use a new operational ledger')
        block_table, _ = table_names(ledger_id)
        connection.execute(sql.SQL("INSERT INTO {} (position, record_id, record) VALUES (%s, %s, %s)")
                           .format(sql.Identifier(block_table)), (block["index"], block["record_id"], Jsonb(block)))
        connection.execute('UPDATE ledger_registry SET head_index=%s,head_hash=%s WHERE id=%s',
                           (block['index'], block['hash'], ledger_id))
        return block, False
