# ChainGuard implementation progress

Branch: `feature/chainguard`. Scope: the supplied ChainGuard v1 blueprint plus the user's final decisions.

## Safety and delivery boundaries

- Work only in the copied ChainGuard checkout. No remote pushes or live infrastructure changes.
- Never connect implementation or tests to the original `blockchain_ledger` database.
- Application database: `chainguard`; disposable tests: `chainguard_test`.
- Preserve historical block serialization, genesis, SHA-256 hashing, and snapshot formats.
- Preserve generic public ledger compatibility; new templates are private by default.
- Customer checkpoint signatures and server-checked redacted records have distinct trust guarantees.
- Genuine external integration remains PENDING until a separate running application generates and delivers an actual event.

## Phase ledger

| Phase | Scope | Status | Evidence / blocker |
|---|---|---|---|
| 0 | Audit and baseline | PASSED | 38 backend, 8 launcher, 8 frontend tests passed; 13 PostgreSQL tests skipped. Production frontend build passed. Credentials repaired by user; chainguard and chainguard_test connections verified. Original database untouched. |
| 1 | Templates and schema validation | PASSED | Four templates, timestamp/identifier validation, private defaults, visibility authorization. All 53 server tests and 8 launcher tests passed with real chainguard_test PostgreSQL. |
| 2 | REST and inbound webhooks | PENDING | Focused integration module. |
| 3 | API policy enforcement | PENDING | Persistent PostgreSQL configuration and limits. |
| 4 | Ledger integrity regression | PENDING | Preserve canonical schema v2 engine. |
| 5 | Signed checkpoints and offline CLI | PENDING | Existing cryptography dependency supports Ed25519. |
| 6 | Private snapshot authorization | PENDING | Preserve Fernet format v2 and old fixtures. |
| 7 | Shipment capabilities | PENDING | Redacted records and explicit proof limitations. |
| 8 | Security audit | PENDING | Sanitized dedicated PostgreSQL records. |
| 9 | React management and customer UI | PENDING | Reuse existing components/styles/session flow. |
| 10 | Deployment and documentation | PENDING | Preserve Windows/Ubuntu launch model. |
| 11 | End-to-end security testing | PENDING | Genuine external producer and database access must be verified. |

## Execution notes

The user authorized independent work when a feature is blocked. Implementation modules may be prepared in parallel; integrated phase acceptance and commits are recorded sequentially. A blocked acceptance check is never counted as passed. No feature is complete merely because a UI control or service skeleton exists.

Baseline: server test discovery ran 51 tests, with 38 passed and 13 database tests skipped; tools ran 8 passed; frontend ran 8 passed. Frontend production build was written to a temporary directory. Source was clean before implementation. The supplied blueprint has been retained in `docs/CHAINGUARD_BLUEPRINT.md` for traceability.

Phase 1 commit: server/ledger_templates.py, server/store.py, server/web_app.py, server/test_templates.py, tools/run_checks.py, .gitignore. GET /api/ledger-templates added; creation accepts template_id/visibility; visibility PATCH is owner/admin protected. Existing reads remain public for generic ledgers. Private access returns 404 to unauthorized callers. No historical payload changes. Backend: 53/53; launcher: 8/8. Snapshot authorization was implemented with visibility to avoid a temporary private-data exposure.
