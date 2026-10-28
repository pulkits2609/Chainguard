"""Expiring shipment capabilities and redacted, explicitly server-checked evidence."""

import datetime
import hashlib
import hmac
import re
import secrets
import time
import uuid

from checkpoints import canonical_uuid, page_values, trusted_key, validate_checkpoint, verify_checkpoint
from store import StoreForbidden, StoreNotFound, verify_ledger


SHIPMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
GRANT_FIELDS = ("grant_id", "ledger_id", "shipment_id", "created_at_ms", "expires_at", "revoked")
LIMITATIONS = [
    "Shipment records are checked by the ChainGuard server; redacted contents are not independently verified.",
    "A valid checkpoint signature authenticates that checkpoint only when its public key is separately trusted.",
    "Full-record history verification requires an authorized owner's complete history export and an independently trusted key.",
    "Cryptographic integrity does not prove that submitted events or a physical delivery are truthful.",
]


class ShipmentRateLimit(StoreForbidden):
    status = 429

    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__("Too many customer requests; wait before retrying")


def shipment_identifier(value):
    if not isinstance(value, str) or not SHIPMENT_ID.fullmatch(value):
        raise ValueError("Shipment ID must contain 1–128 letters, digits, periods, underscores, colons or hyphens")
    return value


def grant_expiration(value, now, default_lifetime, maximum_lifetime):
    if value is None:
        return now + default_lifetime
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Timezone required")
            value = int(parsed.timestamp())
        except (ValueError, OverflowError):
            raise ValueError("Grant expiration must be a timestamp with a timezone or Unix seconds") from None
    if type(value) is not int or not now < value <= now + maximum_lifetime:
        raise ValueError("Grant expiration must be in the future and within the configured maximum lifetime")
    return value


def public_grant(row):
    return {field: row[field] for field in GRANT_FIELDS}


def project_event(block):
    data = block["data"]
    event = {"shipment_id": data["shipment_id"], "block_index": block["index"], "recorded_at_ms": block["timestamp_ms"]}
    for name in ("event_type", "event_time", "location", "source_system"):
        if isinstance(data.get(name), str):
            event[name] = data[name][:1000]
    if "event_type" not in event and isinstance(data.get("custody_event"), str):
        event["event_type"] = data["custody_event"][:1000]
    return event


class ShipmentAccessService:
    def __init__(self, store, checkpoints, default_lifetime_seconds=86400, maximum_lifetime_seconds=30 * 86400, audit=None):
        if type(default_lifetime_seconds) is not int or type(maximum_lifetime_seconds) is not int or not 60 <= default_lifetime_seconds <= maximum_lifetime_seconds <= 365 * 86400:
            raise ValueError("Invalid customer grant lifetime configuration")
        self.store, self.checkpoints = store, checkpoints
        self.audit = audit
        self.default_lifetime, self.maximum_lifetime = default_lifetime_seconds, maximum_lifetime_seconds
        with store.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS shipment_access_grants (
                grant_id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL REFERENCES ledger_registry(id) ON DELETE CASCADE,
                shipment_id TEXT NOT NULL, secret_hash TEXT NOT NULL, created_at_ms BIGINT NOT NULL,
                expires_at BIGINT NOT NULL, revoked BOOLEAN NOT NULL DEFAULT FALSE)""")
            connection.execute("CREATE INDEX IF NOT EXISTS shipment_grants_ledger ON shipment_access_grants(ledger_id, created_at_ms DESC)")
            connection.execute("""CREATE TABLE IF NOT EXISTS customer_rate_windows (
                grant_id TEXT PRIMARY KEY REFERENCES shipment_access_grants(grant_id) ON DELETE CASCADE,
                minute BIGINT NOT NULL, request_count INTEGER NOT NULL)""")

    @staticmethod
    def _manage(metadata, user):
        if not isinstance(user, dict) or not (user.get("is_admin") is True or user.get("id") == metadata["owner_id"]):
            raise StoreForbidden("Only the ledger owner or an administrator can manage shipment access")

    def create(self, ledger_id, shipment_id, expires_at=None, user=None):
        shipment_identifier(shipment_id)
        now = int(time.time())
        expires_at = grant_expiration(expires_at, now, self.default_lifetime, self.maximum_lifetime)
        grant_id, secret = str(uuid.uuid4()), secrets.token_urlsafe(32)
        grant = {"grant_id": grant_id, "ledger_id": ledger_id, "shipment_id": shipment_id,
                 "created_at_ms": time.time_ns() // 1000000, "expires_at": expires_at, "revoked": False}
        with self.store.connect() as connection:
            metadata = self.store._metadata(connection, ledger_id)
            self._manage(metadata, user)
            if not any(field["name"] == "shipment_id" and field["type"] == "string" for field in metadata["fields"]):
                raise ValueError("Shipment access requires a ledger with a shipment_id string field")
            connection.execute("INSERT INTO shipment_access_grants VALUES (%s,%s,%s,%s,%s,%s,FALSE)",
                               (grant_id, ledger_id, shipment_id, hashlib.sha256(secret.encode("ascii")).hexdigest(), grant["created_at_ms"], expires_at))
            if self.audit is not None:
                self.audit.record("customer_access", "create", "accepted", "grant_created", ledger_id=ledger_id, connection=connection)
        return {"grant": grant, "access_token": grant_id + "." + secret}

    def list(self, ledger_id, user=None, limit=50, offset=0):
        limit, offset = page_values(limit, offset)
        with self.store.connect() as connection:
            self._manage(self.store._metadata(connection, ledger_id), user)
            total = connection.execute("SELECT COUNT(*) AS total FROM shipment_access_grants WHERE ledger_id=%s", (ledger_id,)).fetchone()["total"]
            rows = connection.execute("SELECT * FROM shipment_access_grants WHERE ledger_id=%s ORDER BY created_at_ms DESC,grant_id LIMIT %s OFFSET %s", (ledger_id, limit, offset)).fetchall()
        return {"grants": [public_grant(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def revoke(self, ledger_id, grant_id, user=None):
        canonical_uuid(grant_id, "Grant ID")
        with self.store.connect() as connection:
            self._manage(self.store._metadata(connection, ledger_id), user)
            row = connection.execute("UPDATE shipment_access_grants SET revoked=TRUE WHERE ledger_id=%s AND grant_id=%s RETURNING grant_id", (ledger_id, grant_id)).fetchone()
            if row is None:
                raise StoreNotFound("Shipment access grant not found")
            if self.audit is not None:
                self.audit.record("customer_access", "revoke", "accepted", "grant_revoked", ledger_id=ledger_id, connection=connection)
        return {"revoked": True, "grant_id": grant_id}

    def _authorize(self, connection, shipment_id, token):
        shipment_identifier(shipment_id)
        if not isinstance(token, str) or not 60 <= len(token) <= 128 or token.count(".") != 1 or not token.isascii():
            raise StoreForbidden("Invalid or expired shipment access")
        grant_id, secret = token.split(".", 1)
        try:
            canonical_uuid(grant_id, "Grant ID")
        except ValueError:
            raise StoreForbidden("Invalid or expired shipment access") from None
        row = connection.execute("SELECT * FROM shipment_access_grants WHERE grant_id=%s FOR SHARE", (grant_id,)).fetchone()
        expected = row["secret_hash"] if row else "0" * 64
        valid_secret = hmac.compare_digest(hashlib.sha256(secret.encode("ascii")).hexdigest(), expected)
        if not row or not valid_secret or row["revoked"] or row["expires_at"] <= int(time.time()) or row["shipment_id"] != shipment_id:
            raise StoreForbidden("Invalid or expired shipment access")
        now = int(time.time())
        count = connection.execute("""INSERT INTO customer_rate_windows VALUES (%s,%s,1)
            ON CONFLICT (grant_id) DO UPDATE SET minute=EXCLUDED.minute,
            request_count=CASE WHEN customer_rate_windows.minute=EXCLUDED.minute THEN customer_rate_windows.request_count+1 ELSE 1 END
            RETURNING request_count""", (grant_id, now // 60)).fetchone()["request_count"]
        if count > 60:
            raise ShipmentRateLimit(60 - now % 60)
        return row

    def _view(self, connection, grant):
        metadata = self.store._metadata(connection, grant["ledger_id"])
        blocks = self.store._blocks(connection, grant["ledger_id"])
        internal_valid = verify_ledger(blocks, metadata)["valid"]
        selected = [block for block in blocks if isinstance(block, dict) and isinstance(block.get("data"), dict)
                    and block["data"].get("shipment_id") == grant["shipment_id"]]
        # Only checkpoint metadata is read here; no private block content leaves this service.
        rows = connection.execute("SELECT checkpoint FROM signed_checkpoints WHERE ledger_id=%s ORDER BY created_at_ms DESC,checkpoint_id DESC", (grant["ledger_id"],)).fetchall()
        checkpoint = rows[0]["checkpoint"] if rows else None
        checkpoint_valid = True
        signature_valid = False
        key = None
        for row in rows:
            saved = row["checkpoint"]
            key_row = connection.execute("SELECT public_key FROM verification_keys WHERE key_id=%s", (saved.get("key_id") if isinstance(saved, dict) else None,)).fetchone()
            verification = verify_checkpoint(saved, key_row["public_key"] if key_row else None,
                                             grant["ledger_id"], {"ledger": metadata, "blocks": blocks})
            checkpoint_valid &= verification["valid"]
            if saved is checkpoint:
                key = key_row["public_key"] if key_row else None
                signature_valid = verification["signature_valid"]
        valid = internal_valid and checkpoint_valid
        covered = bool(checkpoint and checkpoint_valid and all(type(block.get("index")) is int and block["index"] <= checkpoint["index"] for block in selected))
        verification = {"server_records_verified": valid, "checkpoint_signature_verified": signature_valid,
                        "independently_verified": False, "status": "server_verified" if valid else "verification_failed",
                        "checkpoint_status": "signature_verified" if signature_valid else "verification_failed" if checkpoint else "not_available",
                        "history_status": "not_independently_verified", "checkpoint_covers_all_shipment_records": covered,
                        "message": "Shipment records verified by the ChainGuard server. Redacted contents are not independently verified." if valid else "Verification failed; the server found a cryptographic or historical inconsistency."}
        # A tampered metadata row must never turn this redacted endpoint into an
        # arbitrary JSON disclosure channel.
        try:
            validate_checkpoint(checkpoint)
        except (ValueError, TypeError):
            checkpoint = None
        try:
            trusted_key(key)
        except (ValueError, TypeError):
            key = None
        return selected, checkpoint, key, verification

    def events(self, shipment_id, token, limit=50, offset=0):
        limit, offset = page_values(limit, offset)
        with self.store.connect() as connection:
            grant = self._authorize(connection, shipment_id, token)
            selected, checkpoint, _, verification = self._view(connection, grant)
            events = [project_event(block) for block in selected[offset:offset + limit]
                      if type(block.get("index")) is int and type(block.get("timestamp_ms")) is int]
        return {"shipment_id": shipment_id, "events": events, "total": len(selected), "limit": limit, "offset": offset,
                "last_recorded_at_ms": max((block.get("timestamp_ms", 0) for block in selected if type(block.get("timestamp_ms")) is int), default=None),
                "verification": verification, "checkpoint": checkpoint}

    def proof(self, shipment_id, token):
        with self.store.connect() as connection:
            grant = self._authorize(connection, shipment_id, token)
            _, checkpoint, key, verification = self._view(connection, grant)
        return {"shipment_id": shipment_id, "checkpoint": checkpoint, "verification_key": key,
                "verification": verification, "limitations": LIMITATIONS}
