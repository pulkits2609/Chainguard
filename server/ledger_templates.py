"""Named ordinary fixed schemas. Template metadata never changes block hashing."""

from copy import deepcopy
from datetime import datetime
import re


def fields(*items):
    return [{"name": name, "type": kind, "required": required} for name, kind, required in items]


TEMPLATES = {
    "shipment_events": {"name": "Shipment Events Ledger", "fields": fields(
        ("shipment_id", "string", True), ("event_type", "string", True),
        ("event_time", "string", True), ("location", "string", False),
        ("source_system", "string", True), ("operator_id", "string", False),
        ("reference_id", "string", False))},
    "warehouse_inventory": {"name": "Warehouse Inventory Ledger", "fields": fields(
        ("warehouse_id", "string", True), ("item_id", "string", True),
        ("event_type", "string", True), ("quantity", "integer", True),
        ("event_time", "string", True), ("source_system", "string", True))},
    "chain_of_custody": {"name": "Chain of Custody Ledger", "fields": fields(
        ("shipment_id", "string", True), ("custody_event", "string", True),
        ("from_party", "string", False), ("to_party", "string", False),
        ("event_time", "string", True), ("source_system", "string", True))},
    "security_events": {"name": "Generic Security Events Ledger", "fields": fields(
        ("event_type", "string", True), ("source_system", "string", True),
        ("resource_id", "string", False), ("event_time", "string", True),
        ("details", "object", False))},
}


def templates():
    return [{"id": key, **deepcopy(value)} for key, value in TEMPLATES.items()]


def template_fields(template_id):
    if not isinstance(template_id, str) or template_id not in TEMPLATES:
        raise ValueError("Unknown ledger template")
    return deepcopy(TEMPLATES[template_id]["fields"])


def validate_template_event(data, template_id):
    if not template_id:
        return
    value = data.get("event_time")
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("event_time must be an RFC 3339 timestamp with timezone")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise ValueError("event_time is not a valid timestamp") from None
    for name in ("shipment_id", "warehouse_id", "item_id", "event_type", "custody_event", "source_system"):
        if name in data and (not isinstance(data[name], str) or not 1 <= len(data[name]) <= 128
                             or not data[name].strip() or any(ord(c) < 32 for c in data[name])):
            raise ValueError(name + " must contain 1–128 non-control characters")
