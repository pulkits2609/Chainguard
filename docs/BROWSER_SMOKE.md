# ChainGuard browser smoke verification

Test date: 29 October 2026 (Asia/Calcutta).

The production frontend build was exercised in the installed Microsoft Edge browser against a temporary loopback application server using **only `chainguard_test`**. The in-app browser and browser connector inventory were unavailable, so native browser controls were used. These were disposable automated-test fixtures, not genuine external logistics activity.

## Verified workflows

1. Registered a disposable ordinary account through the UI. Login completed and the overview displayed database-backed zero counts. The browser password-save offer was declined.
2. Created a Shipment Events ledger through the template selector. Its schema was fixed, its default visibility was private, and the ledger appeared in its owner's management view with one genesis block.
3. Created a bearer integration through the UI using the default explicit field mappings and append-only credential scope. The secret appeared once, alongside the generic webhook endpoint.
4. Selected that integration in API Security, changed its request limit from 60 to 120 per minute, and received the successful policy-save response.
5. Submitted a clearly labeled operational test fixture through the integration webhook using an HTTP client. The response committed block 1. The dashboard's Verify ledger action reported two checked blocks and the expected head hash.
6. Generated and downloaded a signed checkpoint through the UI. Downloaded the owner history and public-key JSON files as well.
7. Selected the exported test key using the native file picker. Browser WebCrypto successfully verified the real Python-generated Ed25519 checkpoint signature. The UI explicitly stated that this does not independently verify redacted shipment contents. Selecting this key was part of the test; downloading a server key does not establish independent trust in deployment.
8. Ran the standalone offline verifier against the downloaded checkpoint, supplied public key, expected ledger ID, and full-history export. It returned `valid: true`, `signature_valid: true`, `history_verified: true`, `verification_scope: full_record_history`, and zero unanchored suffix blocks.
9. Created a shipment-scoped customer grant through the owner UI and opened its generated access link. The access secret disappeared from the address bar. The customer page showed no administrative navigation and displayed only the allowed event fields; the internal operator identifier was absent.
10. Confirmed separate labels for server-checked shipment records, the server's signature check, the unverified independent signature, and full-record history that is not independently verified in a redacted view.
11. Submitted a second webhook fixture while the customer page remained open. Automatic polling displayed the second event without a manual refresh. The older checkpoint remained visible with the explicit warning that later events are not covered by its signature.
12. Revoked the exact disposable grant through the authorized **API**, rather than through the browser revoke button. On its next poll, the customer page showed "Invalid or expired shipment access" and removed the previously displayed timeline and verification evidence.

## Layout and validation scope

Desktop views were visually inspected. A restored Edge window at 125% zoom provided a narrow effective CSS viewport and exercised the responsive single-column customer layout; headings, actions, and explanatory text remained readable. Browser zoom was restored to 100%. This is a responsive smoke check, not testing on physical mobile devices or a complete device/browser matrix.

Frontend automated checks passed after the final edits:

- `npm --prefix frontend test`: **13 tests passed**, no failures or skips. New cases cover real Ed25519 signatures, altered evidence and key mismatch, mandatory-auth policy serialization, safe nested request examples, fragment capabilities, and customer cookie/CSRF/session isolation.
- `npm --prefix frontend run build`: **passed**, 36 modules transformed.
- `git diff --check -- frontend`: no whitespace errors.

The browser run did not create an administrator, exercise the administrator-only Security Events table, or manually execute every frontend workflow. Backend/HTTP tests cover separate authorization and security cases. The test fixtures here do not satisfy genuine external-application integration acceptance.

## Artifacts and cleanup

Downloaded checkpoint, full-history, and public-key JSON evidence was moved into the ignored `test-output/browser-smoke/` directory. The screenshots and accessibility observations were inspected inline through the computer-use tool; no screenshot-file export API was available.

The temporary browser window and application-server process were stopped. The exact disposable user, its ledger, and related resource rows were removed from `chainguard_test`, with database queries confirming the user and ledger were absent. Only the temporary server's loopback port was checked for closure. The original database and existing application data were not modified. Protected test signing material remains in ignored `.local/browser-check/` so its registered key binding is not silently broken.
