# Viewer polling and browser reliability

Date: 29 October 2026 (Asia/Calcutta).

## Polling behavior

The administrative ledger viewer now waits **10 seconds after each completed request**, replacing its previous 1.5-second interval. Two continuously visible viewers therefore normally use about 12 ledger requests per minute in total, leaving room below the default 60-request source allowance for normal navigation and manual actions.

The shared polling scheduler also:

- Stops scheduled polling while the document is hidden and aborts its in-flight read.
- Performs one immediate refresh when the document becomes visible, without overlapping an unfinished request or replaying a backlog of hidden-tab timers.
- Preserves manual Refresh; repeated clicks while a request is active queue only one follow-up request.
- Honors HTTP 429 `Retry-After` across normal timers, manual Refresh and visibility changes.
- Retains a five-second request deadline and removes timers/listeners on component cleanup.

`apiRequest` exposes response status and parsed `retryAfterMs` on errors. Existing same-origin cookie sessions, CSRF behavior and customer capability isolation are preserved. Customer shipment polling is a separate existing flow and was not changed by this viewer fix.

## Automated checks

`npm --prefix frontend test`: **21 tests passed**, no failures or skips. The eight new cases cover seven scheduler/backoff behaviors and API Retry-After metadata. Fake clocks and document visibility events verify hidden-tab cancellation, stale-response suppression, one-in-flight scheduling, cleanup, periodic timing, timeout and backoff semantics.

`npm --prefix frontend run build`: **passed**, 37 modules transformed.

`git diff --check` on the changed frontend files: **passed**.

## Three-minute real HTTP check

A temporary loopback application server used only the disposable `chainguard_test` database, a synthetic ordinary account, a private managed Shipment Events ledger, an actual managed bearer integration, a clearly labeled test event, a signed checkpoint and a scoped customer grant. The test fixture was provisioned through authenticated APIs; it was not actual logistics activity.

Two instances of the **production polling helper** fetched the fixture ledger over authenticated HTTP for three real minutes. Their document objects explicitly modeled two continuously visible views. This was a controlled scheduler/network test, **not two real browser tabs**.

| Observation | Result |
|---|---:|
| Actual elapsed time | 180,011 ms |
| Viewer instances | 2 |
| Ledger requests | 36 |
| Requests in elapsed minute 0 / 1 / 2 | 12 / 12 / 12 |
| HTTP response statuses | 200 only |
| HTTP 429 responses | 0 |
| Polling errors | 0 |

The raw request-timing evidence is in the ignored `.local/browser-reliability/three-minute-polling.json` artifact. Its separate fixture log counts requests without recording cookies, Authorization headers, passwords or customer capability URLs. The controlled clients logged out after their run.

## Visual verification boundary

Desktop/native browser automation was stopped. The user subsequently restricted visual work to the **Codex in-app browser only**. No further desktop browser or screenshot controls are authorized for this task.

This subtask's `open_in_codex` request queued a tab in its hidden child chat, and its tool inventory did not expose an in-app browser control surface. The main task subsequently obtained a working Codex in-app browser and is handling the visual user-journey check separately. The scheduler/network results above must not be described as completed browser interaction coverage.

## Codex in-app browser journey

The main task subsequently completed the following **actual UI checks using only the Codex in-app browser**, with the same isolated temporary application server:

1. Registered a synthetic ordinary account through the UI and created a private Shipment Events template. The result offered managed integration setup and did not reveal a legacy write secret.
2. Created an append-only connector through the UI. Saved a policy of 30 requests per minute and a loopback CIDR allowlist through the UI.
3. Used an authenticated **API client** to append a clearly labeled fixture event, receiving HTTP 201. This ingestion step was not a browser form action or genuine logistics activity.
4. Returned to the UI and verified the two-block history, then created a signed checkpoint covering block 1.
5. Created a grant for shipment `IAB-TEST` through the owner UI and opened its customer link in a second in-app browser tab. The fragment capability disappeared from the URL; the internal operator field was absent, and the server/signature/independent-history distinctions remained visible.
6. Revoked the grant through the **owner UI**. A customer Refresh then displayed invalid access and cleared the old timeline and evidence.

In-app screenshots were retained as ignored local artifacts:

- `test-output/iab-policy-verification.png`
- `test-output/iab-customer-verification.png`
- `test-output/iab-revoked-access.png`

These observations cover that concrete journey, not every interaction or a physical browser/device matrix. The three-minute two-view scheduler test remains separate from this two-tab UI journey; the report does not claim the browser tabs themselves were observed for three continuous minutes.

## Cleanup

The main task closed both of its in-app fixture tabs and removed its exact synthetic account and ledger. The original fixture server was then stopped through its owned stop signal. Its loopback port was confirmed closed, and read-only PostgreSQL queries confirmed its exact synthetic account and ledger were absent from `chainguard_test`. Protected ignored signing material was retained to preserve its registered key binding.

No original database or existing application source was modified. No further desktop/native browser controls were used after the user established the in-app-only boundary.
