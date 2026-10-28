"""Bounded, sanitized PostgreSQL security telemetry, separate from business ledgers."""

from contextlib import nullcontext
import ipaddress
import re
import time
import uuid

from store import StoreForbidden


CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
SEVERITIES = {"info", "warning", "error", "critical"}


class AuditService:
    def __init__(self, store, retention_days=30):
        if type(retention_days) is not int or not 1 <= retention_days <= 365:
            raise ValueError("Audit retention must be between 1 and 365 days")
        self.store, self.retention_days = store, retention_days
        with store.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS security_events (
                id TEXT PRIMARY KEY, timestamp_ms BIGINT NOT NULL,
                category TEXT NOT NULL, severity TEXT NOT NULL,
                integration_id TEXT, ledger_id TEXT,
                operation TEXT NOT NULL, outcome TEXT NOT NULL,
                reason_code TEXT NOT NULL, source_ip TEXT)""")
            connection.execute("CREATE INDEX IF NOT EXISTS security_events_time ON security_events (timestamp_ms DESC, id DESC)")

    def record(self, category, operation, outcome, reason_code, integration_id=None,
               ledger_id=None, severity="info", source_ip=None, connection=None):
        for value in (category, operation, outcome, reason_code):
            if not isinstance(value, str) or not CODE.fullmatch(value):
                raise ValueError("Audit fields must contain bounded reason codes, never request bodies or secrets")
        if severity not in SEVERITIES:
            raise ValueError("Invalid audit severity")
        for value in (integration_id, ledger_id):
            if value is not None and (not isinstance(value, str) or len(value) > 128 or "\x00" in value):
                raise ValueError("Invalid audit resource identifier")
        if source_ip is not None:
            source_ip = str(ipaddress.ip_address(source_ip))
        event = {"id": str(uuid.uuid4()), "timestamp_ms": time.time_ns() // 1000000,
                 "category": category, "severity": severity, "integration_id": integration_id,
                 "ledger_id": ledger_id, "operation": operation, "outcome": outcome,
                 "reason_code": reason_code, "source_ip": source_ip}
        with (nullcontext(connection) if connection is not None else self.store.connect()) as selected:
            selected.execute("DELETE FROM security_events WHERE timestamp_ms < %s",
                             (event["timestamp_ms"] - self.retention_days * 86400000,))
            selected.execute("""INSERT INTO security_events
                (id,timestamp_ms,category,severity,integration_id,ledger_id,operation,outcome,reason_code,source_ip)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", tuple(event.values()))
        return event

    def list(self, user, filters=None):
        if not user or not user.get("is_admin"):
            raise StoreForbidden("Administrator access is required")
        filters = dict(filters or {})
        allowed = {"severity", "category", "integration_id", "ledger_id", "from", "to", "limit", "offset"}
        if set(filters) - allowed:
            raise ValueError("Unsupported security-event filter")
        try:
            limit, offset = int(filters.get("limit", 50)), int(filters.get("offset", 0))
        except (ValueError, TypeError):
            raise ValueError("Pagination must contain integers") from None
        if not 1 <= limit <= 200 or not 0 <= offset <= 100000:
            raise ValueError("Security-event pagination is out of range")
        conditions, values = ["timestamp_ms >= %s"], [int(time.time() * 1000) - self.retention_days * 86400000]
        for key in ("severity", "category", "integration_id", "ledger_id"):
            if key in filters:
                value = filters[key]
                if not isinstance(value, str) or not 1 <= len(value) <= 128:
                    raise ValueError("Invalid security-event filter")
                conditions.append(key + " = %s")
                values.append(value)
        for key, comparator in (("from", ">="), ("to", "<=")):
            if key in filters:
                try:
                    timestamp = int(filters[key])
                except (ValueError, TypeError):
                    raise ValueError("Time filters use Unix milliseconds") from None
                if timestamp < 0:
                    raise ValueError("Invalid time filter")
                conditions.append("timestamp_ms " + comparator + " %s")
                values.append(timestamp)
        with self.store.connect() as connection:
            rows = connection.execute("SELECT * FROM security_events WHERE " + " AND ".join(conditions)
                + " ORDER BY timestamp_ms DESC, id DESC LIMIT %s OFFSET %s", (*values, limit + 1, offset)).fetchall()
        return {"events": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit}
