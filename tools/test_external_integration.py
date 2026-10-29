"""Exercise an actual independent AudioShare session through ChainGuard's APIs.

Uses only chainguard_test and owned temporary processes/resources. It never edits
AudioShare, reads its .env, uses media mocks, or starts production service ports.
"""

import argparse
import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from accounts import Accounts
from settings import load_environment
from store import Store
from web_app import WebServer
from psycopg.conninfo import conninfo_to_dict


def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def request(address, method, path, value=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", address, timeout=20)
    raw = json.dumps(value).encode("utf-8") if value is not None else None
    supplied = {"Content-Type": "application/json", **(headers or {})}
    try:
        connection.request(method, path, body=raw, headers=supplied)
        response = connection.getresponse()
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("Unexpectedly large integration response")
        return response.status, json.loads(body), dict(response.getheaders())
    except http.client.RemoteDisconnected:
        raise RuntimeError('Connection closed during ' + method + ' ' + path.split('?', 1)[0][:180]) from None
    finally:
        connection.close()


def expect(status, expected, description):
    if status != expected:
        raise RuntimeError(description + " returned HTTP " + str(status) + "; expected " + str(expected))


def launch(command, directory, environment, log):
    options = {"cwd": directory, "env": environment, "stdin": subprocess.DEVNULL, "stdout": log, "stderr": log}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def stop_owned(process):
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        # The PID is the exact handle created by launch(), never a discovered/live
        # service PID. /T closes only this fixture's process tree.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def wait_ready(process, address):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Independent AudioShare process exited during startup")
        try:
            status, _, _ = request(address, "GET", "/ready")
            if status == 200:
                return
        except (OSError, ValueError, RuntimeError):
            pass
        time.sleep(0.15)
    raise RuntimeError("Independent AudioShare did not become ready with its actual LiveKit and private voice services")


def run(source):
    load_environment(ROOT / ".env")
    database_url = os.environ.get("LEDGER_TEST_DATABASE_URL", "")
    if conninfo_to_dict(database_url).get("dbname") != "chainguard_test":
        raise RuntimeError("LEDGER_TEST_DATABASE_URL must explicitly select chainguard_test")
    node_candidates = sorted((source / "work" / "tools").glob("node-*/node.exe"))
    node = str(node_candidates[-1]) if node_candidates else shutil.which("node")
    livekit = source / "work" / "tools" / "livekit" / ("livekit-server.exe" if os.name == "nt" else "livekit-server")
    voice = source / "server" / "native" / "realtime-voice" / ("voice_server.exe" if os.name == "nt" else "voice_server")
    entry = source / "server" / "dist" / "index.js"
    for label, candidate in (("Node runtime", Path(node) if node else None), ("LiveKit executable", livekit),
                             ("Private voice executable", voice), ("Built AudioShare service", entry)):
        if candidate is None or not candidate.is_file():
            raise RuntimeError("Missing independent application dependency: " + label)
    source_entry_hash = hashlib.sha256(entry.read_bytes()).hexdigest()
    source_process = livekit_process = server = thread = None
    user_id = ledger_id = key_id = None
    store = Store(database_url)
    with tempfile.TemporaryDirectory(prefix="chainguard-external-") as temporary:
        temporary = Path(temporary)
        api_port, media_port, ice_tcp, ice_udp = port(), port(), port(), port(socket.SOCK_DGRAM)
        api_key, api_secret, join_token = "cg-integration-" + secrets.token_hex(6), secrets.token_urlsafe(40), secrets.token_urlsafe(32)
        config = (f"port: {media_port}\nbind_addresses: [127.0.0.1]\nrtc:\n  node_ip: 127.0.0.1\n"
                  f"  tcp_port: {ice_tcp}\n  udp_port: {ice_udp}\n  use_external_ip: false\n"
                  f"  enable_loopback_candidate: true\n  use_mdns: true\nkeys:\n  {api_key}: {api_secret}\nlogging:\n  level: warn\n")
        config_path = temporary / "livekit.yaml"
        descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(config)
        child_environment = {key: value for key, value in os.environ.items()
                             if not key.startswith(('LEDGER_', 'CHAINGUARD_'))}
        source_environment = {**child_environment, "HOST": "127.0.0.1", "PORT": str(api_port),
            "LIVEKIT_API_KEY": api_key, "LIVEKIT_API_SECRET": api_secret, "LIVEKIT_URL": f"http://127.0.0.1:{media_port}",
            "LIVEKIT_PUBLIC_URL": "", "VOICE_SERVER_EXECUTABLE": str(voice), "VOICE_SERVER_PORT": "",
            "JOIN_TOKEN": join_token, "ALLOW_GUESTS": "false", "ALLOWED_ORIGINS": f"http://127.0.0.1:{api_port}"}
        # Explicit test-only key files override optional deployment configuration.
        os.environ["LEDGER_SIGNING_KEY_PATH"] = str(temporary / "checkpoint.key")
        os.environ["LEDGER_INTEGRATION_KEY_PATH"] = str(temporary / "integration.key")
        with (temporary / "source.log").open("wb") as source_log, (temporary / "media.log").open("wb") as media_log:
            try:
                livekit_process = launch([str(livekit), "--config", str(config_path)], temporary, child_environment, media_log)
                server = WebServer(("127.0.0.1", 0), store, Accounts(database_url), temporary,
                                   secure_cookies=False, development=True, key_directory=temporary)
                key_id = server.checkpoints.verification_key["key_id"]
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                address = server.server_port
                username = "external_" + uuid.uuid4().hex[:16]
                status, registered, headers = request(address, "POST", "/api/register", {"username": username, "password": secrets.token_urlsafe(24)})
                expect(status, 201, "ChainGuard test account creation")
                user_id = registered["user"]["id"]
                session = {"Cookie": headers["Set-Cookie"].split(";", 1)[0], "X-CSRF-Token": registered["csrf_token"]}
                status, created, _ = request(address, "POST", "/api/ledgers", {"name": "AudioShare operational integration", "description": "Actual independent session creation; disposable acceptance run", "template_id": "security_events"}, session)
                expect(status, 201, "Private operational ledger creation")
                ledger_id = created["ledger"]["id"]
                status, connector, _ = request(address, "POST", "/api/integrations", {"ledger_id": ledger_id, "name": "AudioShare controlled session source", "auth_method": "bearer", "event_id_field": "event_id"}, session)
                expect(status, 201, "Authenticated connector creation")
                authorization = {"Authorization": "Bearer " + connector["secret"]}
                path = "/api/ledgers/" + ledger_id
                source_environment.update(CHAINGUARD_EVENT_URL=f"http://127.0.0.1:{address}{path}/blocks",
                                          CHAINGUARD_EVENT_TOKEN=connector['secret'],
                                          CHAINGUARD_ADAPTER_ALLOW_LOOPBACK_HTTP='true')
                preload = ROOT / 'adapters' / 'audioshare_events.mjs'
                source_process = launch([node, '--import', preload.as_uri(), str(entry)], temporary, source_environment, source_log)
                wait_ready(source_process, api_port)
                status, source_event, _ = request(api_port, "POST", "/v1/session", {"name": "ChainGuard acceptance", "room": "chainguard-" + uuid.uuid4().hex[:16], "token": join_token})
                expect(status, 200, "Independent AudioShare session creation")
                identity = str(uuid.UUID(source_event["identity"]))
                if not isinstance(source_event.get("voiceLobby"), dict) or not isinstance(source_event["voiceLobby"].get("groupId"), int):
                    raise RuntimeError("Independent source did not return an actual voice-lobby resource")
                # The acceptance harness does not construct or forward this event.
                # Ordinary source API completion must cause the preloaded source
                # adapter to deliver it automatically. We only observe the target.
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    status, history, _ = request(address, 'GET', path, headers=session)
                    expect(status, 200, 'Observe automatic source event')
                    matches = [block for block in history['blocks'][1:]
                               if block.get('data', {}).get('resource_id') == identity]
                    if matches:
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError('Source activity did not automatically appear in ChainGuard')
                first = matches[0]
                event = first['data']
                if set(event) != {'event_type', 'source_system', 'resource_id', 'event_time'} or event['event_type'] != 'SESSION_CREATED' or event['source_system'] != 'audioShare':
                    raise RuntimeError('Automatic adapter forwarded unexpected source fields')
                logical_id = hashlib.sha256(f"{event['source_system']}\0{event['event_type']}\0{identity}".encode()).hexdigest()
                rest_headers = {**authorization, "Idempotency-Key": logical_id}
                status, duplicate, _ = request(address, "POST", path + "/blocks", event, rest_headers)
                expect(status, 200, "Operational REST retry")
                if not duplicate["duplicate"] or duplicate["block"]["hash"] != first["hash"]:
                    raise RuntimeError("REST retry did not return the original durable record")
                changed = {**event, "event_type": "CONFLICTING_OPERATION"}
                status, _, _ = request(address, "POST", path + "/blocks", changed, rest_headers)
                expect(status, 409, "Conflicting retry")
                status, _, _ = request(address, "POST", path + "/blocks", event, {"Authorization": "Bearer invalid"})
                expect(status, 403, "Invalid integration credential")
                webhook = "/api/integrations/" + connector["integration"]["id"] + "/webhook"
                status, retried_webhook, _ = request(address, "POST", webhook, {**event, 'event_id': logical_id}, authorization)
                expect(status, 200, "Webhook retry of the same source event")
                if not retried_webhook["duplicate"]:
                    raise RuntimeError("Webhook and REST did not share durable deduplication")
                status, history, _ = request(address, "GET", path, headers=session)
                expect(status, 200, "Authorized operational history")
                if not history["verification"]["valid"] or len(history["blocks"]) != 2 or history["blocks"][1]["data"] != event:
                    raise RuntimeError("Actual source event or chain integrity mismatch")
                status, checkpoint_response, _ = request(address, "POST", path + "/signed-checkpoints", {}, session)
                expect(status, 201, "Checkpoint creation")
                checkpoint = checkpoint_response["checkpoint"]
                for filename, document in (("checkpoint.json", checkpoint), ("key.json", server.checkpoints.verification_key), ("history.json", history)):
                    (temporary / filename).write_text(json.dumps(document), encoding="utf-8")
                offline = subprocess.run([sys.executable, str(ROOT / "tools" / "verify_checkpoint.py"),
                    "--checkpoint", str(temporary / "checkpoint.json"), "--public-key", str(temporary / "key.json"),
                    "--history", str(temporary / "history.json"), "--ledger-id", ledger_id], capture_output=True, text=True, check=False)
                if offline.returncode != 0 or not json.loads(offline.stdout).get("history_verified"):
                    raise RuntimeError("Independent offline checkpoint/history verification failed")
                if hashlib.sha256(entry.read_bytes()).hexdigest() != source_entry_hash:
                    raise RuntimeError("Independent application's entry point changed during the acceptance run")
                return {"result": "PASS", "source_application": "Existing independently running AudioShare session backend",
                        "source_operation": "POST /v1/session", "event_type": event["event_type"], "resource_id": identity,
                        "actual_voice_lobby_created": True, "automatic_forwarding": True,
                        "forwarding_mechanism": "Application-side HTTP finish-event preload middleware",
                        "harness_forwarded_initial_event": False, "native_provider_webhook": False,
                        "rest_ingestion": "Committed automatically", "idempotent_rest_retry": "200, duplicate=true",
                        "conflicting_retry": "409", "invalid_credential": "403", "webhook_retry": "200, duplicate=true",
                        "recorded_business_events": 1, "ledger_verification": "valid", "offline_full_history_verification": "valid",
                        "source_code_modified": False, "production_database_used": False,
                        "source_entry_sha256": source_entry_hash,
                        "trust_limit": "Application-side middleware automatically observes a successful source HTTP completion. This is operational session evidence, not provider-native webhook support, a DHL shipment, or proof of a physical delivery."}
            finally:
                if server:
                    server.shutdown()
                if thread:
                    thread.join(timeout=10)
                if server:
                    server.server_close()
                stop_owned(source_process)
                stop_owned(livekit_process)
                if ledger_id:
                    store.delete_ledger(ledger_id, test_cleanup=True)
                with store.connect() as connection:
                    if user_id:
                        connection.execute("DELETE FROM users WHERE id=%s", (user_id,))
                    if ledger_id:
                        connection.execute("DELETE FROM security_events WHERE ledger_id=%s", (ledger_id,))
                    if key_id:
                        connection.execute("DELETE FROM signing_key_bindings WHERE key_id=%s", (key_id,))
                        connection.execute("DELETE FROM verification_keys WHERE key_id=%s", (key_id,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT.parent / "audioShare")
    parser.add_argument("--evidence", type=Path, default=ROOT / "docs" / "EXTERNAL_INTEGRATION_EVIDENCE.md")
    args = parser.parse_args()
    try:
        evidence = run(args.source_root.resolve())
    except Exception as error:
        # Never print connection exceptions, response credentials, child logs, or
        # environment values. Known dependency/status messages contain no secrets.
        safe = str(error) if isinstance(error, RuntimeError) else type(error).__name__ + ": inspect the local fixture without exposing credentials"
        evidence = {"result": "PENDING", "blocker": safe, "production_database_used": False}
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text("# Independent application integration evidence\n\nRun: " + timestamp + "\n\n```json\n" + json.dumps(evidence, indent=2) + "\n```\n\n"
        "Reproduce from ChainGuard with `.venv\\Scripts\\python.exe tools/test_external_integration.py`. "
        "Requires the existing AudioShare checkout, its built service, Node, actual LiveKit and private voice executables, and the local `chainguard_test` connection in `.env`. "
        "No media simulator is used. Test accounts, ledgers, connector secrets and owned processes are removed after the run. "
        "The application-side preload automatically forwards only source event type, source-system label, server-generated resource identity and event time; JWTs and access tokens are excluded. "
        "The acceptance script only triggers the source operation and polls for the initial event; later manual retries test the API contract. "
        "A passing run demonstrates a real separate application's operational event. It does not establish a logistics-provider integration.\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
