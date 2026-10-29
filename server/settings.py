"""Small environment-file reader. Values are data, never shell commands."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from psycopg.conninfo import conninfo_to_dict


def require_chainguard_database(database_url):
    """Fail before opening a connection to an original or unrelated database."""
    if conninfo_to_dict(database_url).get('dbname') not in ('chainguard', 'chainguard_test'):
        raise ValueError('Only the separate chainguard and chainguard_test databases are permitted')


def database_options():
    """Bound SQL execution and lock waits; validated integers cannot inject options."""
    try:
        statement = int(os.environ.get('LEDGER_DB_STATEMENT_TIMEOUT_MS', '5000'))
        lock = int(os.environ.get('LEDGER_DB_LOCK_TIMEOUT_MS', '2000'))
    except ValueError:
        raise ValueError('Database timeouts must be integer milliseconds') from None
    if not 10 <= lock <= statement <= 60000:
        raise ValueError('Database timeouts require 10 <= lock <= statement <= 60000 milliseconds')
    return '-c statement_timeout=' + str(statement) + ' -c lock_timeout=' + str(lock)


def load_environment(path):
    if not path:
        return
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("Invalid environment entry on line " + str(number))
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError("Invalid environment name on line " + str(number))
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Unclosed environment value on line " + str(number))
            value = value[1:-1]
        # Explicit process variables take priority over a local configuration file.
        os.environ.setdefault(name, value)


def environment_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() not in ("true", "false"):
        raise ValueError(name + " must be true or false")
    return value.lower() == "true"


def public_origin(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Public origin must be an http(s) origin without credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Public origin must not include a path, query or fragment")
    parsed.port  # Reject malformed ports now, before accepting requests.
    return value.rstrip("/")
