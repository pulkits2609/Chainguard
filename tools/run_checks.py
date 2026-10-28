"""Reproducible checks; database tests refuse every database except chainguard_test."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from settings import load_environment
from psycopg.conninfo import conninfo_to_dict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pattern', default='test_*.py')
    parser.add_argument('--frontend', action='store_true')
    args = parser.parse_args()
    load_environment(ROOT / '.env')
    dsn = os.environ.get('LEDGER_TEST_DATABASE_URL')
    if not dsn or conninfo_to_dict(dsn).get('dbname') != 'chainguard_test':
        raise SystemExit('Set LEDGER_TEST_DATABASE_URL to chainguard_test; refusing unsafe or missing test database')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    commands = [[sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'server', '-p', args.pattern],
                [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tools', '-p', 'test_*.py']]
    if args.frontend:
        npm = 'npm.cmd' if os.name == 'nt' else 'npm'
        commands += [[npm, '--prefix', 'frontend', 'test'], [npm, '--prefix', 'frontend', 'run', 'build']]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
