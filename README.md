# ChainGuard

**SECURE → RECORD → VERIFY.** Security middleware for existing applications, built on the original Python HTTP server, PostgreSQL store, React UI, and SHA-256 ledger engine. No distributed consensus, cryptocurrency, additional infrastructure, or framework rewrite.

## Start with one script

```powershell
python deploy.py
```

Or double-click **Deploy ChainGuard.bat** on Windows; on Ubuntu use `bash deploy/start-chainguard.sh`. The shared entry point builds the frontend, prepares the project Python environment/dependencies, preserves or configures `.env`, validates database/TLS settings, initializes schema and persistent keys, initializes an absent administrator once, and starts the application directly. Python 3.11+, Node/npm and PostgreSQL must already be installed. No global OS packages, Pi setup or service installation are performed. Production HTTPS requires your actual certificate/key and hostname; development stays loopback-only. See [deployment instructions](docs/DEPLOYMENT.md).

Latest reliability work: [export capacity and retention](docs/EXPORT_AND_RETENTION.md), [in-app browser and polling evidence](docs/BROWSER_RELIABILITY.md), and [HTTPS/key/backup verification](docs/OPERATIONAL_VERIFICATION.md). The repository remains unlicensed by the owner's choice.

## Features

- Shipment Events, Warehouse Inventory, Chain of Custody, and Generic Security Events templates; custom fixed schemas remain supported.
- New templates default to private. Existing/custom generic ledgers retain public defaults. Owners/admins manage visibility without changing historical blocks.
- Existing accounts, sessions, CSRF protection, ledger lifecycle, write-token rotation, idempotency and Fernet snapshots.
- Bearer/HMAC inbound webhooks, explicit dotted field mappings, integration-scoped persistent idempotency, credential generation/rotation/revocation.
- Credential scopes intersect configurable policies for append/read/verify/snapshot export, expiry, IPv4/IPv6 CIDRs, size limits, and persistent rate limits.
- Ed25519 checkpoint creation/history/download, public key export, owner/admin history export, and independent offline verification.
- Hashed expiring/revocable shipment capabilities, redacted customer timelines and evidence, bounded polling and responses.
- Sanitized administrator security events with filters, pagination, and retention.
- Extended React dashboard: Overview, Ledgers, Integrations, API Security, Verification, Security Events, Account.

See [implementation progress](IMPLEMENTATION_PROGRESS.md), [frozen blueprint](docs/CHAINGUARD_BLUEPRINT.md), and [genuine external integration evidence](docs/EXTERNAL_INTEGRATION_EVIDENCE.md). The pre-ChainGuard README is retained in `docs/LEGACY_LEDGER_README.md` for historical reference.

## October security-audit hardening

- [GitHub Actions CI](docs/CI.md) provisions isolated PostgreSQL databases and runs the backend, launcher, adapter and frontend checks, production build, plus an Ubuntu application startup/restart smoke. A workflow definition alone is not a passing hosted run.
- Expensive ledger requests and all customer capability attempts receive source limits **before database lookup**. A separate four-worker verification budget leaves inexpensive account/session routes responsive. PostgreSQL statements default to a 5-second timeout and lock waits to 2 seconds. See `.env.example` for tuning.
- Customer pages use a shipment expression index and bounded pagination. A 64-entry server verification cache expires after 30 seconds and invalidates when ordinary SQL inserts/updates/deletes/truncates change the ledger revision. It retains no full ledger payload. Cache hits retain the original `checked_at_ms`; authorization, expiry and revocation are checked on every request.
- Each customer refresh hashes the chain once and compares at most the latest 20 checkpoints. Responses and the UI disclose when older checkpoint evidence was not compared. Retain independently trusted evidence to verify other historical checkpoints. A privileged database attacker can disable or rewrite revision metadata; periodic expiry is still required, and cached server checks are not independent proof.
- [New template ledgers require managed credentials](docs/MANAGED_CREDENTIALS.md), omit legacy write secrets and reject legacy token rotation/append. Existing ledgers retain their previous credential compatibility; custom generic ledgers still support the legacy path.
- [Application-side event forwarding](docs/AUTOMATIC_INTEGRATION.md) now records real AudioShare session operations automatically. It is explicitly middleware integration, not provider-native webhook support or a logistics feed. Finite in-memory retries do not provide a durable outbox guarantee.

Read gates are intentionally process-local for this single-process deployment: default 60 ledger requests and 30 customer attempts per peer IP per minute, with bounded source bookkeeping. They complement the existing persistent integration and per-grant limits. Only the first denied source request per window is audited, so repeated denied guesses do not create unbounded telemetry writes. Behind NAT, clients share their source allowance; this MVP does not infer proxy trust.

New history writes are capped conservatively at 5 MiB of ASCII-serialized blocks so the complete JSON and encrypted snapshot workflows stay within their existing 8 MiB formats. The separate default 32 MiB/100,000-block read budget remains available for inspection/recovery of legacy histories. Genesis/append checks reject excess growth before commit. Legacy oversized histories have a read-only operator export path; no historical blocks are silently rewritten. Configure budgets before commissioning a ledger. Full-chain append verification remains enabled and writes remain O(N); [measured 1,000/10,000-block results](docs/PERFORMANCE_MEASUREMENTS.md) describe that limitation rather than claiming enterprise throughput.

## Architecture and compatibility

One deployable Python application serves the frontend and same-origin API, backed by one PostgreSQL service. Focused modules handle integrations, policies, audit, checkpoints and shipment capabilities. Existing registry/accounts and per-ledger block/key tables are reused; business records are not duplicated.

`server/ledger.py` keeps canonical schema-v2 serialization, genesis and SHA-256 hashing. Row-locked append transactions preserve index/link/idempotency consistency. Repeatable initialization adds tables and registry visibility/template/head metadata without rewriting blocks. Valid legacy histories receive an initial head reference; invalid history is not silently repaired. Existing snapshot formats and encryption remain unchanged.

**Database isolation:** application startup refuses any database except `chainguard`; the check runner refuses any test database except `chainguard_test`. Never configure the original `blockchain_ledger` database in this checkout.

## Windows setup

Requires Python 3.11+, Node.js 22.12+, npm and PostgreSQL. An authorized PostgreSQL operator must create an ordinary application role and the two separate databases. For example, using a private interactive SQL client:

```sql
CREATE ROLE chainguard_app LOGIN PASSWORD 'SET_A_PRIVATE_STRONG_PASSWORD';
CREATE DATABASE chainguard OWNER chainguard_app;
CREATE DATABASE chainguard_test OWNER chainguard_app;
```

Do not put real passwords in shell history or Git. Copy `.env.example`, then privately set URL-encoded credentials in `.env`:

```dotenv
LEDGER_DATABASE_URL=postgresql://chainguard_app:URL_ENCODED_PASSWORD@127.0.0.1:5432/chainguard
LEDGER_TEST_DATABASE_URL=postgresql://chainguard_app:URL_ENCODED_PASSWORD@127.0.0.1:5432/chainguard_test
LEDGER_BIND=127.0.0.1
LEDGER_PORT=8000
LEDGER_PUBLIC_ORIGIN=http://127.0.0.1:8000
LEDGER_ALLOWED_HOSTS=localhost,127.0.0.1
LEDGER_DEV_MODE=true
LEDGER_SECURE_COOKIES=false
```

Double-click **Start Ledger.bat**. The preserved launcher creates/reuses `.venv`, installs pinned dependencies, runs `npm ci`, and rebuilds the React frontend every time. Choose option **2** once to initialize the administrator, then **1** to start. Normal startup never creates/resets administrators. Registration creates ordinary users. Administrator access is required for global lifecycle controls and Security Events.

Prepared environment commands:

```powershell
.venv\Scripts\python.exe server/app.py --env-file .env --check-db
.venv\Scripts\python.exe server/app.py --env-file .env
```

`--check-db` performs repeatable schema/key initialization; it never creates database roles or modifies another database. Policies, revocations, rate windows, grants, idempotency, keys and checkpoints persist across restarts. The old explicit generic demo launcher is preserved but is not used as external-integration evidence.

## Ubuntu and HTTPS

Install PostgreSQL, Python 3.11+ with venv/pip, and Node 22.12+ with npm. An authorized operator can use `createuser --pwprompt chainguard_app` and `createdb --owner=chainguard_app chainguard`; create `chainguard_test` only on development/test machines. Run `bash deploy/start-ubuntu.sh`; `deploy/setup-ubuntu.sh` remains build-only.

Review `deploy/blockchain-ledger.service` before installation. It uses `/opt/Chainguard`, an unprivileged `ledger` account, `ProtectSystem=strict`, and a writable `StateDirectory=chainguard` with mode 0700. Protected keys persist in `/var/lib/chainguard`. Restrict `.env` and private files to the application account (0600 on Ubuntu); on Windows restrict directory/file ACLs to the application account and authorized administrators.

Production requires real certificate/key paths in `LEDGER_TLS_CERT`/`LEDGER_TLS_KEY`, an HTTPS `LEDGER_PUBLIC_ORIGIN`, `LEDGER_DEV_MODE=false` and `LEDGER_SECURE_COOKIES=true`. Keep certificate verification enabled. The backend terminates TLS directly and uses the socket peer for IP policies; arbitrary forwarding headers are ignored. No reverse proxy trust is inferred. Actual Ubuntu/systemd runtime verification requires an Ubuntu host; Windows checks do not establish that platform's runtime behavior.

## REST and webhook integration

Create a ledger and connector in the dashboard. Save the revealed secret immediately in the producing application's private configuration. Select credential scopes at creation; policies can restrict these scopes but cannot enlarge them. Provision a replacement connector to change scopes. HMAC credentials authenticate their webhook profile; bearer connectors may also use the existing direct REST endpoint.

The existing direct body format remains unchanged. Supply actual source values matching the selected ledger schema:

```http
POST /api/ledgers/LEDGER_UUID/blocks
Authorization: Bearer INTEGRATION_SECRET
Content-Type: application/json
Idempotency-Key: STABLE_SOURCE_EVENT_ID

{"event_type":"SOURCE_OPERATION","source_system":"YOUR_APPLICATION","event_time":"RFC3339_TIMESTAMP","resource_id":"SOURCE_RESOURCE_ID"}
```

This contract example fits the Generic Security Events template; other templates have their own fields shown in the UI and `GET /api/ledger-templates`. It is documentation, not a recorded logistics event. HTTP201 creates a block; a matching retry returns200 with `duplicate=true`; a conflicting retry returns409. Integration identities namespace stored event IDs. Legacy ledger tokens remain independent append-only credentials only for compatible existing/custom generic ledgers. Newly created templates require managed credentials so their writes cannot bypass connector policies.

Webhook endpoint: `POST /api/integrations/INTEGRATION_UUID/webhook`. Configuration includes `ledger_id`, `name`, `auth_method` (`bearer`/`hmac`), `enabled`, `credential_scopes`, `field_mapping` (target→dotted source path), `event_id_field`, `timestamp_field`, `signature_header`, and `policy`. Mapping never runs arbitrary scripts.

The default ChainGuard HMAC profile uses `X-ChainGuard-Signature: sha256=LOWERCASE_HEX_DIGEST`. Sign the **exact raw UTF-8 JSON body bytes** with HMAC-SHA256. The configured timestamp must occur inside that signed body (default `event_time`); both past and future skew are bounded to300 seconds. Event IDs are required and deduplication is durable. For delayed re-signed retries, configure a separate current `signed_at` field while preserving mapped business data/event time. Expired raw signed requests are rejected. Third-party providers require their actual documented signature protocol; no DHL protocol or shipment feed is invented.

Default credential scopes and policy operations are append-only. Other operations are `read`, `verify`, `snapshot_export`. Authentication cannot be disabled. Policy rate defaults to60/minute, below the configurable hard global cap600/minute. Ingestion source IPs also have a persistent120/minute limit. These are fixed minute windows. CIDRs support IPv4/IPv6 and use verified peer addresses. An empty allowlist still requires the remaining controls. Rotation invalidates old secrets; deletion revokes connectors. Bearer secrets are hashed; HMAC secrets are Fernet-encrypted using a protected key outside PostgreSQL.

`LEDGER_MAX_BODY_BYTES` defaults to65536 and caps JSON bodies before reading, including legacy writes. Smaller connector caps also apply to credential-authorized verification uploads. Browser snapshot uploads retain their separate8MiB format limit; JSON responses and snapshot exports are bounded to8MiB. Large histories should use bounded operational ledgers; an unbounded export bypass is not provided. Strict JSON rejects duplicate keys, fractional/unsafe numbers, excessive nesting, unknown schema fields and malformed values. Template timestamps require RFC3339 with timezone.

Failures distinguish invalid authentication, denied authorization/IP, malformed payload, schema mismatch, replay/conflict, HTTP429 with Retry-After, and temporary storage failure. Success follows durable commit. No outbound retry queue/broker is included.

## Checkpoints and offline trust

Use Verification to create/download checkpoints, public keys and owner/admin full-record exports. Keep trusted keys and checkpoints **outside ChainGuard**, obtaining the key/fingerprint independently. A key returned by a compromised server is not automatically trusted.

```powershell
.venv\Scripts\python.exe tools/verify_checkpoint.py --checkpoint checkpoint.json --public-key trusted-key.json --ledger-id EXPECTED_LEDGER_UUID
.venv\Scripts\python.exe tools/verify_checkpoint.py --checkpoint checkpoint.json --public-key trusted-key.json --ledger-id EXPECTED_LEDGER_UUID --history history.json
```

The CLI makes no network requests and exits nonzero for invalid evidence. Signature verification authenticates a checkpoint under the supplied trusted key. History verification recomputes existing canonical hashes and compares the corresponding signed block. An older checkpoint anchors only its prefix: `prefix_verified`, `anchored_prefix_index/count`, and `unanchored_suffix_count` expose that boundary. `history_verified=true` requires the supplied history's head to be covered.

Private keys remain outside PostgreSQL/frontend. Defaults are `.local/chainguard/checkpoint.key` and `.local/chainguard/integration.key`; test services use a distinct `.local/chainguard_test` directory. Set `LEDGER_SIGNING_KEY_PATH` and `LEDGER_INTEGRATION_KEY_PATH` for deployment. Back up protected keys and PostgreSQL together. Missing/corrupt/replaced established keys fail clearly instead of being silently regenerated. Public verification keys remain exportable. Explicitly changing a key path is an operator action, not automatic rotation.

## Customer access and guarantees

Owners/admins create a shipment grant in Verification. The private link uses `?shipment=ID#token=CAPABILITY`; the browser removes the fragment and holds the secret only in memory. Requests use Authorization without browser cookies. Reopening the original link is required after refreshing a page whose fragment was removed. Share it privately; do not feed it to analytics. Default expiry is24 hours and grants are revocable. Customer endpoints are read-only, paginated, throttled and poll every15 seconds.

Only explicitly safe shipment event fields are projected. Other shipments, employee identifiers, arbitrary internal details, credentials, administrative blocks and security logs are excluded. Producing systems must provide public-safe location/source labels.

The UI distinguishes:

- **Checkpoint signature verified:** mathematical check; key trust depends on independent distribution.
- **Shipment records verified by the ChainGuard server:** internal chain and available stored evidence checks performed by the server.
- **Independently verifiable full-record history:** authorized owner verification using complete records and separately trusted evidence.
- **Verification failed:** cryptographic/historical inconsistency.
- **Not independently verified:** redacted customer contents cannot reproduce full-payload block hashes.

A checkpoint signature alone does **not** independently prove redacted shipment contents. No selective disclosure, Merkle field proof, or zero-knowledge scheme is claimed.

## Audit and API inventory

Administrator-only `GET /api/security/events` supports severity/category/integration_id/ledger_id, `from`/`to` in Unix milliseconds, limit and offset. Retention defaults to30 days via `LEDGER_AUDIT_RETENTION_DAYS`. Stored events contain bounded sanitized reason codes, never passwords, tokens, HMAC/private keys or entire request bodies. Connector, checkpoint and grant changes commit atomically with audit rows. Rejected operations stay rejected if telemetry is unavailable.

Existing account, ledger, credential, verify and snapshot APIs remain. Private access protects listings, metadata, content, snapshots, checkpoints and exports. New route families:

| Route | Purpose |
|---|---|
| `GET /api/ledger-templates` | Fixed schemas |
| `GET /api/overview` | Authorized database-backed metrics |
| `/api/integrations` and `/{id}` | Session-authenticated connector CRUD |
| `/api/integrations/{id}/policy` | Read/update policy |
| `POST /api/integrations/{id}/rotate-secret` | Rotate secret |
| `POST /api/integrations/{id}/webhook` | Ingest |
| `/api/ledgers/{id}/signed-checkpoints` and `/{checkpoint_id}` | Create/list/download |
| `GET /api/verification-keys/{key_id}` | Public key distribution |
| `POST /api/ledgers/{id}/verify-checkpoint` | Server comparison |
| `GET /api/ledgers/{id}/history-export` | Owner/admin full records |
| `/api/ledgers/{id}/shipment-access` and `/{grant_id}` | Create/list/revoke |
| `GET /api/customer/shipments/{shipment_id}/events` or `/proof` | Scoped reads |
| `GET /api/security/events` | Administrator telemetry |

See the blueprint for methods and the dashboard for examples. Browser mutations require a session and `X-CSRF-Token` obtained at login or `/api/me`.

## Automated verification

```powershell
.venv\Scripts\python.exe tools/run_checks.py --frontend
.venv\Scripts\python.exe tools/test_external_integration.py
```

The first command loads local `.env`, refuses unsafe test targets, and runs all Python backend/launcher tests, frontend tests and production build. Tampering tests use only disposable test ledgers. Viewer polling now waits ten seconds, pauses while the tab is hidden, avoids overlapping requests and respects Retry-After; manual refresh remains available. No standalone lint/typecheck toolchain exists in the original project; syntax/build checks, helper/crypto tests, HTTP/PostgreSQL tests and browser smoke results are reported accurately.

The external acceptance command requires the existing AudioShare checkout, its built Node backend and actual LiveKit/private voice binaries. It starts owned independent processes on unused ports, creates a genuine session/lobby, forwards the server-generated identity through generic ChainGuard APIs, tests retries/conflicts, signs a checkpoint and runs offline verification. It cleans its test resources/processes. [Recorded evidence](docs/EXTERNAL_INTEGRATION_EVIDENCE.md) demonstrates a real controlled application's operational event, not a logistics-provider integration.

## Limitations

This is a centralized blockchain-inspired audit service, not tamper-proof storage. Hash consistency does not establish that submitted data is truthful or prove physical delivery. A privileged database attacker can rewrite blocks and registry metadata consistently; independently retained checkpoints detect changes to covered prefixes. Signing-key compromise or falsely trusting a replacement key undermines that evidence. Server-only checkpoint storage is not independent trust.

Use authorized infrastructure, least-privilege database credentials, real HTTPS, protected key/database backups and independent evidence retention. This MVP is not a universal API proxy, SIEM, WMS/TMS, payment system, distributed network or physical-delivery authenticity service.
