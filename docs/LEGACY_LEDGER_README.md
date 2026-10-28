# Archived pre-ChainGuard documentation

Historical reference only. Use the root README for current security and deployment behavior.

# blockchainLedger

A small React website and Python API for public, SHA-256-linked ledgers. Anyone
can browse a ledger, open its blocks and download an encrypted snapshot. Sign in
to create a ledger and receive its private write token. One administrator manages
ledger metadata, availability and deletion.

Records live in a local PostgreSQL database. This is a centralized application,
not distributed consensus: hashes reveal inconsistent records and links, but do
not prove that submitted information is truthful or prevent a database owner from
rebuilding all history. Keep a trusted chain-head fingerprint outside the server
when you need to detect a complete rewrite.

## Accounts and permissions

| Action | Visitor | Ledger creator | Administrator |
| --- | --- | --- | --- |
| List and view ledgers, download and validate snapshots | Yes | Yes | Yes |
| Create a ledger | Sign in first | Yes | Yes |
| See integration details or rotate a ledger's write token | No | Own ledger only | Only if also its creator |
| Append a block through the API | Valid bearer token required | Valid bearer token required | Valid bearer token required |
| Edit name/description, change state or delete a ledger | No | No | Yes |
| Rewrite saved blocks or change an established schema | No | No | No |

Ordinary accounts use self-registration. Passwords are hashed; browser login uses
an HTTP-only session cookie. Browser account/ledger changes require a CSRF token.
Write tokens belong in an API client's secret configuration, not frontend source,
URLs or Git. The full write token is shown at creation/rotation; store it safely.
Rotating it invalidates the previous token.

Integration details are hidden from visitors in the UI and owner-only API.
Endpoint names are not a security boundary: append requests are authorized using
the bearer token even if someone discovers the route.

States are **active**, **read_only** and **archived**. Only an active ledger accepts
new blocks. Read-only and archived ledgers remain publicly inspectable. Deletion
removes that ledger's stored records and encryption key; it is not an undo action.

## Create a ledger and define its blocks

The home screen offers **View ledgers** or **Log in**. Visitors can choose any
listed ledger. After login, the account view lists ledgers created by that user;
the administrator's view lists all ledgers.

Choose a name, description and field list before creating a ledger. Each field
has a name, type and required flag. Supported types are `string`, `integer`,
`boolean`, `object` and `array`. For example:

```json
[
  { "name": "title", "type": "string", "required": true },
  { "name": "tags", "type": "array", "required": false },
  { "name": "quantity", "type": "integer", "required": true }
]
```

Creation generates the genesis block and fixes the schema. Later submissions
must satisfy it; fields and existing block contents cannot be edited afterward.
Objects and arrays accept the project's supported JSON values, rather than an
additional nested field designer. Fractional values must be represented as scaled
integers. The canonical JSON format intentionally rejects floating-point values,
unsafe-range integers, duplicate keys and excessive nesting.

## Local setup on Windows

Requirements:

- Python 3.11 or newer, with `python` available in the terminal.
- Node.js 22.12 or newer and npm for building the React frontend.
- A local PostgreSQL server and a dedicated application database/user.

Create the PostgreSQL role and database once using pgAdmin or PostgreSQL's tools.
Use a normal login role that owns the application database, not a PostgreSQL
superuser. Then double-click **Start Ledger.bat** or run:

```powershell
& ".\Start Ledger.bat"
```

The launcher creates/reuses `.venv`, installs Python requirements, runs `npm ci`
and rebuilds the React viewer **every time**, including after a Git pull. It then
asks which startup command to run:

1. Start the website/API using `.env`.
2. Initialize the single administrator; prompts for a username and hidden password.
3. Check the PostgreSQL connection.
4. Start with the four demo records; asks for the existing admin username.
5. Supply additional server flags, such as `--port 8443`.
0. Exit.

On first run without `.env`, a short wizard asks for local PostgreSQL credentials
and local-HTTP or certificate-based HTTPS settings. Passwords are hidden and the
database password is URL-encoded automatically. Alternatively, choose to copy
`.env.example`, edit it and rerun. Existing `.env` files are never overwritten.
Explicit process environment variables still take priority over `.env` values.

Choose **2** once to create the administrator, then **1** to start. The menu does
not save the admin password. Existing administrator passwords are never reset by
initialization. Usernames use 3–32 letters, numbers, underscores or hyphens;
passwords contain 12–128 characters. Stop the running website with Ctrl+C.

For automation, passing server flags bypasses the menu; configure `.env` first.
For example, `& ".\Start Ledger.bat" --check-db` runs only the requested command
after building. `--build-only` prepares dependencies/assets without starting the
server or requiring database configuration. Custom input is parsed as server
arguments, not evaluated as a shell command. Keep secrets in `.env`.
When changing the public host/port, update `--public-origin` or `.env` to match.

**Start Demo Ledger.bat** remains available for noninteractive demo startup when
`LEDGER_ADMIN_USERNAME` is configured. Normal startup adds no demo records.

## Local setup on Ubuntu

Install PostgreSQL and Python prerequisites if they are not already installed:

```bash
sudo apt update
sudo apt install postgresql postgresql-client python3 python3-venv python3-pip
```

Use Python 3.11+ and install Node.js 22.12+ with npm separately if the distribution
version is older. See the [official Node.js downloads](https://nodejs.org/en/download).

Create the application role/database once. `--pwprompt` keeps the password out of
the shell command; these commands do not replace an existing role/database:

```bash
sudo -u postgres createuser --pwprompt ledger_app
sudo -u postgres createdb --owner=ledger_app blockchain_ledger
```

After pulling the repository on Ubuntu or a 64-bit Raspberry Pi installation,
run this one command as your ordinary user:

```bash
bash deploy/start-ubuntu.sh
```

It builds everything and opens the same first-time configuration and startup
menu as Windows. Newly generated `.env` files have owner-only permissions on
Ubuntu. HTTPS mode asks for your public origin and existing certificate/key paths.
The launcher does not install system packages or replace PostgreSQL users/data.

Use that same command after each pull; an existing build is always refreshed.
`bash deploy/setup-ubuntu.sh` remains a build-only shortcut. For automation use
`bash deploy/start-ubuntu.sh --check-db` or other explicit server flags; the
systemd unit starts the prepared application directly without an interactive menu.

## HTTPS with your certificate

No certificate or private key is included in the repository. Supply your actual
certificate chain and matching private key later. Set these values in `.env`:

```ini
LEDGER_BIND=0.0.0.0
LEDGER_PORT=8443
LEDGER_ALLOWED_HOSTS=ledger.example.com
LEDGER_PUBLIC_ORIGIN=https://ledger.example.com:8443
LEDGER_TLS_CERT=/etc/blockchainLedger/fullchain.pem
LEDGER_TLS_KEY=/etc/blockchainLedger/private.key
LEDGER_DEV_MODE=false
LEDGER_SECURE_COOKIES=true
```

Replace the hostname and paths. The certificate must cover the hostname visitors
use, and their devices must trust its issuing CA. A private CA requires installing
its CA certificate on those devices; do not work around trust failures by disabling
certificate verification. Keep the private key readable only by the service
account and its administrator.

Start the same script. Python terminates TLS directly; the viewer and API share
the configured HTTPS origin. `LEDGER_PUBLIC_ORIGIN` must exactly match the browser's
scheme, hostname and port. `LEDGER_ALLOWED_HOSTS` is a comma-separated host list,
without schemes or ports. Use one canonical public origin for login.

The built-in server is intended for this small deployment. Do not place an HTTPS
reverse proxy in front of it without also configuring trusted origin and cookie
handling; arbitrary forwarded headers must not be trusted.

An optional `deploy/blockchain-ledger.service` example runs an already prepared
installation at `/opt/blockchainLedger` as an unprivileged `ledger` user. Create
that account, adjust the paths and give it read access to `.env` and the TLS key
before installing the unit. Use an unprivileged port such as 8443. The example
does not create accounts, databases, certificates or firewall/router rules.

```bash
sudo cp deploy/blockchain-ledger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now blockchain-ledger
sudo journalctl -u blockchain-ledger -f
```

## Append through the creator's API

The creator receives a per-ledger path and a bearer token when the ledger is
created. Use the displayed path, rather than the old shared `/api/blocks` route.
Send the ledger's fields directly in the JSON body. Required fields must be
present; optional fields may be omitted. Included fields must have the declared
type, and unknown fields are rejected. No extra wrapper is required.

```http
POST /api/ledgers/LEDGER_ID/blocks HTTP/1.1
Host: ledger.example.com:8443
Authorization: Bearer YOUR_PRIVATE_WRITE_TOKEN
Content-Type: application/json

{
  "title": "Inventory receipt",
  "tags": ["warehouse", "delivery"],
  "quantity": 180
}
```

Send it over the configured HTTPS origin. A new record returns HTTP 201 and the
server generates its internal record ID. For retry safety, optionally send an
`Idempotency-Key` header (1–128 printable ASCII characters without spaces).
The same key and data return the existing block with HTTP 200; different
data for that key produce a conflict. Without the header, each accepted request
creates a new record. Stored block metadata uses the generic `record_id` field;
application fields belong directly in the request body and are stored under `data`.
Envelope-style requests are not accepted unless those fields are explicitly part
of the ledger's own schema.
Invalid schema data, invalid tokens
and writes to a non-active ledger are rejected without appending.

The server owns indexes, timestamps, previous hashes and record hashes. The token
authorizes submission but does not establish that a submitted fact
is true. Requests are bounded; no endpoint edits saved block contents.

Browser routes include:

| Route | Purpose |
| --- | --- |
| `GET /api/me` | Current login and CSRF token |
| `POST /api/register`, `POST /api/login`, `POST /api/logout` | Account/session operations |
| `GET /api/ledgers` | Public ledger listing |
| `GET /api/my-ledgers` | Own ledgers, or all for the administrator |
| `POST /api/ledgers` | Create a ledger with its immutable field schema |
| `GET /api/ledgers/ID` | Public block history and verification |
| `GET /api/ledgers/ID/snapshot` | Encrypted chain download; `?block=N` selects one block |
| `POST /api/ledgers/ID/validate-snapshot` | Compare an encrypted file with that ledger |
| `GET /api/ledgers/ID/credentials` | Creator-only integration details |
| `POST /api/ledgers/ID/rotate-token` | Creator-only token replacement |
| `PATCH /api/ledgers/ID`, `DELETE /api/ledgers/ID` | Administrator metadata/state changes or deletion |

External applications integrate through their ledger-specific HTTPS endpoint and
bearer token. No other application's source code or runtime is required.

New ledgers use block schema version 2 with `record_id` metadata. This is a clean
format change: create fresh ledgers after updating. Existing historical hashes and
snapshots are not silently rewritten or migrated.

## Snapshots and visible verification

Every ledger has its own randomly generated encryption key. All use the same
vetted authenticated encryption algorithm, Fernet; independent keys provide
isolation, rather than inventing a different encryption algorithm for each ledger.
Keys stay server-side in PostgreSQL and are never returned to browsers or API
clients. A snapshot from one ledger cannot be authenticated using another's key.

Visitors can download the whole chain or one block. Uploading a `.ledger` file
checks its authenticity, the saved hashes/links and its records against the
current server. It does not import or overwrite anything.

The chain's verification display distinguishes:

- **Green — verified:** this block was in the saved file and matches.
- **Red — changed/missing:** an authenticated saved record differs from current
  history, is missing, or fails a hash/link check. The report identifies the index.
- **Red, labelled not in snapshot:** this block was not included in the file,
  such as a newer append. This is not evidence of tampering.

A file that fails encryption authentication cannot reliably identify a changed
block. It is reported as an untrusted/changed file without inventing an index.
Comparison is refreshed or cleared when the ledger changes so old green results
are not shown as a fresh verdict. Files are limited to 8 MiB.

## PostgreSQL storage and backups

`LEDGER_DATABASE_URL` selects a local PostgreSQL database. Shared tables hold
accounts, sessions and the ledger registry. Each ledger gets separate block and
secret-key tables, using server-generated identifiers rather than user-provided
SQL names. Creating/deleting a ledger manages those tables transactionally.
Appends are serialized for that ledger to avoid two blocks sharing the same index.

The field schema is fixed in genesis. Verification checks block hashes, links,
indexes, schema and duplicate record identifiers. Storage errors and inconsistent
history are reported instead of silently resetting the ledger.

Back up the complete database, including its encryption-key tables, using
PostgreSQL backup tools. A source clone, frontend build or encrypted snapshot is
not a substitute for that backup. Deleting keys or restoring unrelated keys makes
older downloaded snapshots unreadable. Store database backups outside Git and
limit who can read them.

The old `server/data/chain.jsonl` and `snapshot.key` files are not automatically
deleted or silently treated as PostgreSQL records. Keep them safe if migrating
an older installation; do not assume old snapshots belong to a new ledger.

Verify a PostgreSQL ledger without starting the website:

```bash
.venv/bin/python server/app.py --env-file .env --verify --ledger-id LEDGER_ID
```

The retained offline audit of an old JSON Lines ledger uses `--verify --data-dir
PATH`. Do not use it as the new application's storage configuration.

## Development and checks

The project uses pinned frontend dependencies and Python requirements. Recreate
the virtual environment on each machine rather than copying it between Windows
and Ubuntu.

For frontend development, keep the Python server running locally, then:

```powershell
npm.cmd --prefix frontend run dev
```

Vite serves `http://127.0.0.1:5173` and proxies `/api` to port 8000. Keep the local
settings from `.env.example` (`LEDGER_DEV_MODE=true`,
`LEDGER_SECURE_COOKIES=false`, loopback bind and an HTTP public origin). Development
mode explicitly permits the loopback Vite origins on port 5173. The deployed
build uses one HTTPS origin and does not permit those development exceptions.

```powershell
.venv\Scripts\python.exe -m unittest discover -s server -v
npm.cmd --prefix frontend run build
```

PostgreSQL integration tests require a separate disposable database selected by
`LEDGER_TEST_DATABASE_URL`; do not point it at a real deployment database. A
skipped database test is not evidence that PostgreSQL behavior passed.

Important source areas:

```text
frontend/src/          Public viewer, accounts, ledger creation and admin UI
server/app.py          Authenticated PostgreSQL deployment entry point
server/legacy_http.py  Old JSONL HTTP fixtures used only by regression tests
server/web_app.py      HTTP/API routing, authorization and HTTPS configuration
server/accounts.py    Password hashes, administrator bootstrap and browser sessions
server/settings.py    Literal environment-file loading and origin configuration
server/store.py        PostgreSQL ledger registry, per-ledger tables and append rules
server/field_schema.py Immutable field definitions and submitted-data validation
server/ledger.py       Canonical JSON, SHA-256 block/hash/verification helpers
server/multi_snapshots.py  Per-ledger encrypted exports and visual comparison reports
deploy/               Ubuntu preparation/start scripts and optional systemd unit
```

`.gitignore` excludes dependencies, builds, environment files, certificates/keys,
local database dumps, legacy local data, downloaded snapshots and test output.
Keep the tracked `.env.example` free of real credentials.
