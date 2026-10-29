# Audit remediation plan

Baseline reviewed: `5a33b464`. Scope: the four priority changes in the user's October 29 audit. Preserve block serialization/hashing, PostgreSQL, snapshots, checkpoint trust boundaries and current UI architecture.

## Work and acceptance criteria

1. **CI:** Ubuntu GitHub Actions with PostgreSQL, normal application database role, pinned dependencies/actions, backend/launcher/frontend tests and build; optional local `.env` when explicit CI variables exist. Do not claim a hosted pass before a run actually completes.
2. **Expensive requests:** cheap source throttling before database lookup for costly ledger routes and all customer capability attempts; independent work concurrency limit; bounded database statement/lock timeouts; controlled concurrent-request responsiveness checks.
3. **Customer reads:** indexed shipment lookup/pagination; at most a configured bounded set of checkpoint comparisons, one full-chain check per cache refresh instead of one per checkpoint. A short-lived bounded verification cache must invalidate on ordinary SQL block mutations via metadata revision triggers, and expire periodically even when metadata is unchanged. Disclose checked-at time, cache age, evidence comparison scope and incomplete checkpoint coverage. Never equate cached server checks with independent proof. Existing tampering, prefix and privacy tests remain required.
4. **Managed writes:** new template ledgers require managed connector credentials; existing ledger/token compatibility is migrated explicitly. Reject legacy append/rotation where managed writes are required; keep internal authorized append used by authenticated connectors.
5. **Automatic integration:** application-side adapter observes real successful operations in an independently running existing application and forwards events automatically. Acceptance code triggers the source operation and polls ChainGuard; it must not translate and forward the response itself. No provider-native or logistics-feed claims.

## Validation and remaining bounds

Write targeted regressions before changing the affected behavior, then run affected tests and the full suite/build. Review security and stale-cache behavior independently. Record actual timings instead of complexity illustrations. Retain the conservative full-chain append verification; this audit fix does not silently trade tamper detection for write throughput. A 10,000-block write benchmark can therefore remain expensive and must be reported honestly.

No original application source/database edits, no production database tampering, no remote pushes until authorized, no added runtime infrastructure or unrelated frontend redesign. Hosted CI and actual Ubuntu service/reboot acceptance remain separate from Windows/local evidence.

## Completed local verification

- 104 backend tests, 14 tools/launcher tests, and 13 frontend tests passed; zero skips/failures. The tools suite includes six actual Node adapter regression cases through its adapter test bridge. Production frontend build passed.
- Source throttling and controlled concurrency checks verified that rejected work stops before lookup and `/api/me` remains responsive while the separate verification budget is saturated. A real slow PostgreSQL statement was canceled by its configured timeout.
- Direct SQL tampering invalidates the customer verification cache; expiry/revocation is never cached. Tests cover periodic expiry, lock-wait age, bounded checkpoint comparisons and projected history/genesis budget rejection without stranding a ledger.
- Managed template credential regressions reject legacy writes even with a matching stored token hash, and enforce managed source/expiry/disable policies. Compatible existing/custom generic credentials remain functional.
- Independent review found and then verified fixes for two cache/budget edge cases. Final bounded verdict: APPROVE, subject to the final suite; that suite subsequently passed.
- Actual 1,000/10,000-record measurements are recorded in `docs/PERFORMANCE_MEASUREMENTS.md`; they are direct local service calls with small samples, not production/concurrent capacity estimates. Writes still verify the whole history.
- Automatic AudioShare delivery passed in two observed real source runs. The latest repetition was PENDING because the unchanged source closed `POST /v1/session` during media initialization. `docs/AUTOMATIC_INTEGRATION.md` distinguishes successful delivery from source availability and documents bounded best-effort retry/loss behavior.

The fixes are staged on a separate `codex/security-audit-hardening` branch for review. Hosted CI status must be read from that exact branch revision's Actions result; no systemd/reboot, PostgreSQL service restart, certificate-renewal or backup/restore pass is inferred from it. The existing development application may be reloaded with these changes; the original ledger database/service remains untouched.
