# Export capacity and operational retention

## Accepted history must remain exportable

New genesis/append operations cannot grow their ledger past **5 MiB of ASCII-serialized block records**. This is a hard ceiling in addition to any smaller configured read/history budget. It deliberately leaves room below both existing 8 MiB formats:

- A complete owner/admin JSON history response includes the blocks plus bounded metadata and verification fields.
- The existing compact UTF-8 snapshot envelope is Fernet encrypted and base64 encoded. The conservative 5 MiB block allowance leaves room for encryption/encoding expansion and the envelope.

No hashing, block or snapshot format changed. Exceeding the cap returns HTTP 413 before insertion; accepted records, head metadata and idempotent retries remain intact. Archive a full operational ledger and create the next bounded ledger using a new managed integration. Keep independent checkpoints/evidence for each ledger identity. Writes still verify the full current history; the byte cap is not an enterprise-throughput claim.

The default 32 MiB `LEDGER_MAX_HISTORY_BYTES` remains an inspection/recovery budget so previously accepted larger histories are not silently rewritten or discarded. It does **not** authorize new writes beyond 5 MiB. Reducing that read budget below existing data can make interactive operations unavailable; configure it before commissioning a ledger.

## Legacy histories already beyond HTTP capacity

Historical data is not truncated to fit a new limit. A valid old history larger than the HTTP response/snapshot format capacity returns a clear 413 for those full downloads, while integrity checks and checkpoint creation remain available within the configured read budget. An operator with existing local database credentials can recover verified full-history JSON without passing it through the HTTP size limit:

```powershell
.venv\Scripts\python.exe tools/export_history.py --ledger-id LEDGER_UUID --output .local/retained-history.json
```

This is an operator tool, not a public/customer endpoint. It uses only the explicitly selected ChainGuard database, does not initialize/migrate tables or write database records, refuses invalid histories, creates the output exclusively with owner-only file permissions on POSIX, and never overwrites retained evidence. Restrict Windows output-directory ACLs as with the existing key directory. The inspection budget and 100,000-block ceiling still apply; PostgreSQL backups are the separate recovery path for larger datasets. Compare the export with a separately retained checkpoint/public key using `tools/verify_checkpoint.py`.

Regression tests submit 150 individually valid ~60 KiB records (over 8 MiB total attempted input), verify rejection before the accepted cap, and successfully export/validate complete evidence. A second test constructs a pre-cap valid ~9 MiB history in the disposable database, demonstrates HTTP format rejection, recovers its unchanged records locally, and verifies the signed history offline. No real existing history is modified.

## Application retention policy

Populated template/managed operational ledgers cannot be hard-deleted through the application, including after archival and checkpoint creation. Administrators may change their state to read-only or archived, which stops writes and retains records, snapshot keys and stored checkpoints. These records have no automatic application purge period in this MVP. Hard-deletion attempts are rejected with HTTP 409 and a sanitized retention audit event. Archive/metadata changes and their audit record commit together.

Generic legacy/custom ledgers retain their original administrative deletion behavior. A template with only genesis may also be removed. This compatibility distinction is explicit; it is not a claim that every database record is undeletable. A privileged PostgreSQL operator can still alter/drop data outside the application. Independently retained signed evidence remains important.

The internal test-cleanup keyword is permitted only against `chainguard_test`; no HTTP body/query parameter can enable it, and it is rejected before connecting to the application database. This lets disposable test fixtures be removed without adding a production retention bypass.

Security telemetry has its existing configurable default 30-day retention. That setting applies to sanitized security events, not operational ledger records. No legal/compliance retention certification, WORM storage, outbox guarantee or new purge workflow is implied.
