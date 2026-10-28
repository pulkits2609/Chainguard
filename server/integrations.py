"""Authenticated REST/webhook adapters and persistent API security policies.

The ChainGuard HMAC profile signs the exact raw request bytes. Its event timestamp
is a required field inside that signed JSON body, not an unsigned HTTP header.
"""

import datetime as dt
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import re
import secrets
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken
from psycopg.types.json import Jsonb

from ledger import MAX_BODY_BYTES, LedgerConflict, canonical_json, parse_json
from security_audit import AuditService
from store import StoreForbidden, StoreNotFound, token_hash, validate_label


OPERATIONS = {"append", "read", "verify", "snapshot_export"}
PATH_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}$")
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
HEADER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
DEFAULT_SIGNATURE_HEADER = "X-ChainGuard-Signature"
SIGNED_WINDOW_SECONDS = 300


class IntegrationError(ValueError):
    def __init__(self, status, message, reason_code="request_rejected", retry_after=None):
        super().__init__(message)
        self.status, self.message = status, message
        self.reason_code, self.retry_after = reason_code, retry_after


def timestamp_seconds(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40 or not TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError("Timestamp must be an RFC 3339 string with a timezone")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        raise ValueError("Timestamp must be an RFC 3339 string with a timezone") from None


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_scopes(value):
    if not isinstance(value, list) or len(value) > len(OPERATIONS) or any(not isinstance(item, str) for item in value):
        raise ValueError("Credential scopes must be a list of supported operations")
    if len(set(value)) != len(value) or set(value) - OPERATIONS:
        raise ValueError("Unsupported or duplicate credential scope")
    return list(value)


def validate_policy(value, *, existing=None, max_requests_per_minute=600, max_body_bytes=MAX_BODY_BYTES):
    if not isinstance(value, dict):
        raise ValueError("Policy must be an object")
    allowed = {"enabled", "requests_per_minute", "allowed_operations", "allowed_source_cidrs",
               "max_body_bytes", "credential_expires_at", "require_authentication"}
    if set(value) - allowed:
        raise ValueError("Unsupported API security policy setting")
    result = {"enabled": True, "requests_per_minute": min(60, max_requests_per_minute),
              "allowed_operations": ["append"], "allowed_source_cidrs": [],
              "max_body_bytes": max_body_bytes, "credential_expires_at": None,
              "require_authentication": True}
    result.update(existing or {})
    result.update(value)
    if type(result["enabled"]) is not bool or result["require_authentication"] is not True:
        raise ValueError("Integration authentication is mandatory and enabled must be boolean")
    for key, maximum in (("requests_per_minute", max_requests_per_minute), ("max_body_bytes", max_body_bytes)):
        if type(result[key]) is not int or not 1 <= result[key] <= maximum:
            raise ValueError(key + " must be positive and cannot exceed the server-wide limit")
    result["allowed_operations"] = validate_scopes(result["allowed_operations"])
    cidrs = result["allowed_source_cidrs"]
    if not isinstance(cidrs, list) or len(cidrs) > 64 or any(not isinstance(value, str) for value in cidrs):
        raise ValueError("Source allowlist must contain at most 64 IPv4/IPv6 CIDRs")
    try:
        result["allowed_source_cidrs"] = sorted({str(ipaddress.ip_network(value, strict=False)) for value in cidrs})
    except ValueError:
        raise ValueError("Invalid source IP address or CIDR") from None
    expiry = result["credential_expires_at"]
    if expiry is not None:
        timestamp_seconds(expiry)
    result["updated_at"] = utc_now()
    return result


def extract_path(value, path, *, required=True):
    if not isinstance(path, str) or len(path) > 256 or not PATH_PATTERN.fullmatch(path):
        raise ValueError("Field paths must contain explicit dot-separated object keys")
    selected = value
    for name in path.split("."):
        if not isinstance(selected, dict) or name not in selected:
            if required:
                raise ValueError("A configured webhook field is missing")
            return None, False
        selected = selected[name]
    return selected, True


def scoped_record_id(integration_id, event_id):
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128 or not event_id.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in event_id):
        raise ValueError("Event ID must contain 1–128 printable ASCII characters without spaces")
    return "cg:" + integration_id + ":" + hashlib.sha256(event_id.encode("ascii")).hexdigest()


def is_integration_token(token):
    return isinstance(token, str) and re.fullmatch(r'cg_[0-9a-f]{32}_[A-Za-z0-9_-]{43}', token) is not None


def header_value(headers, name):
    if hasattr(headers, "get_all"):
        values = headers.get_all(name, [])
    else:
        values = [value for key, value in headers.items() if key.lower() == name.lower()]
    if len(values) != 1 or not isinstance(values[0], str):
        raise IntegrationError(401, "Exactly one authentication header is required", "invalid_credentials")
    return values[0]


class IntegrationService:
    def __init__(self, store, secret_key_path=None, max_requests_per_minute=600,
                 max_body_bytes=MAX_BODY_BYTES, audit=None):
        if type(max_requests_per_minute) is not int or not 1 <= max_requests_per_minute <= 100000:
            raise ValueError("Invalid global API rate limit")
        if type(max_body_bytes) is not int or not 1 <= max_body_bytes <= MAX_BODY_BYTES:
            raise ValueError("Global API body limit cannot exceed the existing ledger limit")
        self.store = store
        self.max_requests_per_minute, self.max_body_bytes = max_requests_per_minute, max_body_bytes
        self.secret_key_path = Path(secret_key_path) if secret_key_path else Path(__file__).resolve().parents[1] / ".local" / "integration.key"
        self.audit = audit or AuditService(store)
        with store.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS integrations (
                id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL REFERENCES ledger_registry(id) ON DELETE CASCADE,
                name TEXT NOT NULL, enabled BOOLEAN NOT NULL, revoked BOOLEAN NOT NULL DEFAULT FALSE,
                auth_method TEXT NOT NULL CHECK (auth_method IN ('bearer','hmac')),
                secret_hash TEXT NOT NULL UNIQUE, encrypted_secret TEXT,
                credential_scopes JSONB NOT NULL, field_mapping JSONB NOT NULL,
                event_id_field TEXT NOT NULL, timestamp_field TEXT NOT NULL,
                signature_header TEXT NOT NULL, policy JSONB NOT NULL,
                created_at BIGINT NOT NULL, last_accepted_at BIGINT)""")
            connection.execute("CREATE INDEX IF NOT EXISTS integrations_ledger ON integrations (ledger_id)")
            connection.execute("""CREATE TABLE IF NOT EXISTS integration_rate_windows (
                identity TEXT NOT NULL, window_start BIGINT NOT NULL, request_count INTEGER NOT NULL,
                PRIMARY KEY (identity, window_start))""")
            connection.execute("CREATE INDEX IF NOT EXISTS integration_rate_expiry ON integration_rate_windows (window_start)")
            encrypted = connection.execute("SELECT 1 FROM integrations WHERE encrypted_secret IS NOT NULL LIMIT 1").fetchone()
        if encrypted:
            self._cipher(create=False)

    def _cipher(self, create=False):
        path = self.secret_key_path
        if path.is_symlink():
            raise ValueError("Integration key must be a protected regular file")
        if create and not path.exists():
            # Never regenerate a key if encrypted database secrets already exist.
            with self.store.connect() as connection:
                existing = connection.execute("SELECT 1 FROM integrations WHERE encrypted_secret IS NOT NULL LIMIT 1").fetchone()
            if existing:
                raise ValueError("Integration encryption key is missing; restore the original key")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as file:
                    file.write(Fernet.generate_key())
                    file.flush()
                    os.fsync(file.fileno())
        try:
            return Fernet(path.read_bytes())
        except (OSError, ValueError):
            raise ValueError("Integration encryption key is missing or invalid; restore the original key") from None

    @staticmethod
    def _public(row):
        keys = ("id", "ledger_id", "name", "enabled", "auth_method", "credential_scopes",
                "field_mapping", "event_id_field", "timestamp_field", "signature_header",
                "policy", "created_at", "last_accepted_at")
        return {key: row[key] for key in keys} | {"webhook_path": "/api/integrations/" + row["id"] + "/webhook"}

    def _owner(self, user, ledger_id, connection):
        if not user:
            raise StoreForbidden("Authentication is required")
        ledger = self.store._metadata(connection, ledger_id)
        if not user.get("is_admin") and ledger["owner_id"] != user.get("id"):
            raise StoreForbidden("Ledger owner or administrator access is required")
        return ledger

    def _row(self, connection, integration_id, user=None, lock=False, include_revoked=False):
        try:
            if str(uuid.UUID(integration_id)) != integration_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise StoreNotFound("Integration not found") from None
        row = connection.execute("SELECT * FROM integrations WHERE id=%s", (integration_id,)).fetchone()
        if not row or (row["revoked"] and not include_revoked):
            raise StoreNotFound("Integration not found")
        # Match ledger append/deletion lock order: registry first, then connector.
        self.store._metadata(connection, row["ledger_id"], lock=lock)
        row = connection.execute("SELECT * FROM integrations WHERE id=%s" + (" FOR UPDATE" if lock else " FOR SHARE"), (integration_id,)).fetchone()
        if not row or (row["revoked"] and not include_revoked):
            raise StoreNotFound("Integration not found")
        if user is not None:
            self._owner(user, row["ledger_id"], connection)
        return row

    def create(self, user, value):
        allowed = {"ledger_id", "name", "enabled", "auth_method", "credential_scopes", "field_mapping",
                   "event_id_field", "timestamp_field", "signature_header", "policy"}
        if not isinstance(value, dict) or set(value) - allowed or not {"ledger_id", "name"} <= set(value):
            raise ValueError("Integration needs a ledger_id, name, and supported configuration settings")
        auth_method = value.get("auth_method", "bearer")
        if auth_method not in ("bearer", "hmac"):
            raise ValueError("Authentication method must be bearer or hmac")
        enabled = value.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("Integration enabled must be boolean")
        scopes = validate_scopes(value.get("credential_scopes", ["append"]))
        policy = validate_policy(value.get("policy", {}), max_requests_per_minute=self.max_requests_per_minute, max_body_bytes=self.max_body_bytes)
        integration_id = str(uuid.uuid4())
        secret = ("cg_" + uuid.UUID(integration_id).hex + "_" if auth_method == "bearer" else "") + secrets.token_urlsafe(32)
        encrypted = self._cipher(create=True).encrypt(secret.encode()).decode() if auth_method == "hmac" else None
        with self.store.connect() as connection:
            ledger = self._owner(user, value["ledger_id"], connection)
            config = self._validate_mapping(value, ledger["fields"])
            connection.execute("""INSERT INTO integrations
                (id,ledger_id,name,enabled,auth_method,secret_hash,encrypted_secret,credential_scopes,
                 field_mapping,event_id_field,timestamp_field,signature_header,policy,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (integration_id, value["ledger_id"], validate_label(value["name"], "Integration name", 80), enabled,
                 auth_method, token_hash(secret), encrypted, Jsonb(scopes), Jsonb(config["field_mapping"]),
                 config["event_id_field"], config["timestamp_field"], config["signature_header"], Jsonb(policy), time.time_ns() // 1000000))
            self.audit.record("integration_created", "configure", "success", "integration_created", integration_id=integration_id,
                              ledger_id=value["ledger_id"], connection=connection)
            result = self._public(self._row(connection, integration_id))
        return {"integration": result, "secret": secret}

    def _validate_mapping(self, value, fields, existing=None):
        result = {"field_mapping": {field["name"]: field["name"] for field in fields},
                  "event_id_field": "event_id", "timestamp_field": "event_time", "signature_header": DEFAULT_SIGNATURE_HEADER}
        if existing:
            result.update({key: existing[key] for key in result})
        result.update({key: value[key] for key in result if key in value})
        mapping = result["field_mapping"]
        names = {field["name"] for field in fields}
        if not isinstance(mapping, dict) or set(mapping) - names or len(mapping) > 32:
            raise ValueError("Webhook mappings must target fields in the ledger's fixed schema")
        if any(field["required"] and field["name"] not in mapping for field in fields):
            raise ValueError("Map every required ledger field")
        for path in [*mapping.values(), result["event_id_field"], result["timestamp_field"]]:
            if not isinstance(path, str) or len(path) > 256 or not PATH_PATTERN.fullmatch(path):
                raise ValueError("Webhook mappings require explicit dot-separated object paths")
        if not isinstance(result["signature_header"], str) or not HEADER_PATTERN.fullmatch(result["signature_header"]) or result["signature_header"].lower() in {"authorization", "host", "cookie", "content-length", "content-type"}:
            raise ValueError("Invalid webhook signature header")
        return result

    def list(self, user):
        if not user:
            raise StoreForbidden("Authentication is required")
        with self.store.connect() as connection:
            rows = connection.execute("""SELECT i.* FROM integrations i JOIN ledger_registry l ON i.ledger_id=l.id
                WHERE NOT i.revoked AND (%s OR l.owner_id=%s) ORDER BY i.created_at,i.id LIMIT 1000""",
                (bool(user.get("is_admin")), user.get("id"))).fetchall()
        return [self._public(row) for row in rows]

    def get(self, integration_id, user):
        if not user:
            raise StoreForbidden("Authentication is required")
        with self.store.connect() as connection:
            return self._public(self._row(connection, integration_id, user))

    def update(self, integration_id, user, changes):
        if not user:
            raise StoreForbidden("Authentication is required")
        allowed = {"name", "enabled", "field_mapping", "event_id_field", "timestamp_field", "signature_header"}
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise ValueError("Only integration name, enabled state, and mapping settings may change")
        with self.store.connect() as connection:
            row = self._row(connection, integration_id, user, lock=True)
            ledger = self._owner(user, row["ledger_id"], connection)
            mapping = self._validate_mapping(changes, ledger["fields"], row)
            name = validate_label(changes.get("name", row["name"]), "Integration name", 80)
            enabled = changes.get("enabled", row["enabled"])
            if type(enabled) is not bool:
                raise ValueError("Integration enabled must be boolean")
            connection.execute("""UPDATE integrations SET name=%s,enabled=%s,field_mapping=%s,event_id_field=%s,
                timestamp_field=%s,signature_header=%s WHERE id=%s""", (name,enabled,Jsonb(mapping["field_mapping"]),
                mapping["event_id_field"],mapping["timestamp_field"],mapping["signature_header"],integration_id))
            self.audit.record("integration_updated", "configure", "success", "integration_updated", integration_id=integration_id,
                              ledger_id=row["ledger_id"], connection=connection)
            return self._public(self._row(connection, integration_id))

    def delete(self, integration_id, user):
        if not user:
            raise StoreForbidden("Authentication is required")
        with self.store.connect() as connection:
            row = self._row(connection, integration_id, user, lock=True)
            connection.execute("UPDATE integrations SET enabled=FALSE,revoked=TRUE,encrypted_secret=NULL WHERE id=%s", (integration_id,))
            self.audit.record("integration_revoked", "configure", "success", "credential_revoked", integration_id=integration_id,
                              ledger_id=row["ledger_id"], connection=connection)
        return {"deleted": True}

    def rotate_secret(self, integration_id, user):
        if not user:
            raise StoreForbidden("Authentication is required")
        with self.store.connect() as connection:
            row = self._row(connection, integration_id, user, lock=True)
            secret = ("cg_" + uuid.UUID(integration_id).hex + "_" if row["auth_method"] == "bearer" else "") + secrets.token_urlsafe(32)
            encrypted = self._cipher(create=True).encrypt(secret.encode()).decode() if row["auth_method"] == "hmac" else None
            connection.execute("UPDATE integrations SET secret_hash=%s,encrypted_secret=%s WHERE id=%s", (token_hash(secret), encrypted, integration_id))
            self.audit.record("credential_rotation", "configure", "success", "credential_rotated", integration_id=integration_id,
                              ledger_id=row["ledger_id"], connection=connection)
            return {"integration": self._public(self._row(connection, integration_id)), "secret": secret}

    def policy(self, integration_id, user):
        return self.get(integration_id, user)["policy"]

    def update_policy(self, integration_id, user, changes):
        if not user:
            raise StoreForbidden("Authentication is required")
        with self.store.connect() as connection:
            row = self._row(connection, integration_id, user, lock=True)
            policy = validate_policy(changes, existing=row["policy"], max_requests_per_minute=self.max_requests_per_minute,
                                     max_body_bytes=self.max_body_bytes)
            connection.execute("UPDATE integrations SET policy=%s WHERE id=%s", (Jsonb(policy), integration_id))
            self.audit.record("policy_changed", "configure", "success", "policy_updated", integration_id=integration_id,
                              ledger_id=row["ledger_id"], connection=connection)
            return policy

    def _rate(self, identity, limit):
        now = int(time.time())
        window = now // 60
        # A separate, short transaction commits the rate decision even if the business operation fails.
        with self.store.connect() as connection:
            connection.execute("DELETE FROM integration_rate_windows WHERE window_start < %s", (window - 2,))
            count = connection.execute("""INSERT INTO integration_rate_windows (identity,window_start,request_count)
                VALUES (%s,%s,1) ON CONFLICT (identity,window_start) DO UPDATE
                SET request_count=LEAST(integration_rate_windows.request_count+1,1000001) RETURNING request_count""",
                (identity,window)).fetchone()["request_count"]
        if count > limit:
            raise IntegrationError(429, "API rate limit exceeded", "rate_limited", 60 - now % 60)

    def throttle(self, peer_ip):
        try:
            address = str(ipaddress.ip_address(peer_ip))
        except ValueError:
            raise IntegrationError(403, "A valid peer address is required", "invalid_peer") from None
        self._rate("source:" + hashlib.sha256(address.encode()).hexdigest(), 120)
        return address

    def _check_policy(self, row, operation, peer_ip, body_size):
        policy = row["policy"]
        if row["revoked"] or not row["enabled"] or not policy["enabled"]:
            raise IntegrationError(403, "Integration is disabled or revoked", "credential_revoked")
        expiry = policy["credential_expires_at"]
        if expiry is not None and time.time() >= timestamp_seconds(expiry):
            raise IntegrationError(403, "Integration credential has expired", "credential_expired")
        if operation not in OPERATIONS or operation not in row["credential_scopes"] or operation not in policy["allowed_operations"]:
            raise IntegrationError(403, "Integration does not permit this operation", "operation_denied")
        address = ipaddress.ip_address(peer_ip)
        networks = [ipaddress.ip_network(value) for value in policy["allowed_source_cidrs"]]
        if networks and not any(address in network for network in networks):
            raise IntegrationError(403, "Source address is not allowed", "source_denied")
        if body_size > min(policy["max_body_bytes"], self.max_body_bytes):
            raise IntegrationError(413, "Request body exceeds the integration limit", "body_too_large")
        self._rate("integration:" + row["id"], min(policy["requests_per_minute"], self.max_requests_per_minute))

    def _audit_failure(self, error, operation, peer_ip, row=None):
        try:
            peer_ip = str(ipaddress.ip_address(peer_ip))
        except ValueError:
            peer_ip = None
        self.audit.record("integration_rejected", operation if operation in OPERATIONS else "request", "rejected",
            error.reason_code if isinstance(error, IntegrationError) else "invalid_payload",
            integration_id=row["id"] if row else None, ledger_id=row["ledger_id"] if row else None,
            severity="warning", source_ip=peer_ip)

    def _audit_conflict(self, error, peer_ip, row=None, ledger_id=None):
        if "verification failed" in str(error).lower():
            category, reason = "verification_failed", "invalid_chain"
        elif "record_id" in str(error):
            category, reason = "idempotency_conflict", "conflict"
        else:
            category, reason = "operation_rejected", "ledger_unavailable"
        self.audit.record(category, "append", "rejected", reason, integration_id=row["id"] if row else None,
                          ledger_id=row["ledger_id"] if row else ledger_id, severity="warning", source_ip=peer_ip)

    def _size(self, body_size):
        if type(body_size) is not int or not 0 <= body_size <= self.max_body_bytes:
            raise IntegrationError(413, "Request body exceeds the server limit", "body_too_large")

    def _bearer_row(self, connection, token, ledger_id, write=False):
        if not isinstance(token, str) or not 20 <= len(token) <= 256 or not token.isascii():
            raise IntegrationError(401, "Invalid integration credentials", "invalid_credentials")
        row = connection.execute("SELECT * FROM integrations WHERE secret_hash=%s", (token_hash(token),)).fetchone()
        if not row or row["auth_method"] != "bearer" or not hmac.compare_digest(row["secret_hash"], token_hash(token)):
            raise IntegrationError(401, "Invalid integration credentials", "invalid_credentials")
        if row["ledger_id"] != ledger_id:
            raise IntegrationError(403, "Integration cannot access this ledger", "operation_denied")
        self.store._metadata(connection, ledger_id, lock=write)
        row = connection.execute("SELECT * FROM integrations WHERE id=%s" + (" FOR UPDATE" if write else " FOR SHARE"), (row["id"],)).fetchone()
        if not row or not hmac.compare_digest(row["secret_hash"], token_hash(token)):
            raise IntegrationError(401, "Invalid integration credentials", "invalid_credentials")
        return row

    def authorize(self, token, ledger_id, operation, peer_ip, body_size=0, throttled=False):
        row = None
        try:
            self._size(body_size)
            if not throttled:
                peer_ip = self.throttle(peer_ip)
            with self.store.connect() as connection:
                row = self._bearer_row(connection, token, ledger_id)
                self._check_policy(row, operation, peer_ip, body_size)
                return self._public(row)
        except IntegrationError as error:
            self._audit_failure(error, operation, peer_ip, row)
            raise

    def append(self, token, ledger_id, data, record_id, peer_ip, body_size=0, throttled=False):
        row = None
        try:
            if isinstance(data, bytes):
                body_size = len(data)
            self._size(body_size)
            if not throttled:
                peer_ip = self.throttle(peer_ip)
            with self.store.connect() as connection:
                row = self._bearer_row(connection, token, ledger_id, write=True)
                self._check_policy(row, "append", peer_ip, body_size)
                if isinstance(data, bytes):
                    data = parse_json(data)
                event_id = scoped_record_id(row["id"], record_id) if record_id is not None else None
                result = self.store.append_authorized(ledger_id, data, record_id=event_id, connection=connection)
                connection.execute("UPDATE integrations SET last_accepted_at=%s WHERE id=%s", (time.time_ns() // 1000000,row["id"]))
                self.audit.record("integration_accepted", "append", "success", "duplicate" if result[1] else "recorded",
                                  integration_id=row["id"], ledger_id=ledger_id, source_ip=peer_ip, connection=connection)
                return result
        except (IntegrationError, LedgerConflict, ValueError) as error:
            if isinstance(error, LedgerConflict):
                self._audit_conflict(error, peer_ip, row, ledger_id)
            else:
                self._audit_failure(error, "append", peer_ip, row)
            raise

    def ingest(self, integration_id, raw_body, headers, peer_ip, throttled=False):
        row = None
        try:
            if not isinstance(raw_body, bytes):
                raise ValueError("Webhook body must contain raw bytes")
            self._size(len(raw_body))
            if not throttled:
                peer_ip = self.throttle(peer_ip)
            with self.store.connect() as connection:
                row = self._row(connection, integration_id, lock=True, include_revoked=True)
                if row["revoked"] or not row["enabled"] or not row["policy"]["enabled"]:
                    raise IntegrationError(403, "Integration is disabled or revoked", "credential_revoked")
                if row["auth_method"] == "bearer":
                    authorization = header_value(headers, "Authorization")
                    if not authorization.startswith("Bearer ") or not hmac.compare_digest(token_hash(authorization[7:]), row["secret_hash"]):
                        raise IntegrationError(401, "Invalid webhook credentials", "invalid_credentials")
                else:
                    signature = header_value(headers, row["signature_header"])
                    try:
                        secret = self._cipher().decrypt(row["encrypted_secret"].encode())
                    except (InvalidToken, AttributeError, ValueError):
                        raise IntegrationError(503, "Webhook authentication key is unavailable", "key_unavailable") from None
                    expected = "sha256=" + hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
                    if not signature.isascii() or not hmac.compare_digest(signature, expected):
                        raise IntegrationError(401, "Invalid webhook signature", "invalid_signature")
                self._check_policy(row, "append", peer_ip, len(raw_body))
                value = parse_json(raw_body)
                if not isinstance(value, dict):
                    raise ValueError("Webhook payload must be a JSON object")
                event_id, _ = extract_path(value, row["event_id_field"])
                event_id = scoped_record_id(row["id"], event_id)
                timestamp, _ = extract_path(value, row["timestamp_field"])
                seconds = timestamp_seconds(timestamp)
                if row["auth_method"] == "hmac" and abs(time.time() - seconds) > SIGNED_WINDOW_SECONDS:
                    raise IntegrationError(409, "Signed webhook timestamp is outside the acceptance window", "expired_signature")
                ledger = self.store._metadata(connection, row["ledger_id"])
                fields = {field["name"]: field for field in ledger["fields"]}
                mapped = {}
                for target, source in row["field_mapping"].items():
                    selected, present = extract_path(value, source, required=fields[target]["required"])
                    if present:
                        mapped[target] = selected
                result = self.store.append_authorized(row["ledger_id"], mapped, record_id=event_id, connection=connection)
                connection.execute("UPDATE integrations SET last_accepted_at=%s WHERE id=%s", (time.time_ns() // 1000000,integration_id))
                self.audit.record("integration_accepted", "append", "success", "duplicate" if result[1] else "recorded",
                                  integration_id=row["id"], ledger_id=row["ledger_id"], source_ip=peer_ip, connection=connection)
                return result
        except (IntegrationError, LedgerConflict, ValueError) as error:
            if isinstance(error, LedgerConflict):
                self._audit_conflict(error, peer_ip, row)
            else:
                self._audit_failure(error, "append", peer_ip, row)
            raise
