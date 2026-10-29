"""Read-only operator recovery export for legacy histories beyond HTTP export limits."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from settings import load_environment
from store import Store
import psycopg


def export_history(store, ledger_id, destination):
    view = store.read(ledger_id)
    if not view['verification']['valid']:
        raise ValueError('History verification failed; recovery export was not written')
    destination = Path(destination)
    # Explicit exclusive creation prevents accidental replacement of retained evidence.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        json.dump(view, output, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        output.flush()
        os.fsync(output.fileno())
    return len(view['blocks'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--test-database', action='store_true', help='Explicitly recover from disposable chainguard_test')
    args = parser.parse_args()
    try:
        if (ROOT / '.env').exists():
            load_environment(ROOT / '.env')
        setting = 'LEDGER_TEST_DATABASE_URL' if args.test_database else 'LEDGER_DATABASE_URL'
        dsn = os.environ.get(setting)
        if not dsn:
            raise ValueError('Missing local database configuration')
        from psycopg.conninfo import conninfo_to_dict
        expected = 'chainguard_test' if args.test_database else 'chainguard'
        if conninfo_to_dict(dsn).get('dbname') != expected:
            raise ValueError('Recovery database does not match the explicitly selected environment')
        # Recovery is read-only: do not initialize/migrate tables or signing keys.
        store = Store(dsn, initialize=False)
        count = export_history(store, args.ledger_id, args.output)
        print('Verified history exported:', count, 'blocks. Keep the file private and verify against a separately trusted checkpoint.')
        return 0
    except (psycopg.Error, OSError, ValueError, OverflowError):
        print('Recovery export failed. Check the selected database, ledger integrity, output path, or read budget; sensitive connection details withheld.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
