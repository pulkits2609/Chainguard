"""One build-and-start menu shared by the Windows and Ubuntu launchers."""

import getpass
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from urllib.parse import quote, urlsplit
import venv


ROOT = Path(__file__).resolve().parents[1]


def run(command, root=ROOT, env=None):
    subprocess.run([str(part) for part in command], cwd=root, env=env, check=True)


def prepare(root=ROOT):
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11 or newer is required")
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise ValueError("Install Node.js 22.12+ and npm first")
    result = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    version = tuple(int(part) for part in result.stdout.strip().lstrip("v").split(".")[:2])
    if version < (22, 12):
        raise ValueError("Install Node.js 22.12 or newer before building")
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating the project Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(root / ".venv")
    print("Installing Python dependencies...", flush=True)
    run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", "server/requirements.txt"], root)
    # Always rebuild after a pull; an old dist folder must not hide newer source.
    print("Installing locked frontend dependencies and rebuilding the viewer...", flush=True)
    run([npm, "--prefix", "frontend", "ci"], root)
    run([npm, "--prefix", "frontend", "run", "build"], root)
    return python


def ask(label, default=""):
    suffix = " [" + default + "]" if default else ""
    return input(label + suffix + ": ").strip() or default


def create_configuration(root=ROOT):
    path = root / ".env"
    if path.exists():
        return
    print("\nFirst-time configuration. Your existing database/user must already exist.")
    print("1. Enter connection settings now\n2. Copy the example and edit it yourself")
    choice = ask("Choose", "1")
    if choice not in ("1", "2"):
        raise ValueError("Choose 1 or 2")
    if choice == "2":
        with path.open("x", encoding="utf-8") as output:
            output.write((root / ".env.example").read_text(encoding="utf-8"))
        raise ValueError("Created .env. Edit its PostgreSQL/TLS values, then run this launcher again")
    host = ask("PostgreSQL host", "127.0.0.1")
    database_port = ask("PostgreSQL port", "5432")
    database = ask("Database name", "chainguard")
    if database != 'chainguard':
        raise ValueError('ChainGuard requires the separate chainguard database')
    user = ask("Database user", "chainguard_app")
    password = getpass.getpass("Database password (hidden): ")
    if not database_port.isdecimal() or not 1 <= int(database_port) <= 65535:
        raise ValueError("Invalid PostgreSQL port")
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Use the local PostgreSQL service: localhost, 127.0.0.1 or ::1")
    authority = "[" + host + "]" if ":" in host else host
    url = "postgresql://" + quote(user, safe="") + ":" + quote(password, safe="") + "@" + authority
    url += ":" + database_port + "/" + quote(database, safe="")
    print("\n1. Local development (HTTP, this machine only)\n2. HTTPS deployment (your certificate)")
    mode = ask("Choose", "1")
    if mode not in ("1", "2"):
        raise ValueError("Choose 1 or 2")
    https = mode == "2"
    port = ask("Website/API port", "8443" if https else "8000")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("Invalid website port")
    origin = "http://127.0.0.1:" + port
    cert = key = ""
    if https:
        origin = ask("Public HTTPS origin, including port if needed")
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Enter an HTTPS origin without a path or credentials")
        parsed.port
        cert = str(Path(ask("Certificate/fullchain file path").strip('"')).expanduser().resolve())
        key = str(Path(ask("Private key file path").strip('"')).expanduser().resolve())
        if not Path(cert).is_file() or not Path(key).is_file():
            raise ValueError("Certificate and key files must exist; no configuration was saved")
    values = {"LEDGER_DATABASE_URL": url, "LEDGER_BIND": "0.0.0.0" if https else "127.0.0.1",
              "LEDGER_PORT": port, "LEDGER_ALLOWED_HOSTS": urlsplit(origin).hostname,
              "LEDGER_PUBLIC_ORIGIN": origin.rstrip("/"), "LEDGER_DEV_MODE": "false" if https else "true",
              "LEDGER_SECURE_COOKIES": "true" if https else "false"}
    values['LEDGER_TEST_DATABASE_URL'] = url.rsplit('/', 1)[0] + '/chainguard_test'
    if https:
        values.update(LEDGER_TLS_CERT=cert, LEDGER_TLS_KEY=key)
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Configuration values cannot contain line breaks")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("# Private local configuration. Never commit this file.\n")
        output.write("\n".join(name + "=" + value for name, value in values.items()) + "\n")
    print("Saved private connection settings in .env. Admin passwords are not saved by this launcher.")


def server_command(python):
    return [python, "server/app.py", "--env-file", ".env"]


def menu(python):
    command = server_command(python)
    while True:
        print("\nBuild complete. Choose a startup command:")
        print("1. Start the website/API using .env")
        print("2. Initialize the single administrator (first time only)")
        print("3. Check the PostgreSQL connection")
        print("4. Start with four demo records")
        print("5. Start with additional server arguments")
        print("0. Exit")
        choice = ask("Choose", "1")
        if choice == "0":
            return
        try:
            if choice == "2":
                username = ask("Admin username", "admin")
                password = getpass.getpass("Admin password, 12–128 characters (hidden): ")
                confirmation = getpass.getpass("Repeat password (hidden): ")
                if password != confirmation:
                    print("Passwords do not match. No administrator was changed.")
                    continue
                env = dict(os.environ, LEDGER_ADMIN_USERNAME=username, LEDGER_ADMIN_PASSWORD=password)
                run(command + ["--init-admin"], env=env)
                print("Existing administrator passwords are never reset by this command.")
            elif choice == "3":
                run(command + ["--check-db"])
            elif choice == "4":
                env = dict(os.environ, LEDGER_ADMIN_USERNAME=ask("Existing admin username", "admin"))
                run(command + ["--seed-demo"], env=env)
                return
            elif choice == "5":
                print("Enter server flags only, e.g. --port 8443. Use .env for secrets and certificate paths.")
                print("If changing the public host/port, also update --public-origin or .env to match.")
                arguments = shlex.split(input("Server arguments: "), posix=os.name != "nt")
                if os.name == "nt":
                    arguments = [value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value for value in arguments]
                run(command + arguments)
                return
            elif choice == "1":
                run(command)
                return
            else:
                print("Choose 0–5.")
        except subprocess.CalledProcessError:
            print("The command failed. Check the message above and fix .env if needed.")


def main():
    try:
        python = prepare()
        arguments = sys.argv[1:]
        if arguments == ["--build-only"]:
            print("Build complete; no server was started.")
            return 0
        if not sys.stdin.isatty() and not arguments:
            raise ValueError("An interactive terminal is required for the startup menu; use explicit server flags for automation")
        if not sys.stdin.isatty() and not (ROOT / ".env").exists():
            raise ValueError("Configure .env interactively before using automated startup flags")
        create_configuration()
        if arguments:
            run(server_command(python) + arguments)
        else:
            menu(python)
        return 0
    except subprocess.CalledProcessError as error:
        print("Launcher stopped: a build/start command failed (exit " + str(error.returncode) + "). See the message above.", file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print("Launcher stopped: " + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nLauncher stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
