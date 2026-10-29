# Operational verification and remaining deployment boundaries

Verified on 29 October 2026 using only disposable `chainguard_test` fixtures. No original database, live PostgreSQL service, or existing application data was reset. Pi/systemd installation and reboot testing are excluded from this work at the user's request; deployment has a separate single-script entry point documented in `DEPLOYMENT.md`.

## TLS and HTTPS

`server/test_tls_operations.py` exercises a real TLS listener with a disposable private test CA and a leaf certificate for localhost/127.0.0.1. The client explicitly trusts that CA, retains certificate verification and hostname checks, and verifies:

- An untrusted issuer is rejected.
- A wrong server hostname is rejected.
- A trusted HTTPS registration succeeds and returns an HTTP-only, Secure, `__Host-` session cookie.
- HSTS is present, private ledger content remains inaccessible anonymously, and authenticated mutations require CSRF.
- Abandoned TLS peers do not prevent another HTTPS request completing.

Only expected EOF, orderly TLS close, reset, broken pipe and timeout errors **during handshake setup** become a quiet `TLSClientDisconnected` condition. The worker still closes the connection and releases its capacity. Unexpected TLS/protocol/application exceptions still reach `handle_error`; certificate errors are not hidden to make tests pass. This removes the successful-test `ssl.SSLEOFError` traceback without broadly swallowing SSL errors.

This demonstrates a correctly trusted private-CA HTTPS configuration. It does **not** claim a public-domain production deployment or a certificate issued by a public CA. A real deployment still needs the operator's actual certificate/private-key paths and matching HTTPS origin; the deployment script must not fabricate trust or silently fall back to insecure network HTTP.

## Private filesystem material

The recovery drill creates temporary signing material and a PostgreSQL archive containing a ledger snapshot secret. It checks owner-only POSIX modes or inspects Windows ACLs before proceeding.

Read-only ACL checks of the existing `.local/chainguard/checkpoint.key` and `integration.key` both passed on this Windows account: no Everyone, Authenticated Users or built-in Users **ReadData** allowance was present. Authorized account, administrator and SYSTEM access was retained. This is a scoped ACL inspection, not a claim that no privileged administrator can access keys. No ACL was modified or privilege bypass attempted.

On POSIX, generated private material uses 0600 files and a 0700 temporary directory. The existing signing-key loader rejects group/world-accessible signing files. Keep `.env`, persistent signing/HMAC keys and backups restricted to the intended application account and authorized administrators.

## Actual PostgreSQL table-data recovery drill

Run explicitly from the prepared checkout:

```powershell
.venv\Scripts\python.exe tools/verify_backup_restore.py
```

The command refuses every target except the configured `chainguard_test`. PostgreSQL client tools `pg_dump` and `pg_restore` must be installed. Database credentials come from the local ignored `.env` and child process environment; passwords are never placed in subprocess arguments or output.

The completed drill:

1. Created one exact owned **generic** fixture ledger with 12 records plus genesis.
2. Retained its original full history, encrypted Fernet snapshot, signed Ed25519 checkpoint and protected signing file.
3. Used real `pg_dump --format=custom` to archive only that ledger's generated block and secret tables.
4. Truncated only those two exact owned fixture tables; no registry, account, checkpoint, other ledger or original database table was truncated.
5. Used real `pg_restore --data-only --single-transaction --exit-on-error` to restore their data.
6. Verified all 13 original blocks/hashes, the original encrypted snapshot, independent full-history checkpoint verification, and unchanged retained signing key.
7. Removed the exact fixture and its own key-registration metadata, and deleted its temporary sensitive archive/key directory.

**Result: PASS.** Sanitized machine evidence is saved in ignored `test-output/backup-restore-evidence.json`.

The first run exposed a real compatibility issue: PostgreSQL restore uses an empty security `search_path`, while the history revision trigger referenced an unqualified registry table. The migration now uses `public.ledger_registry`; a regression inserts a restored row with `SET LOCAL search_path = ''` and confirms revision invalidation still works.

### Scope limit

This is an actual **ledger block/secret table-data recovery** with registry/checkpoint metadata and signing key retained. It is not a full-database or whole-machine disaster-recovery exercise. Full recovery must preserve account/integration/grant/policy/checkpoint metadata, all ledger tables and external signing/HMAC key files together. A PostgreSQL dump alone cannot restore signing keys held outside the database.

For full recovery, an authorized operator should back up the entire ChainGuard database plus separately protected key files, restore them into a newly provisioned isolated PostgreSQL instance, restore the same key paths/permissions, run the one-script initialization/check, and verify independently retained checkpoints with the separately trusted public key. Do not restore over an existing/original database merely to demonstrate this procedure. A complete disaster-recovery test, public HTTPS certificate deployment and certificate renewal remain unverified until suitable isolated infrastructure and real certificate configuration are supplied.

### Whole-application public-schema recovery in disposable hosted CI

`tools/verify_ci_database_recovery.py --disposable-ci` is a separate, explicitly destructive **CI-only** drill. It is not a laptop/deployment command. Before any database connection or PostgreSQL tool invocation, it requires the explicit flag, `CI=true`, `GITHUB_ACTIONS=true`, a GitHub-hosted Linux runner, a numeric workflow run ID, the current checkout as `GITHUB_WORKSPACE`, and the ordinary `chainguard_ci` role targeting the loopback `chainguard_test` service on port 5432. Missing conditions, application/original database names, other roles, remote hosts and Windows/laptop contexts are rejected. These checks prevent accidental invocation; they are not a substitute for isolating the CI service or an authorization boundary against an operator who can edit the program itself.

The hosted workflow may invoke it after the full suite, once no other test-database clients are active. It creates a fixture account/session, managed shipment ledger, HMAC connector and non-default policy, actual fixture event, signed checkpoint, encrypted snapshot and shipment grant. It then archives the **entire public schema**, verifies that the archive includes its schema definition, revalidates the exact connection identity, drops only that disposable test database's public schema and restores it transactionally with `pg_restore`.

Freshly initialized services verify restored account login/session state, original block hashes, HMAC authentication and idempotency, policy settings, customer capability access, encrypted snapshot validation and full-history signed-checkpoint comparison. Signing and HMAC key files remain outside the database and are checked unchanged and owner-only. Exact fixture cleanup follows a successful restoration. If restoration fails, the job fails and its disposable service is discarded; no attempt is made to recover over a real database.

The four local guard tests pass without executing destructive database work. **Actual whole-schema restoration is verified only when the hosted recovery step for the submitted revision passes.** This expands the earlier table-only proof to all application public-schema state, but still does not demonstrate recovery of cluster roles, the PostgreSQL cluster itself, lost external private keys, machine reboot or a public certificate deployment.

## Targeted checks

- `python -B tools/run_checks.py --pattern test_tls_operations.py`: TLS classification, real trusted-CA HTTPS, secure cookies/CSRF and empty-search-path trigger regression.
- `python -B -m unittest discover -s tools -p test_backup_restore.py`: exact test-database guard, environment-only password passing and sanitized tool failures.
- `python -B tools/verify_backup_restore.py`: explicit real disposable PostgreSQL recovery drill; not implicitly destructive during ordinary unit discovery.
- `python -B -m unittest discover -s tools -p test_ci_database_recovery.py`: pure refusal guards; no local schema drop or restore.
