# Reliability and single-script deployment follow-up

Scope: user's second audit after `6b9f0d36`. Exclude Pi/systemd installation and reboot work. Keep the architecture, canonical hashes, existing snapshot format, truthful proof labels and best-effort adapter limits. Keep the repository unlicensed (explicit user decision).

## Plan and acceptance

1. Limit newly accepted history conservatively to 5 MiB of ASCII-serialized blocks, below both the 8 MiB JSON response and encrypted snapshot ceilings. Keep a larger read-only inspection/recovery budget for legacy histories. Test valid attempted growth beyond 8 MiB, rejection without mutation, complete authorized history export/snapshot/standalone verification, and an oversized legacy recovery path.
2. Slow viewer polling to 10 seconds, stop background-tab work, preserve manual refresh, respect rate-limit backoff, and test two normal viewers over several minutes.
3. Prevent application hard-deletion of populated template/managed operational ledgers, including archived/checkpointed ledgers. Preserve generic legacy deletion and administrative archival. Test-only fixture cleanup is explicitly restricted to chainguard_test and inaccessible through HTTP.
4. Add one cross-platform deployment entry point that reuses existing dependency/build/configuration/initialization services. Preserve existing configuration and keys. Require installed prerequisites and actual supplied production TLS configuration; no automatic production certificate fiction or privileged machine changes.
5. Handle expected abandoned TLS handshakes quietly; retain visibility of unexpected TLS/protocol errors. Test HTTPS with an explicitly trusted private test CA, hostname verification, secure cookies and CSRF.
6. Demonstrate pg_dump/pg_restore recovery of exact disposable fixture tables with retained key/checkpoint evidence, documenting that this is a scoped recovery drill rather than full disaster recovery. Inspect key filesystem protection and repeat current UI/security demos where feasible.

No original ledger database, live PostgreSQL restart, production data reset, new durable outbox, scalable hash-engine rewrite, selective-disclosure scheme or mandatory new infrastructure. Missing public DNS/certificate and full-system restore infrastructure remain explicit operational limits.

## Implementation evidence

- Capacity and retention implemented: attempted valid growth beyond 8 MiB stops before the conservative 5 MiB accepted-history ceiling; authorized export, encrypted snapshots and independent checkpoint/history verification remain usable. A pre-existing oversized history is preserved and recoverable through the read-only operator exporter. Populated operational ledgers are archived instead of application hard-deleted; generic deletion remains compatible.
- Polling changed to 10 seconds with hidden-tab cancellation, no overlap, manual refresh and Retry-After backoff. Two production scheduler clients sharing one source completed a 180-second HTTP exercise: 36 ledger requests, all HTTP 200, zero HTTP 429. This is separate from the shorter actual two-tab visual journey.
- Actual Codex in-app browser journey verified template privacy, managed integration/policy editing, checkpoint creation, customer redaction/trust labels, and grant revocation clearing the customer view. Fixture tabs/accounts/ledger were cleaned. No desktop or external-browser control is used after the user's in-app-only instruction.
- The new deployment command completed its real dependency install/frontend production build, then its database/schema/key readiness check against chainguard. Existing configuration, administrator and persistent keys were preserved.
- Trusted private-CA HTTPS, secure cookies/CSRF and expected TLS disconnect handling passed focused checks. Existing persistent-key Windows ACL checks passed.
- Real disposable ledger table-data pg_dump/pg_restore passed with all 13 original blocks, snapshot, checkpoint and retained signing key verified. The restore revealed and fixed the trigger's empty-search-path compatibility issue.
- A separate whole-public-schema recovery drill is guarded for the ephemeral GitHub-hosted service only. Its four refusal tests pass locally; actual full-schema restoration must pass the hosted workflow before being described as verified.
- A separate source review found no actionable medium/high findings in the changed contracts. Final full-suite and hosted results are recorded below when complete.

## Deliberate limits

The repository remains unlicensed. Adapter delivery remains best-effort, append verification remains linear in history size, and no DHL-native integration or universal event-delivery guarantee is claimed. Public-domain TLS issuance/renewal, cluster/role recovery, loss of external private keys and real systemd/reboot validation are not demonstrated. Pi deployment is excluded. The deployment script requires installed Python/Node/PostgreSQL prerequisites and actual supplied production HTTPS configuration; it does not change live infrastructure.

## Final local verification

`tools/run_checks.py --frontend` completed successfully: **113 backend, 34 tool and 21 frontend tests**, no failures or skips, plus the production build. The initial full run exposed an OS-dependent TLS callback-count assumption; both client certificate rejection assertions were already working. The test now waits for rejected workers to finish and separately verifies unexpected-error reporting. Four consecutive targeted runs and the final complete suite passed; production exception handling was not broadened.

Fresh real external acceptance: **PASS**, with an actual independent AudioShare voice lobby/session, automatic initial delivery, idempotent retry, conflicting-payload rejection, invalid-credential rejection, HMAC retry, valid chain and offline full-history checkpoint verification. See `docs/RELIABILITY_EXTERNAL_INTEGRATION.md`. Previous media-initialization failures remain an external source dependency; this passing run does not imply guaranteed source availability.

The real dependency-install/build-only command, readiness check, and actual `deploy.py --skip-build --non-interactive` startup passed. The app remains available at loopback port 8000, opened inside Codex's in-app browser. No external/native browser is needed. A private `.env` credential scan of tracked candidates passed, and configuration/keys remain ignored.

Hosted full-schema recovery and Ubuntu runtime results are tracked by the new branch workflow; a local passing suite alone does not establish those results.

The first hosted run exposed a pre-existing HTTP test crossing from 02:40:59 to 02:41:00 between a policy change and its rate-limit assertion. The real fixed-window quota correctly reset. That test now fixes its clock only during the related real HTTP/database assertions; production limits are unchanged. All four targeted HTTP tests and all 34 tool tests passed after this test-only correction. The corrected commit requires a fresh hosted full-suite run.

## Hosted verification completed

[GitHub Actions run 40808291012](https://github.com/pulkits2609/Chainguard/actions/runs/40808291012) passed at code revision `ed68d816f341555990e819333dbfc5ec2594c379`:

- 113 backend, 34 tool and 21 frontend tests, plus production build.
- Entire disposable application public schema dumped, dropped and transactionally restored; restored accounts/sessions, policies, HMAC credentials/idempotency, grants, full history, encrypted snapshot and signed checkpoint all verified. External private keys remained unchanged.
- One-script initialization checked twice; Ubuntu application startup, authenticated/private access, and restart persistence passed; deployment shell syntax passed.

The initial recovery-step tooling attempt could not install PostgreSQL 18 clients from Ubuntu's available apt repositories and stopped before executing the drill. The final CI step reuses matching clients from its existing official PostgreSQL 18 container with the hosted runner's UID and temporary directory. This adds no Docker requirement to the actual deployment command. No destructive schema recovery was run on the laptop. The following commit records documentation only; the linked run establishes verification of all delivered application and workflow changes.
