"""Public ledger reading, account management and authenticated append operations."""

import argparse
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import mimetypes
import os
from pathlib import Path
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg

from accounts import Accounts, SESSION_SECONDS
from ledger import LedgerConflict, MAX_BODY_BYTES, parse_json, read_existing_ledger
from multi_snapshots import SnapshotService
from settings import environment_bool, load_environment, public_origin
from snapshots import MAX_SNAPSHOT_BYTES
from store import Store, StoreForbidden, StoreNotFound
from ledger_templates import templates
from chainguard_routes import initialize_services, route as chainguard_route
from integrations import IntegrationError, is_integration_token
from shipment_access import ShipmentRateLimit
from request_limits import ReadBudget, ResourceLimitError


class HttpError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class TLSClientDisconnected(ConnectionError):
    """A peer abandoned its handshake before an HTTP request existed."""


class LoginLimits:
    """Small process-local throttle shared by registration and login."""
    def __init__(self):
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, address):
        now = time.monotonic()
        with self.lock:
            # Pruning also bounds memory when many different addresses make requests.
            if len(self.attempts) > 1000:
                self.attempts = defaultdict(deque, {key: values for key, values in self.attempts.items()
                                                   if values and values[-1] > now - 60})
            if len(self.attempts) >= 2000 and address not in self.attempts:
                raise HttpError(429, "Too many account requests; try again later")
            recent = self.attempts[address]
            while recent and recent[0] < now - 60:
                recent.popleft()
            if len(recent) >= 10:
                raise HttpError(429, "Too many account requests; wait one minute")
            recent.append(now)


class WebServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 32

    def __init__(self, address, store, accounts, frontend, allowed_hosts=None,
                 origin=None, secure_cookies=True, development=False, key_directory=None):
        self.store, self.accounts = store, accounts
        self.frontend = Path(frontend).resolve()
        self.origin = public_origin(origin) if origin else None
        self.secure_cookies = secure_cookies
        self.development = development
        self.cookie_name = "__Host-ledger_session" if secure_cookies else "ledger_session"
        self.allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        self.allowed_hosts.update(allowed_hosts or [])
        if self.origin:
            self.allowed_hosts.add(urlsplit(self.origin).hostname)
        self.login_limits = LoginLimits()
        self.workers = threading.BoundedSemaphore(32)
        self.read_budget = ReadBudget(
            reads_per_minute=int(os.environ.get('LEDGER_PUBLIC_READS_PER_MINUTE', '60')),
            customer_attempts_per_minute=int(os.environ.get('LEDGER_CUSTOMER_ATTEMPTS_PER_MINUTE', '30')),
            concurrent_work=int(os.environ.get('LEDGER_CONCURRENT_VERIFICATIONS', '4')))
        initialize_services(self, key_directory)
        super().__init__(address, WebHandler)

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except TLSClientDisconnected:
            pass
        except Exception:
            self.handle_error(request, client_address)
        finally:
            try:
                self.shutdown_request(request)
            finally:
                self.workers.release()


class WebHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)
        if isinstance(self.connection, ssl.SSLSocket):
            # Handshake in this bounded worker; a silent peer must not block the accept loop.
            try:
                self.connection.do_handshake()
            except (ssl.SSLEOFError, ssl.SSLZeroReturnError, ConnectionResetError, BrokenPipeError, TimeoutError):
                raise TLSClientDisconnected('TLS client disconnected before request') from None

    def security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if self.server.secure_cookies:
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def send_json(self, status, value, cookie=None):
        body = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(body) > 8 * 1024 * 1024:
            raise OverflowError('Response exceeds 8 MiB; use bounded record exports')
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.security_headers()
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def check_origin(self):
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if len(hosts) != 1 or len(origins) > 1:
            raise HttpError(403, "Invalid request origin")
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        try:
            host = urlsplit(scheme + "://" + hosts[0])
            if not host.hostname or host.username or host.password or host.path or host.query or host.fragment:
                raise ValueError("Invalid host")
            port = host.port or (443 if scheme == "https" else 80)
            approved = host.hostname in self.server.allowed_hosts and port == self.server.server_port
            if self.server.origin:
                public = urlsplit(self.server.origin)
                approved |= host.hostname == public.hostname and port == (public.port or (443 if public.scheme == "https" else 80))
            if not approved:
                raise ValueError("Unapproved host")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ValueError("Cross-site request")
            if origins:
                supplied = public_origin(origins[0])
                expected = self.server.origin or (scheme + "://" + hosts[0])
                same_origin = supplied == expected
                development = self.server.development and supplied in ("http://localhost:5173", "http://127.0.0.1:5173")
                if not same_origin and not development:
                    raise ValueError("Unapproved origin")
        except ValueError:
            raise HttpError(403, "Only configured hosts and same-origin browser requests are allowed") from None

    def body(self, maximum=MAX_BODY_BYTES, content_type="application/json", raw=False):
        if content_type == 'application/json':
            maximum = min(maximum, self.server.integrations.max_body_bytes)
        sizes = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(sizes) != 1:
            raise ValueError("Exactly one Content-Length is required; chunked bodies are not supported")
        try:
            size = int(sizes[0])
        except ValueError:
            raise ValueError("Invalid Content-Length") from None
        if not 0 <= size <= maximum:
            raise HttpError(413, "Request body exceeds the allowed size")
        if len(self.headers.get_all('Content-Type', [])) != 1 or self.headers.get_content_type() != content_type:
            raise ValueError("Content-Type must be " + content_type)
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError("Incomplete request body")
        return parse_json(data) if content_type == "application/json" and not raw else data

    def session_token(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            cookie = cookies.get(self.server.cookie_name)
            return cookie.value if cookie else ""
        except Exception:
            return ""

    def current_user(self, required=False, csrf=False, admin=False):
        session = self.server.accounts.session(self.session_token())
        if not session:
            if required:
                raise HttpError(401, "Log in to continue")
            return None
        if admin and not session["is_admin"]:
            raise HttpError(403, "Administrator access is required")
        if csrf:
            supplied = self.headers.get("X-CSRF-Token", "")
            if len(supplied) > 128 or not supplied.isascii() or not hmac.compare_digest(supplied, session["csrf_token"]):
                raise HttpError(403, "Session confirmation failed; refresh and try again")
        return session

    def cookie(self, token, expired=False):
        value = self.server.cookie_name + "=" + token + "; Path=/; HttpOnly; SameSite=Lax; Max-Age="
        value += "0" if expired else str(SESSION_SECONDS)
        if self.server.secure_cookies:
            value += "; Secure"
        return value

    @staticmethod
    def metadata(value, user=None):
        result = dict(value)
        owner = result.pop("owner_id", None)
        result["is_owner"] = bool(user and user["id"] == owner)
        return result

    def ledger_access(self, ledger_id, operation="read"):
        metadata = self.server.store.get_ledger(ledger_id)
        user = self.current_user()
        if self.headers.get("Authorization"):
            authorization = self.headers.get_all('Authorization', [])
            if len(authorization) != 1 or not authorization[0].startswith('Bearer '):
                raise StoreForbidden('A valid bearer token is required')
            token = authorization[0][7:]
            if not is_integration_token(token):
                raise StoreForbidden("This credential does not permit the requested operation")
            size = 0
            if self.command != 'GET':
                sizes = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(sizes) != 1 or not sizes[0].isascii() or not sizes[0].isdecimal():
                    raise ValueError('Exactly one valid Content-Length is required')
                size = int(sizes[0])
            self.server.integrations.authorize(token, ledger_id, operation, self.client_address[0], body_size=size)
            return None
        if metadata["visibility"] == "private" and not (user and (
                user['id'] == metadata['owner_id'] or user['is_admin'])):
            raise StoreNotFound("Ledger not found")
        if metadata['visibility'] == 'private' and self.command != 'GET':
            self.current_user(required=True, csrf=True)
        return user

    def route(self):
        self.check_origin()
        parsed = urlsplit(self.path)
        path = parsed.path
        method = self.command
        parts = path.strip('/').split('/')
        group = None
        if len(parts) >= 3 and parts[:3] == ['api', 'customer', 'shipments']:
            group = 'customer'
        elif (parts in (['api', 'overview'], ['api', 'ledgers'], ['api', 'my-ledgers']) and method == 'GET') or (
                len(parts) >= 3 and parts[:2] == ['api', 'ledgers'] and (
                    method == 'GET' or (method == 'POST' and len(parts) >= 4 and parts[3] in (
                        'verify', 'validate-snapshot', 'verify-checkpoint', 'signed-checkpoints')))):
            group = 'ledger'
        if group:
            self.server.read_budget.check(self.client_address[0], group)
            self.server.read_budget.acquire()
            self._read_slot = True
        if chainguard_route(self, parsed):
            return
        if method == "GET" and path == "/api/ledger-templates":
            return self.send_json(200, {"templates": templates()})
        if method == "GET" and path == "/api/me":
            user = self.current_user()
            return self.send_json(200, {"user": Accounts.public_user(user) if user else None,
                                        "csrf_token": user["csrf_token"] if user else None})
        if method == "POST" and path in ("/api/register", "/api/login"):
            self.server.login_limits.check(self.client_address[0])
            value = self.body()
            if not isinstance(value, dict) or set(value) != {"username", "password"}:
                raise ValueError("Expected username and password")
            try:
                if path.endswith("register"):
                    user = self.server.accounts.register(**value)
                else:
                    user = self.server.accounts.login(**value)
            except ValueError as error:
                raise HttpError(400 if path.endswith("register") else 401, str(error)) from None
            # A successful login replaces an existing browser session rather than accumulating it.
            self.server.accounts.logout(self.session_token())
            token, csrf = self.server.accounts.new_session(user["id"])
            return self.send_json(201 if path.endswith("register") else 200,
                                  {"user": user, "csrf_token": csrf}, self.cookie(token))
        if method == "POST" and path == "/api/logout":
            self.current_user(required=True, csrf=True)
            self.body()
            self.server.accounts.logout(self.session_token())
            return self.send_json(200, {"user": None, "csrf_token": None}, self.cookie("", expired=True))
        if method == "GET" and path in ("/api/ledgers", "/api/my-ledgers"):
            private = path == "/api/my-ledgers"
            user = self.current_user(required=private)
            rows = self.server.store.list_ledgers(user["id"], user["is_admin"]) if private else self.server.store.list_ledgers(public_only=True)
            return self.send_json(200, {"ledgers": [self.metadata(row, user) for row in rows]})
        if method == "POST" and path == "/api/ledgers":
            user = self.current_user(required=True, csrf=True)
            value = self.body()
            if not isinstance(value, dict) or not {"name", "description"} <= set(value) or set(value) - {"name", "description", "fields", "template_id", "visibility"}:
                raise ValueError("Expected ledger name, description, fields or template_id, and optional visibility")
            result = self.server.store.create_ledger(user["id"], **value)
            result["ledger"] = self.metadata(result["ledger"], user)
            return self.send_json(201, result)

        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "ledgers"]:
            ledger_id = parts[2]
            operation = parts[3] if len(parts) == 4 else ""
            if len(parts) > 4:
                raise StoreNotFound("API route not found")
            if method == "GET" and not operation:
                self.ledger_access(ledger_id)
                view = self.server.store.read(ledger_id)
                view["ledger"] = self.metadata(view["ledger"], self.current_user())
                return self.send_json(200, view)
            if method == "GET" and operation == "credentials":
                user = self.current_user(required=True)
                return self.send_json(200, self.server.store.credentials(ledger_id, user["id"]))
            if method == "POST" and operation == "rotate-token":
                user = self.current_user(required=True, csrf=True)
                self.body()
                return self.send_json(200, self.server.store.rotate_token(ledger_id, user["id"]))
            if method == "PATCH" and not operation:
                user = self.current_user(required=True, csrf=True)
                changes = self.body()
                if not isinstance(changes, dict):
                    raise ValueError("Expected a metadata object")
                metadata = self.server.store.get_ledger(ledger_id)
                if not user['is_admin'] and (metadata['owner_id'] != user['id'] or set(changes) != {'visibility'}):
                    raise StoreForbidden("Only administrators manage metadata; owners may change visibility")
                ledger = self.server.store.update_ledger(ledger_id, changes, audit=self.server.audit)
                return self.send_json(200, {"ledger": self.metadata(ledger, self.current_user())})
            if method == "DELETE" and not operation:
                self.current_user(required=True, csrf=True, admin=True)
                value = self.body()
                ledger = self.server.store.get_ledger(ledger_id)
                if not isinstance(value, dict) or value != {"confirm": ledger["name"]}:
                    raise ValueError("Confirm deletion using the exact ledger name")
                self.server.store.delete_ledger(ledger_id, audit=self.server.audit)
                return self.send_json(200, {"deleted": True})
            if method == "POST" and operation == "blocks":
                self.server.integrations.throttle(self.client_address[0])
                authorization = self.headers.get_all("Authorization", [])
                if len(authorization) != 1 or not authorization[0].startswith("Bearer "):
                    raise HttpError(401, "A ledger bearer token is required")
                token = authorization[0][7:]
                identifiers = self.headers.get_all("Idempotency-Key", [])
                if len(identifiers) > 1:
                    raise ValueError("Send at most one Idempotency-Key header")
                record_id = identifiers[0] if identifiers else None
                raw = self.body(raw=True)
                if is_integration_token(token):
                    block, duplicate = self.server.integrations.append(token, ledger_id, raw, record_id,
                                                                      self.client_address[0], len(raw), throttled=True)
                else:
                    block, duplicate = self.server.store.append(ledger_id, token, parse_json(raw), record_id=record_id)
                return self.send_json(200 if duplicate else 201, {"block": block, "duplicate": duplicate})
            if method == "GET" and operation == "checkpoint":
                self.ledger_access(ledger_id, 'verify')
                view = self.server.store.read(ledger_id)
                if not view["verification"]["valid"]:
                    raise LedgerConflict("Cannot export a checkpoint from an invalid ledger")
                head = view["blocks"][-1]
                return self.send_json(200, {"schema_version": head["schema_version"], "index": head["index"], "hash": head["hash"]})
            if method == "GET" and operation == "snapshot":
                self.ledger_access(ledger_id, 'snapshot_export')
                query = parse_qs(parsed.query, keep_blank_values=True)
                index = None
                if query:
                    if set(query) != {"block"} or len(query["block"]) != 1 or not query["block"][0].isascii() or not query["block"][0].isdecimal():
                        raise ValueError("Expected one nonnegative block index")
                    index = int(query["block"][0])
                token, index = SnapshotService(self.server.store, ledger_id).export(index)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(token)))
                self.send_header("Content-Disposition", 'attachment; filename="ledger-' + ledger_id + '-' + str(index) + '.ledger"')
                self.security_headers()
                self.end_headers()
                self.wfile.write(token)
                return
            if method == "POST" and operation == "validate-snapshot":
                self.ledger_access(ledger_id, 'verify')
                report = SnapshotService(self.server.store, ledger_id).validate(self.body(MAX_SNAPSHOT_BYTES, "application/octet-stream"))
                return self.send_json(200, report)
            if method == "POST" and operation == "verify":
                self.ledger_access(ledger_id, 'verify')
                if self.body() != {}:
                    raise ValueError("Expected an empty verification request")
                report = self.server.store.read(ledger_id)["verification"]
                if not report['valid']:
                    self.server.audit.record('integrity', 'verify', 'failed', 'chain_mismatch', ledger_id=ledger_id, severity='error')
                return self.send_json(200, report)
        if path.startswith("/api/"):
            raise HttpError(404, "API route not found")
        if method != "GET":
            raise HttpError(405, "Method not allowed")
        self.serve_frontend(path)

    def serve_frontend(self, path):
        target = (self.server.frontend / unquote(path).lstrip("/")).resolve()
        if not target.is_relative_to(self.server.frontend):
            raise HttpError(403, "Invalid file path")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            raise HttpError(404, "Frontend not built or file not found")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.security_headers()
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self):
        self._read_slot = False
        try:
            self.route()
        except ResourceLimitError as error:
            # Only the first source-limit violation per window touches telemetry.
            # Repeated denied guesses stay cheap and never reach credential/database lookup.
            if error.record_audit:
                try:
                    self.server.audit.record('rate_limit', 'read', 'rejected', 'source_read_limit',
                                             severity='warning', source_ip=self.client_address[0])
                except (psycopg.Error, OSError):
                    pass
            body = json.dumps({'error': str(error)}).encode('utf-8')
            self.send_response(429)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Retry-After', str(error.retry_after))
            self.security_headers()
            self.end_headers()
            self.wfile.write(body)
        except HttpError as error:
            self.send_json(error.status, {"error": error.message})
        except IntegrationError as error:
            if error.reason_code == 'rate_limited':
                try:
                    self.server.audit.record('rate_limit', 'request', 'rejected', 'rate_limited',
                                             severity='warning', source_ip=self.client_address[0])
                except (psycopg.Error, OSError):
                    pass
            self.send_response(error.status)
            body = json.dumps({'error': error.message}).encode('utf-8')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            if error.retry_after is not None:
                self.send_header('Retry-After', str(error.retry_after))
            self.security_headers()
            self.end_headers()
            self.wfile.write(body)
        except ShipmentRateLimit as error:
            body = json.dumps({'error': str(error)}).encode('utf-8')
            self.send_response(429)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Retry-After', str(error.retry_after))
            self.security_headers()
            self.end_headers()
            self.wfile.write(body)
        except StoreNotFound as error:
            self.send_json(404, {"error": str(error)})
        except StoreForbidden as error:
            try:
                self.server.audit.record('authorization', 'request', 'rejected', 'access_denied',
                                         severity='warning', source_ip=self.client_address[0])
            except (psycopg.Error, OSError):
                pass  # Denial stays effective even when telemetry storage is unavailable.
            self.send_json(403, {"error": str(error)})
        except LedgerConflict as error:
            self.send_json(409, {"error": str(error)})
        except OverflowError as error:
            self.send_json(413, {"error": str(error)})
        except (socket.timeout, TimeoutError):
            self.send_json(408, {"error": "Request timed out"})
        except (ValueError, UnicodeError, RecursionError) as error:
            self.send_json(400, {"error": str(error)})
        except (psycopg.Error, OSError):
            # Never send a DSN, database exception or secret back to the browser.
            self.send_json(503, {"error": "Storage is unavailable; inspect the local service logs"})
        finally:
            if self._read_slot:
                self.server.read_budget.release()
            self.close_connection = True

    do_GET = handle_request
    do_POST = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request

    def log_request(self, code='-', size='-'):
        # Never write query strings, Authorization or customer capabilities to access logs.
        self.log_message('%s %s %s', self.command, urlsplit(self.path).path[:200], str(code))


def main():
    parser = argparse.ArgumentParser(description="Multi-ledger PostgreSQL server and React viewer")
    parser.add_argument("--env-file")
    parser.add_argument("--database-url")
    parser.add_argument("--bind")
    parser.add_argument("--port", type=int)
    parser.add_argument("--allow-host", action="append", default=[])
    parser.add_argument("--public-origin")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--init-admin", action="store_true")
    parser.add_argument("--check-db", action="store_true")
    parser.add_argument("--seed-demo", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--ledger-id")
    parser.add_argument("--data-dir", help="Read-only verification of an old JSONL directory")
    args = parser.parse_args()
    try:
        load_environment(args.env_file)
        if args.verify and args.data_dir:
            _, report = read_existing_ledger(Path(args.data_dir) / "chain.jsonl")
            print(json.dumps(report, indent=2))
            return 0 if report["valid"] else 1
        database_url = args.database_url or os.environ.get("LEDGER_DATABASE_URL")
        if not database_url:
            raise ValueError("Set LEDGER_DATABASE_URL to your local PostgreSQL database")
        from psycopg.conninfo import conninfo_to_dict
        if conninfo_to_dict(database_url).get('dbname') != 'chainguard':
            raise ValueError('ChainGuard startup requires database chainguard; refusing another database')
        accounts = Accounts(database_url)
        store = Store(database_url)
        if args.init_admin:
            username = os.environ.get("LEDGER_ADMIN_USERNAME", "")
            password = os.environ.get("LEDGER_ADMIN_PASSWORD", "")
            _, created = accounts.initialize_admin(username, password)
            print("Administrator created." if created else "Administrator already exists; password was not changed.")
            return 0
        if args.check_db:
            from types import SimpleNamespace
            initialize_services(SimpleNamespace(store=store))
            print("PostgreSQL is reachable and all ChainGuard tables and persistent keys are ready.")
            return 0
        if args.verify:
            if not args.ledger_id:
                raise ValueError("Supply --ledger-id for PostgreSQL verification")
            report = store.read(args.ledger_id)["verification"]
            print(json.dumps(report, indent=2))
            return 0 if report["valid"] else 1
        if args.seed_demo:
            owner = accounts.find_admin(os.environ.get("LEDGER_ADMIN_USERNAME", ""))
            existing = [item for item in store.list_ledgers(owner["id"]) if item["name"] == "Demo ledger"]
            if not existing:
                result = store.create_ledger(owner["id"], "Demo ledger", "Four explicit demo records",
                                             [{"name": "label", "type": "string", "required": True},
                                              {"name": "value", "type": "integer", "required": True}])
                for index in range(1, 5):
                    store.append(result["ledger"]["id"], result["write_token"],
                                 {"label": "Demo record " + str(index), "value": index * 3}, record_id="DEMO-" + str(index))
                print("Demo ledger created. Its owner can rotate the write token from the dashboard.")
        bind = args.bind or os.environ.get("LEDGER_BIND", "127.0.0.1")
        port = args.port if args.port is not None else int(os.environ.get("LEDGER_PORT", "8000"))
        if not 0 <= port <= 65535:
            raise ValueError("Port must be between 0 and 65535")
        development = environment_bool("LEDGER_DEV_MODE")
        secure = environment_bool("LEDGER_SECURE_COOKIES", True)
        cert = args.tls_cert or os.environ.get("LEDGER_TLS_CERT")
        key = args.tls_key or os.environ.get("LEDGER_TLS_KEY")
        origin = args.public_origin or os.environ.get("LEDGER_PUBLIC_ORIGIN")
        if bool(cert) != bool(key):
            raise ValueError("Set both the TLS certificate and key paths")
        if development:
            if bind not in ("127.0.0.1", "localhost", "::1"):
                raise ValueError("Development HTTP may bind only to loopback; use HTTPS for network deployment")
        elif not cert or not secure or not origin or not public_origin(origin).startswith("https://"):
            raise ValueError("Network deployment requires TLS certificate/key, an HTTPS public origin and secure cookies")
        if not cert and secure:
            raise ValueError("HTTP development requires LEDGER_SECURE_COOKIES=false")
        if cert and origin and not public_origin(origin).startswith("https://"):
            raise ValueError("TLS requires an HTTPS public origin")
        allowed = args.allow_host + [item.strip() for item in os.environ.get("LEDGER_ALLOWED_HOSTS", "").split(",") if item.strip()]
        frontend = Path(__file__).resolve().parents[1] / "frontend/dist"
        server = WebServer((bind, port), store, accounts, frontend, allowed, origin, secure, development)
        if cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        print("Ledger server: " + (origin or ("http://" + bind + ":" + str(server.server_port))), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except psycopg.Error:
        print("PostgreSQL startup failed. Check the local database service and connection configuration.")
        return 1
    except (ValueError, OSError) as error:
        print("Startup failed: " + str(error))
        return 1
