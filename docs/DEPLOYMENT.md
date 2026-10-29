# One-command deployment

From the checkout root, run:

```powershell
python deploy.py
```

Windows users can double-click **Deploy ChainGuard.bat**. Linux users can run `bash deploy/start-chainguard.sh`. All three entry points invoke the same script; there is no separate menu or platform-specific application architecture.

## Prerequisites

Install Python 3.11+, Node.js 22.12+ with npm, and a running PostgreSQL service first. The script detects missing Python/Node prerequisites and database connection/authentication failures with actionable messages. It deliberately does not silently install privileged operating-system packages, create PostgreSQL superusers, replace another database, or configure public infrastructure. No Docker, Raspberry Pi, or systemd installation is required for this workflow.

The application database must be named **chainguard**. An authorized PostgreSQL operator can create a normal login role and grant it ownership of that database. Development tests additionally use `chainguard_test`; the deployment script does not run destructive test fixtures or create a test database in production.

If the application database is missing and the configured role is already authorized to create databases, explicitly run `python deploy.py --create-database`. That option creates only `chainguard`; it never resets an existing database or accesses the original `blockchain_ledger` database. Without that option, missing database creation is an actionable prerequisite error. The normal application role does not need database-creation rights once its database exists.

## What the command handles

1. Reuses/creates `.venv`, installs existing pinned Python dependencies, runs `npm ci`, and rebuilds the frontend using the established launcher code.
2. Preserves an existing `.env` exactly. If none exists, an interactive first-run wizard requests private PostgreSQL settings and local-development or existing-certificate HTTPS settings. Secrets are entered with hidden prompts and URL-encoded correctly.
3. Re-executes the prepared Python interpreter, validates the separate database target and deployment configuration, checks PostgreSQL access, and initializes repeatable tables and persistent protected keys.
4. Preserves any existing administrator without resetting its password. If none exists, prompts once for an administrator username and hidden password, or consumes explicit private `LEDGER_ADMIN_USERNAME` / `LEDGER_ADMIN_PASSWORD` bootstrap configuration. Newly prompted administrator passwords are not saved by the script.
5. Starts the existing Python server directly in the foreground. Ctrl+C stops it. No sample shipment records are inserted.

Normal repeated runs refresh dependencies/build and preserve data, schemas, administrator credentials, keys and configuration. This is a deployment/startup command, not a process supervisor or a certificate-renewal service.

## Useful automation options

```powershell
python deploy.py --build-only
python deploy.py --check --non-interactive
python deploy.py --skip-build --check --non-interactive
python deploy.py --non-interactive
```

`--build-only` prepares dependencies/assets without accessing configuration or PostgreSQL. `--check` validates configuration and initializes schema/keys but starts no server; if no administrator exists, it reports that a normal launch will prompt. `--non-interactive` prevents configuration/admin prompts and fails clearly when required startup inputs are missing. `--skip-build` explicitly uses the current prepared Python environment and existing frontend build; use it for CI/readiness checks after dependencies have already been installed. Process environment configuration is supported when no `.env` exists, and explicit process variables take precedence over `.env` values.

## HTTPS and private files

Production requires actual matching certificate/key files, an HTTPS public origin, `LEDGER_DEV_MODE=false`, and `LEDGER_SECURE_COOKIES=true`. The script validates and loads the pair before database initialization and never creates a fake public certificate or silently falls back to development HTTP. The configured certificate still needs to cover the public hostname and be trusted by clients; validation here is not proof of public DNS/certificate issuance or renewal.

Local HTTP development binds only to loopback with insecure cookies explicitly disabled for that local mode. On Linux, `.env` and the TLS private key must be owner-only (`chmod 600`); persistent signing-key initialization also enforces its existing owner-only rule. On Windows, protect the checkout/config/key directory using the application account's ACLs and authorized administrators. Keep database credentials in the ignored local `.env`, never Git or command-line arguments. Back up protected keys with PostgreSQL; the deployment script does not replace or regenerate established key material.

The existing **Start Ledger.bat** and Ubuntu startup scripts remain compatible. They retain their historical menu for users who prefer it; the new one-command entry point is the simpler default. The repository remains unlicensed, per the user's decision.
