"""Build, configure, initialize and run ChainGuard with one cross-platform command."""
import argparse
import getpass
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys

from tools import start_ledger as launcher


ROOT = Path(__file__).resolve().parent


def arguments(values=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate configuration and initialize schema/keys without starting')
    parser.add_argument('--build-only', action='store_true', help='Prepare dependencies/frontend only; no database access')
    parser.add_argument('--skip-build', action='store_true', help='Use an already prepared Python environment and frontend build')
    parser.add_argument('--non-interactive', action='store_true', help='Fail clearly instead of prompting for missing configuration')
    parser.add_argument('--create-database', action='store_true', help='Explicitly create missing chainguard if this PostgreSQL role has permission')
    parser.add_argument('--prepared', action='store_true', help=argparse.SUPPRESS)
    result = parser.parse_args(values)
    if result.build_only and (result.check or result.create_database or result.skip_build):
        parser.error('--build-only cannot be combined with --check, --create-database or --skip-build')
    return result


def private_file(path, label):
    if not path.is_file():
        raise ValueError(label + ' must refer to an existing file')
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise ValueError(label + ' must be accessible only to its owner (chmod 600)')


def validate_configuration(root=ROOT):
    sys.path.insert(0, str(ROOT / 'server'))
    from settings import environment_bool, load_environment, public_origin
    from psycopg.conninfo import conninfo_to_dict
    config = root / '.env'
    if config.exists():
        private_file(config, '.env')
        load_environment(config)
    dsn = os.environ.get('LEDGER_DATABASE_URL', '')
    try:
        parameters = conninfo_to_dict(dsn)
    except Exception:
        raise ValueError('LEDGER_DATABASE_URL is malformed; URL-encode reserved password characters') from None
    if not dsn or parameters.get('dbname') != 'chainguard':
        raise ValueError('LEDGER_DATABASE_URL must select the separate chainguard database')
    development = environment_bool('LEDGER_DEV_MODE')
    secure = environment_bool('LEDGER_SECURE_COOKIES', True)
    bind = os.environ.get('LEDGER_BIND', '127.0.0.1')
    try:
        port = int(os.environ.get('LEDGER_PORT', '8000'))
    except ValueError:
        raise ValueError('LEDGER_PORT must be an integer') from None
    if not 1 <= port <= 65535:
        raise ValueError('LEDGER_PORT must be between 1 and 65535')
    origin = public_origin(os.environ.get('LEDGER_PUBLIC_ORIGIN', ''))
    cert, key = os.environ.get('LEDGER_TLS_CERT'), os.environ.get('LEDGER_TLS_KEY')
    if bool(cert) != bool(key):
        raise ValueError('Configure both LEDGER_TLS_CERT and LEDGER_TLS_KEY')
    if development:
        if bind not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Development HTTP may bind only to loopback')
        if not cert and secure:
            raise ValueError('Local HTTP requires LEDGER_SECURE_COOKIES=false')
    elif not cert or not secure or not origin.startswith('https://'):
        raise ValueError('Production requires a real TLS certificate/key, HTTPS origin and secure cookies')
    if cert:
        if not origin.startswith('https://'):
            raise ValueError('TLS requires an HTTPS public origin')
        certificate, private_key = Path(cert).expanduser(), Path(key).expanduser()
        if not certificate.is_absolute():
            certificate = root / certificate
        if not private_key.is_absolute():
            private_key = root / private_key
        if not certificate.is_file():
            raise ValueError('Configured TLS certificate does not exist')
        private_file(private_key, 'TLS private key')
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certificate, private_key)
        except (OSError, ValueError):
            raise ValueError('TLS certificate/private key could not be loaded; verify their format and matching pair') from None
    return dsn


def initialize_database(dsn, create_database=False):
    sys.path.insert(0, str(ROOT / 'server'))
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from accounts import Accounts
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(dsn).get('dbname') != 'chainguard':
        raise ValueError('Database initialization permits only chainguard')
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            pass
    except psycopg.Error as error:
        missing = isinstance(error, psycopg.errors.InvalidCatalogName) or error.sqlstate == '3D000'
        # libpq may expose startup FATAL errors as OperationalError without a SQLSTATE.
        missing |= bool(re.search(r'database "[^"\r\n]+" does not exist', str(error), re.IGNORECASE))
        if not missing:
            raise
        if not create_database:
            raise ValueError('Database chainguard does not exist. Create it with an authorized PostgreSQL role, or explicitly use --create-database') from None
        # Connect only to the standard maintenance database; never use the original ledger database.
        with psycopg.connect(make_conninfo(dsn, dbname='postgres'), autocommit=True, connect_timeout=5) as connection:
            permitted = connection.execute('SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0]
            if not permitted:
                raise ValueError('Configured PostgreSQL role cannot create databases; ask an authorized operator to create chainguard owned by that role')
            try:
                connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier('chainguard')))
            except psycopg.errors.DuplicateDatabase:
                pass  # Another authorized startup created it; do not replace it.
    accounts = Accounts(dsn)
    with accounts.connect() as connection:
        existing = connection.execute('SELECT username FROM users WHERE is_admin').fetchone()
    return accounts, existing is not None


def ensure_admin(accounts, exists, non_interactive=False):
    if exists:
        print('Existing administrator preserved; no password changed.')
        return
    username = os.environ.get('LEDGER_ADMIN_USERNAME', '')
    password = os.environ.get('LEDGER_ADMIN_PASSWORD', '')
    if not username or not password:
        if non_interactive or not sys.stdin.isatty():
            raise ValueError('No administrator exists. Set bootstrap credentials privately in .env/process environment or run interactively')
        username = launcher.ask('First administrator username', 'chainguard_admin')
        password = getpass.getpass('Administrator password, 12–128 characters (hidden): ')
        if password != getpass.getpass('Repeat administrator password (hidden): '):
            raise ValueError('Administrator passwords do not match; no account was created')
    accounts.initialize_admin(username, password)
    print('Administrator initialized. The password was not printed or saved by this script.')


def prepared_main(args, root=ROOT):
    if not (root / 'frontend/dist/index.html').is_file():
        raise ValueError('Frontend build is missing. Run the deployment command without --skip-build')
    dsn = validate_configuration(root)
    accounts, admin_exists = initialize_database(dsn, args.create_database)
    command = [sys.executable, 'server/app.py']
    if (root / '.env').exists():
        command += ['--env-file', '.env']
    launcher.run(command + ['--check-db'], root)
    if args.check:
        print('Configuration, database schema and persistent keys checked; no server started.')
        if not admin_exists:
            print('No administrator exists yet; a normal interactive launch will prompt once.')
        return 0
    ensure_admin(accounts, admin_exists, args.non_interactive)
    launcher.run(command, root)
    return 0


def postgres_failure(error):
    # Never repeat a DSN, password or driver exception in terminal output.
    message = str(error).lower()
    if 'password authentication failed' in message:
        return 'PostgreSQL password authentication failed; correct the private .env credentials'
    if 'connection refused' in message:
        return 'PostgreSQL connection refused; start PostgreSQL and verify the configured host/port'
    if 'no password supplied' in message:
        return 'PostgreSQL requires a password; configure it privately in .env'
    return 'PostgreSQL initialization failed; verify the configured role, database permissions and service availability'


def main(values=None):
    args = arguments(values)
    try:
        if sys.version_info < (3, 11):
            raise ValueError('Install Python 3.11 or newer first')
        if args.prepared or args.skip_build:
            return prepared_main(args)
        python = launcher.prepare(ROOT)
        if args.build_only:
            print('Dependencies and frontend prepared; no database or server accessed.')
            return 0
        if not (ROOT / '.env').exists():
            if args.non_interactive or not sys.stdin.isatty():
                raise ValueError('Configure .env first, or run this script interactively for the first-time configuration wizard')
            launcher.create_configuration(ROOT)
        forwarded = ['--prepared']
        for flag in ('check', 'non_interactive', 'create_database'):
            if getattr(args, flag):
                forwarded.append('--' + flag.replace('_', '-'))
        result = subprocess.run([str(python), str(ROOT / 'deploy.py'), *forwarded], cwd=ROOT, check=False)
        return result.returncode
    except subprocess.CalledProcessError as error:
        print('Deployment stopped: a build/start command failed (exit ' + str(error.returncode) + '). See its message above.', file=sys.stderr)
        return 1
    except ImportError:
        print('Deployment stopped: Python dependencies are missing. Run without --skip-build to prepare them.', file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print('Deployment stopped: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nDeployment stopped.')
        return 0
    except Exception as error:
        if type(error).__module__.startswith('psycopg'):
            print('Deployment stopped: ' + postgres_failure(error), file=sys.stderr)
            return 1
        raise


if __name__ == '__main__':
    raise SystemExit(main())
