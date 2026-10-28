"""Versioned Ed25519 checkpoints and verification against explicitly trusted keys.

Public keys obtained from this server are distribution material, not automatically
trusted evidence. No private key or historical block is stored in this module's tables.
"""

import base64
import binascii
import hashlib
import os
from pathlib import Path
import stat
import time
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from psycopg.types.json import Jsonb

from ledger import LedgerConflict, canonical_json, is_hash
from store import StoreNotFound, verify_ledger


CHECKPOINT_FIELDS = {"format_version", "checkpoint_id", "ledger_id", "index", "hash", "created_at_ms", "key_id", "signature"}
PUBLIC_KEY_FIELDS = {"format_version", "algorithm", "key_id", "public_key"}
MAX_SAFE_INTEGER = 9007199254740991


def canonical_uuid(value, label):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError(label + " must be a canonical UUID")
    return value


def page_values(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= MAX_SAFE_INTEGER:
        raise ValueError("Pagination needs a limit of 1–100 and a nonnegative offset")
    return limit, offset


def key_document(key):
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"format_version": 1, "algorithm": "Ed25519", "key_id": hashlib.sha256(raw).hexdigest(),
            "public_key": base64.b64encode(raw).decode("ascii")}


def trusted_key(value):
    if isinstance(value, Ed25519PublicKey):
        return value
    if isinstance(value, dict):
        if set(value) != PUBLIC_KEY_FIELDS or type(value["format_version"]) is not int or value["format_version"] != 1 or value["algorithm"] != "Ed25519":
            raise ValueError("Unsupported verification-key document")
        try:
            raw = base64.b64decode(value["public_key"], validate=True)
            key = Ed25519PublicKey.from_public_bytes(raw)
        except (ValueError, TypeError, binascii.Error):
            raise ValueError("Invalid Ed25519 public key") from None
        if key_document(key) != value:
            raise ValueError("Public key identifier or encoding does not match its key")
        return key
    if isinstance(value, bytes):
        try:
            key = serialization.load_pem_public_key(value)
        except ValueError:
            raise ValueError("Expected an Ed25519 PEM public key or exported JSON key") from None
        if isinstance(key, Ed25519PublicKey):
            return key
    raise ValueError("Expected an explicitly trusted Ed25519 public key")


def validate_checkpoint(checkpoint):
    if not isinstance(checkpoint, dict) or set(checkpoint) != CHECKPOINT_FIELDS:
        raise ValueError("Invalid signed-checkpoint fields")
    if type(checkpoint["format_version"]) is not int or checkpoint["format_version"] != 1:
        raise ValueError("Unsupported signed-checkpoint format")
    canonical_uuid(checkpoint["checkpoint_id"], "Checkpoint ID")
    canonical_uuid(checkpoint["ledger_id"], "Ledger ID")
    for field in ("index", "created_at_ms"):
        if type(checkpoint[field]) is not int or not 0 <= checkpoint[field] <= MAX_SAFE_INTEGER:
            raise ValueError("Checkpoint " + field + " must be a nonnegative safe integer")
    if not is_hash(checkpoint["hash"]) or not is_hash(checkpoint["key_id"]):
        raise ValueError("Invalid checkpoint hash or key identifier")
    try:
        signature = base64.b64decode(checkpoint["signature"], validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != checkpoint["signature"]:
            raise ValueError("Invalid signature")
    except (ValueError, TypeError, binascii.Error):
        raise ValueError("Invalid Ed25519 signature encoding") from None
    return signature


def sign_checkpoint(key, ledger_id, index, head_hash):
    checkpoint = {"format_version": 1, "checkpoint_id": str(uuid.uuid4()), "ledger_id": ledger_id,
                  "index": index, "hash": head_hash, "created_at_ms": time.time_ns() // 1000000,
                  "key_id": key_document(key.public_key())["key_id"]}
    checkpoint["signature"] = base64.b64encode(key.sign(canonical_json(checkpoint))).decode("ascii")
    validate_checkpoint(checkpoint)
    return checkpoint


def verify_checkpoint(checkpoint, trusted_public_key, expected_ledger_id, history=None):
    """Verify a signature, and optionally full records through its signed prefix.

    history is an exported GET-ledger response containing ``ledger`` and ``blocks``.
    A signature-only result says nothing about redacted shipment content. A valid
    older checkpoint anchors only its prefix; a consistent later suffix remains
    unanchored until covered by a separately trusted checkpoint.
    """
    result = {"valid": False, "signature_valid": False, "history_verified": False,
              "prefix_verified": False, "anchored_prefix_index": None, "anchored_prefix_count": 0,
              "unanchored_suffix_count": None, "verification_scope": "checkpoint_signature", "errors": []}
    try:
        canonical_uuid(expected_ledger_id, "Expected ledger ID")
        signature = validate_checkpoint(checkpoint)
        result.update(ledger_id=checkpoint["ledger_id"], index=checkpoint["index"])
        if checkpoint["ledger_id"] != expected_ledger_id:
            raise ValueError("Checkpoint belongs to a different ledger")
        key = trusted_key(trusted_public_key)
        if key_document(key)["key_id"] != checkpoint["key_id"]:
            raise ValueError("Checkpoint key does not match the independently supplied public key")
        signed = {name: value for name, value in checkpoint.items() if name != "signature"}
        key.verify(signature, canonical_json(signed))
        result["signature_valid"] = True
        if history is not None:
            if not isinstance(history, dict) or not isinstance(history.get("ledger"), dict) or not isinstance(history.get("blocks"), list):
                raise ValueError("Full-history export must contain ledger metadata and blocks")
            metadata, blocks = history["ledger"], history["blocks"]
            if metadata.get("id") != expected_ledger_id:
                raise ValueError("Full history belongs to a different ledger")
            index = checkpoint["index"]
            if index >= len(blocks) or not isinstance(blocks[index], dict) or blocks[index].get("index") != index or blocks[index].get("hash") != checkpoint["hash"]:
                raise ValueError("Signed checkpoint does not match the corresponding historical block")
            # Registry head metadata describes the complete export, not an older
            # prefix. Verify that prefix's original blocks independently.
            prefix_metadata = {key: value for key, value in metadata.items() if key not in ("head_index", "head_hash")}
            prefix_report = verify_ledger(blocks[:index + 1], prefix_metadata)
            if not prefix_report["valid"]:
                result["errors"].extend(prefix_report["errors"])
                return result
            suffix_count = len(blocks) - index - 1
            result.update(prefix_verified=True, anchored_prefix_index=index,
                          anchored_prefix_count=index + 1, unanchored_suffix_count=suffix_count,
                          verification_scope="checkpointed_prefix" if suffix_count else "full_record_history")
            report = verify_ledger(blocks, metadata)
            if not report["valid"]:
                result["errors"].extend(report["errors"])
                return result
            result["history_verified"] = suffix_count == 0
        result["valid"] = True
    except InvalidSignature:
        result["errors"].append({"message": "Checkpoint signature verification failed"})
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError) as error:
        result["errors"].append({"message": str(error)})
    return result


def load_signing_key(path, allow_create=True):
    path = Path(path).resolve()
    if not path.exists():
        if not allow_create:
            raise ValueError("Signing key is missing but verification keys already exist; restore the protected key")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as output:
                output.write(private)
                output.flush()
                os.fsync(output.fileno())
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Signing key permissions must restrict access to its owner")
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError):
        raise ValueError("Configured signing key is invalid; it was not replaced") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Configured signing key must be Ed25519; it was not replaced")
    return key


class CheckpointService:
    """HTTP callers must authorize ledger access before invoking these methods."""
    def __init__(self, store, key_path, audit=None):
        self.store, self.audit = store, audit
        with store.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS verification_keys (
                key_id TEXT PRIMARY KEY, public_key JSONB NOT NULL, created_at_ms BIGINT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS signing_key_bindings (
                path_id TEXT PRIMARY KEY, key_id TEXT NOT NULL REFERENCES verification_keys(key_id))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS signed_checkpoints (
                checkpoint_id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL REFERENCES ledger_registry(id) ON DELETE CASCADE,
                block_index BIGINT NOT NULL, created_at_ms BIGINT NOT NULL, checkpoint JSONB NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS signed_checkpoints_ledger ON signed_checkpoints(ledger_id, created_at_ms DESC)")
            # Serialize first initialization so concurrent processes cannot create different keys.
            connection.execute("SELECT pg_advisory_xact_lock(834220)")
            path_id = hashlib.sha256(str(Path(key_path).resolve()).encode("utf-8")).hexdigest()
            binding = connection.execute("SELECT key_id FROM signing_key_bindings WHERE path_id=%s", (path_id,)).fetchone()
            existing = binding is not None
            self.key = load_signing_key(key_path, allow_create=not existing)
            self.verification_key = key_document(self.key.public_key())
            if binding and binding["key_id"] != self.verification_key["key_id"]:
                raise ValueError("Configured signing key changed unexpectedly; restore the original protected key or explicitly configure a new key path")
            connection.execute("INSERT INTO verification_keys VALUES (%s,%s,%s) ON CONFLICT (key_id) DO NOTHING",
                               (self.verification_key["key_id"], Jsonb(self.verification_key), time.time_ns() // 1000000))
            connection.execute("INSERT INTO signing_key_bindings VALUES (%s,%s) ON CONFLICT (path_id) DO NOTHING", (path_id, self.verification_key["key_id"]))

    def create(self, ledger_id):
        with self.store.connect() as connection:
            metadata = self.store._metadata(connection, ledger_id)
            blocks = self.store._blocks(connection, ledger_id)
            if not verify_ledger(blocks, metadata)["valid"]:
                raise LedgerConflict("Cannot sign an invalid ledger")
            checkpoint = sign_checkpoint(self.key, ledger_id, blocks[-1]["index"], blocks[-1]["hash"])
            connection.execute("INSERT INTO signed_checkpoints VALUES (%s,%s,%s,%s,%s)",
                               (checkpoint["checkpoint_id"], ledger_id, checkpoint["index"], checkpoint["created_at_ms"], Jsonb(checkpoint)))
            if self.audit is not None:
                self.audit.record("checkpoint", "create", "accepted", "checkpoint_created", ledger_id=ledger_id, connection=connection)
        return checkpoint

    def list(self, ledger_id, limit=50, offset=0):
        limit, offset = page_values(limit, offset)
        with self.store.connect() as connection:
            self.store._metadata(connection, ledger_id)
            count = connection.execute("SELECT COUNT(*) AS total FROM signed_checkpoints WHERE ledger_id=%s", (ledger_id,)).fetchone()["total"]
            rows = connection.execute("SELECT checkpoint FROM signed_checkpoints WHERE ledger_id=%s ORDER BY created_at_ms DESC,checkpoint_id DESC LIMIT %s OFFSET %s",
                                      (ledger_id, limit, offset)).fetchall()
        return {"checkpoints": [row["checkpoint"] for row in rows], "total": count, "limit": limit, "offset": offset}

    def get(self, ledger_id, checkpoint_id):
        canonical_uuid(checkpoint_id, "Checkpoint ID")
        with self.store.connect() as connection:
            self.store._metadata(connection, ledger_id)
            row = connection.execute("SELECT checkpoint FROM signed_checkpoints WHERE checkpoint_id=%s AND ledger_id=%s", (checkpoint_id, ledger_id)).fetchone()
        if row is None:
            raise StoreNotFound("Checkpoint not found")
        return row["checkpoint"]

    def public_key(self, key_id):
        if not is_hash(key_id):
            raise StoreNotFound("Verification key not found")
        with self.store.connect() as connection:
            row = connection.execute("SELECT public_key FROM verification_keys WHERE key_id=%s", (key_id,)).fetchone()
        if row is None:
            raise StoreNotFound("Verification key not found")
        return row["public_key"]

    def verify(self, ledger_id, checkpoint):
        validate_checkpoint(checkpoint)
        return verify_checkpoint(checkpoint, self.public_key(checkpoint["key_id"]), ledger_id, self.store.read(ledger_id))

