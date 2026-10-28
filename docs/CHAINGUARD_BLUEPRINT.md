# CHAINGUARD
## Blockchain-Based Supply Chain Integrity & Security Audit System

**Version:** 1.0 — Final Implementation Blueprint  
**Target:** DHL Cybersecurity / Software Engineering Internship  
**Development approach:** Extend the existing codebase using Codex  
**Development target:** 2 days  
**Repository:** https://github.com/pulkits2609/Blockchain-Ledger  
**Project type:** Standalone, integration-first cybersecurity middleware  
**Scope:** Frozen — do not implement features outside this document.

---

# 1. PROJECT OVERVIEW

## 1.1 Problem Statement

Modern logistics organizations operate multiple interconnected software systems responsible for shipment tracking, warehouse operations, transportation management, inventory, and delivery confirmation.

These systems frequently exchange operational events through APIs and webhooks.

This creates cybersecurity challenges:

1. Shipment records may be modified without authorization.
2. Historical records may be manipulated directly in databases.
3. External API integrations may be abused through excessive requests, compromised credentials, or unauthorized endpoint access.
4. Organizations may struggle to independently verify that historical records remain unchanged.
5. Customers may receive tracking information without a cryptographic method of checking its recorded integrity.
6. Different systems may maintain separate audit histories, making investigations difficult.

ChainGuard addresses these problems through a centralized, cryptographically verifiable audit platform.

## 1.2 Proposed Solution

ChainGuard is a standalone security middleware service that receives events from existing applications through authenticated REST APIs and webhooks.

It records accepted events in append-only, SHA-256 hash-linked ledgers backed by PostgreSQL.

It also provides:

- Configurable API security policies.
- Authentication and permission enforcement.
- Schema validation and replay prevention.
- Historical integrity verification.
- Independent digital-signature-based checkpoints.
- Encrypted ledger snapshots.
- Security audit logging.
- An administrative dashboard.
- A limited customer-facing shipment verification interface.

ChainGuard does not replace an existing logistics system. It operates alongside that system.

## 1.3 Core Principle

The project has three responsibilities:

**SECURE → RECORD → VERIFY**

SECURE: Protect incoming integration requests.

RECORD: Preserve accepted business events in a tamper-evident structure.

VERIFY: Allow authorized parties to verify the recorded history against trusted evidence.

## 1.4 Important Technical Limitations

ChainGuard is blockchain-inspired, not a decentralized blockchain.

It does not use distributed consensus, mining, cryptocurrencies, or smart contracts.

Hash linking detects historical inconsistencies but cannot guarantee that the original submitted information was truthful.

An authorized malicious sender can submit false events.

A database administrator may be able to reconstruct an internally consistent chain. Independent trusted checkpoints are therefore required for stronger tamper detection.

Do not describe the system as tamper-proof or claim that a signed ledger proves the real-world authenticity of a physical delivery.

---

# 2. EXISTING REPOSITORY — REUSE REQUIREMENTS

Before implementation, Codex must inspect the complete repository.

The existing application already includes:

- Python HTTP backend.
- PostgreSQL database integration.
- React frontend.
- Multiple independently managed ledgers.
- Configurable fixed JSON schemas.
- SHA-256 block hashing.
- Previous-hash linkage.
- Ledger integrity verification.
- Account registration and login.
- Administrator authentication.
- Browser session management.
- Per-ledger bearer write tokens.
- Credential rotation.
- Idempotency-key handling.
- Encrypted ledger snapshots.
- Snapshot validation.
- Ledger lifecycle management.
- Windows and Ubuntu startup scripts.
- Automated backend and frontend tests.

## 2.1 Existing Files to Inspect

Backend:

- `server/ledger.py`
- `server/store.py`
- `server/web_app.py`
- `server/accounts.py`
- `server/field_schema.py`
- `server/multi_snapshots.py`
- `server/settings.py`
- `server/requirements.txt`

Frontend:

- `frontend/src/App.jsx`
- `frontend/src/api.js`
- `frontend/src/components/`
- `frontend/src/styles.css`

Deployment:

- `deploy/`
- `.env.example`
- `README.md`
- `Start Ledger.bat`

Also inspect all existing test files.

## 2.2 Preservation Rules

Do not rewrite the application from scratch.

Do not replace the existing Python HTTP framework with FastAPI unless an unavoidable, documented compatibility blocker exists.

Do not replace PostgreSQL.

Do not introduce Redis, Kafka, MongoDB, or other infrastructure unless essential for the frozen scope.

Do not change the existing block hashing algorithm without an explicit versioned migration plan.

Do not silently modify historical blocks or genesis records.

Do not break the existing snapshot format.

Do not remove existing APIs unless absolutely necessary.

Reuse the existing UI components and styling.

Prefer adding focused modules rather than significantly increasing existing file complexity.

All existing tests must continue passing.

**Implementation rule:** For every new feature, first determine whether an existing service already provides part of the required functionality.

---

# 3. FINAL SYSTEM ARCHITECTURE

## 3.1 External Systems

ChainGuard accepts genuine events from applications such as:

- Warehouse Management Systems (WMS).
- Transportation Management Systems (TMS).
- Shipment tracking services.
- Inventory management applications.
- Delivery management applications.
- Enterprise backend services.
- Existing applications owned or controlled by the developer.

External integrations use HTTPS REST endpoints or configurable webhooks.

No external system simulator is required.

## 3.2 Backend Services

The backend consists of these logical services:

1. Account Service.
2. Ledger Management Service.
3. Integration Credential Service.
4. API Security Policy Service.
5. REST/Webhook Ingestion Service.
6. Cryptographic Ledger Service.
7. Integrity Verification Service.
8. Snapshot Service.
9. Checkpoint Signing Service.
10. Security Audit Service.
11. Shipment Verification Access Service.

These are logical modules within one deployable Python backend, not separate microservices.

## 3.3 Storage

Use the existing PostgreSQL database.

Continue using the existing ledger tables and account tables.

Add only the database tables needed for:

- Integration credentials and policy settings.
- Webhook connector configurations.
- Security audit events.
- Signed checkpoint metadata.
- Shipment verification access grants.

Do not create redundant copies of blockchain records.

## 3.4 Frontend

Reuse the React frontend.

Provide:

- Ledger management.
- Integration configuration.
- API security policy editor.
- Ledger history and block inspection.
- Integrity verification interface.
- Snapshot management.
- Checkpoint management.
- Security event viewer.
- Customer shipment verification page.

## 3.5 Deployment

Use one application server and one PostgreSQL database.

Keep the existing Windows and Ubuntu deployment mechanisms.

No distributed services or Kubernetes.

---

# 4. IMPLEMENTATION PHASES

Every phase below is a mandatory checkpoint.

Do not move to the next phase until its acceptance tests pass.

---

# PHASE 0 — REPOSITORY AUDIT AND BASELINE

## Objective

Understand and preserve the existing codebase before adding functionality.

## Tasks

1. Clone or open the existing repository.
2. Inspect backend modules, frontend components, and database schema.
3. Identify current authentication and authorization boundaries.
4. Identify how ledger creation and block insertion work.
5. Identify how the application generates and verifies hashes.
6. Identify current token handling and idempotency behavior.
7. Identify existing snapshot encryption and validation mechanisms.
8. Inspect existing security headers and request validation.
9. Identify database migration requirements.
10. Record the current test-suite results.

Create a development branch named:

`feature/chainguard`

Do not modify historical ledger data during initial setup.

## Tests

- Application starts successfully.
- PostgreSQL connection succeeds.
- Existing login works.
- An authenticated account can create a ledger.
- Valid JSON events can be appended.
- Ledger verification succeeds.
- Invalid ledger tokens are rejected.
- Encrypted snapshot export succeeds.
- Snapshot validation succeeds.
- Existing test suite passes.

## Acceptance Criteria

All baseline tests pass, or pre-existing failures are documented separately.

The current application is usable before modifications begin.

---

# PHASE 1 — LOGISTICS LEDGER CONFIGURATION

## Objective

Adapt the generic ledger system to enterprise logistics workflows without changing the underlying hashing engine.

## Features

### 1.1 Ledger Templates

Introduce predefined ledger schema templates.

Templates:

**Shipment Events Ledger**

Fields:

- `shipment_id`: string, required.
- `event_type`: string, required.
- `event_time`: string, required.
- `location`: string, optional.
- `source_system`: string, required.
- `operator_id`: string, optional.
- `reference_id`: string, optional.

**Warehouse Inventory Ledger**

Fields:

- `warehouse_id`: string, required.
- `item_id`: string, required.
- `event_type`: string, required.
- `quantity`: integer, required.
- `event_time`: string, required.
- `source_system`: string, required.

**Chain of Custody Ledger**

Fields:

- `shipment_id`: string, required.
- `custody_event`: string, required.
- `from_party`: string, optional.
- `to_party`: string, optional.
- `event_time`: string, required.
- `source_system`: string, required.

**Generic Security Events Ledger**

Fields:

- `event_type`: string, required.
- `source_system`: string, required.
- `resource_id`: string, optional.
- `event_time`: string, required.
- `details`: object, optional.

Existing custom ledger creation must remain supported.

Templates must create ordinary ledgers using the existing field-schema mechanism.

### 1.2 Schema Validation

Preserve existing validation.

Reject:

- Missing required fields.
- Incorrect field types.
- Unknown fields.
- Invalid JSON.
- Duplicate JSON keys.
- Unsupported numeric values.
- Excessive nesting.
- Oversized request bodies.

For the new template ingestion paths, validate timestamp formatting and event identifiers where applicable.

Do not alter the canonical block serialization format.

## APIs

Reuse:

`POST /api/ledgers`

`GET /api/ledgers`

`GET /api/my-ledgers`

`GET /api/ledgers/{ledger_id}`

Add only if useful:

`GET /api/ledger-templates`

Returns the predefined template names and their field definitions.

## Tests

1. Create each predefined ledger template.
2. Append a valid shipment event.
3. Append a valid warehouse event.
4. Append a valid custody event.
5. Reject an event missing `shipment_id`.
6. Reject incorrect types.
7. Reject unknown fields.
8. Confirm original generic ledgers still work.
9. Confirm historical hashes remain unchanged.

## Checkpoint

Users can create logistics-specific ledgers and record actual application events without modifying the ledger engine.

---

# PHASE 2 — REAL SYSTEM INTEGRATION

## Objective

Allow existing applications to send events directly to ChainGuard.

## 2.1 Existing REST Integration

Preserve the current ledger write endpoint:

`POST /api/ledgers/{ledger_id}/blocks`

Request headers:

`Authorization: Bearer <WRITE_TOKEN>`

`Content-Type: application/json`

`Idempotency-Key: <EVENT_ID>`

Example body:

```json
{
  "shipment_id": "SHP-12891",
  "event_type": "WAREHOUSE_DISPATCH",
  "event_time": "2026-10-28T10:30:00Z",
  "location": "INDORE-HUB",
  "source_system": "WAREHOUSE_WMS",
  "operator_id": "EMP-104"
}
```

Use the existing direct-body format, not a new wrapper.

Expected response:

HTTP `201 Created` for a newly recorded event.

Response includes:

- Block index.
- Record ID.
- Timestamp.
- Block hash.
- Previous hash.
- Duplicate flag.

For an idempotent retry with matching data, return the existing block without appending again.

For the same idempotency key with conflicting data, return a conflict response.

## 2.2 Webhook Integration

Implement a configurable inbound webhook adapter.

Endpoint:

`POST /api/integrations/{integration_id}/webhook`

Each integration configuration contains:

- Unique integration ID.
- Target ledger ID.
- Integration name.
- Enabled/disabled state.
- Authentication method.
- Authentication secret reference.
- Allowed payload size.
- JSON field mappings.
- Event identifier mapping.
- Timestamp field mapping.
- Security policy reference.

For the two-day MVP, support a simple explicit field mapping between incoming JSON and the predefined ledger schema.

Do not implement an unrestricted expression language or arbitrary script execution for mapping.

## 2.3 Webhook Authentication

Support two modes:

**Mode A: Bearer token**

An external application sends a secret token in the Authorization header.

**Mode B: HMAC-SHA256**

A webhook producer signs the raw request body using a shared secret.

A configured signature header carries the authentication value.

Compare signatures using constant-time comparison.

The exact signature encoding and canonical signed bytes must be specified per integration. The default ChainGuard HMAC profile signs the raw body bytes.

Never trust client-supplied usernames or application names as authentication evidence.

Do not assume an external provider supports HMAC if its actual documented API does not.

Do not invent DHL authentication headers or signature formats.

## 2.4 Webhook Replay Protection

Support an integration-specific event ID.

Use the existing idempotency mechanism when possible.

For clients using ChainGuard's own signed webhook profile, include a signed timestamp with a bounded acceptance window.

Reject expired signed requests.

Prevent repeated processing of identical signed events.

Persist the deduplication decision so server restarts do not permit duplicate records.

Generic third-party webhook authentication must follow the actual provider's documented protocol.

## 2.5 Failure Handling

Distinguish:

- Authentication failure.
- Authorization failure.
- Invalid payload.
- Schema mismatch.
- Duplicate request.
- Rate-limit violation.
- Temporary database failure.

Do not acknowledge an event as successfully accepted before it has been durably committed.

Return suitable HTTP status codes and non-sensitive error messages.

Do not implement an outbound retry queue or message broker in this version.

## New Administrative APIs

`POST /api/integrations`

Create an integration.

`GET /api/integrations`

List authorized integrations.

`GET /api/integrations/{integration_id}`

Retrieve configuration metadata without returning secrets.

`PATCH /api/integrations/{integration_id}`

Modify configuration.

`DELETE /api/integrations/{integration_id}`

Disable and remove integration configuration safely.

`POST /api/integrations/{integration_id}/rotate-secret`

Rotate the integration authentication secret.

These administration routes require browser-session authentication, CSRF protection where applicable, and ownership/administrator authorization.

## Tests

1. Submit a real event from an existing application.
2. Confirm it appears in the ledger.
3. Send a valid webhook.
4. Reject an invalid bearer token.
5. Reject an invalid HMAC signature.
6. Reject an expired signed request.
7. Retry an event with the same idempotency key.
8. Confirm no duplicate block is created.
9. Retry with conflicting payload.
10. Confirm an appropriate conflict response.
11. Reject malformed JSON.
12. Disable an integration and verify requests are rejected.
13. Rotate an integration secret.
14. Confirm old credentials stop working.
15. Restart the application and confirm deduplication remains effective.

## Checkpoint

A genuine external application can send events through REST or webhooks, and ChainGuard stores accepted events without requiring a logistics simulator.

---

# PHASE 3 — API SECURITY GATEWAY AND POLICY MANAGEMENT

## Objective

Allow authorized administrators to configure security restrictions on the APIs exposed by ChainGuard.

This gateway protects ChainGuard's integration APIs.

It does not operate as a universal reverse proxy for unrelated systems.

## 3.1 Policy Model

Each integration can have a configurable policy.

Fields:

- `enabled`
- `requests_per_minute`
- `allowed_operations`
- `allowed_source_cidrs`
- `max_body_bytes`
- `credential_expires_at`
- `require_authentication`
- `updated_at`

All policies must have secure defaults.

The system administrator can define non-overridable global safety limits.

## 3.2 Rate Limiting

Implement configurable requests-per-minute limits.

Apply limits by authenticated integration identity.

Unauthenticated requests should also have a conservative source-IP limit to reduce credential guessing.

Use existing server-side capabilities or a PostgreSQL-backed limiter if persistence/shared enforcement is needed.

An in-memory limiter is acceptable only for a documented, single-process deployment.

Avoid adding Redis solely for rate limiting.

Return HTTP `429 Too Many Requests`.

Include `Retry-After` when appropriate.

Apply rate limiting before expensive ledger/database operations where practical.

## 3.3 Endpoint Allowlisting

Support these operation permissions:

- `append`
- `read`
- `verify`
- `snapshot_export`

Do not let an integration credential create ledgers, administer accounts, rotate its own credentials, or change security policies.

Default integration permissions:

`append` only.

Existing write tokens must not automatically become read tokens.

A policy permitting an operation is not sufficient by itself. The caller must also have the corresponding authenticated permission.

## 3.4 Source IP Restrictions

Administrators can define allowed IPv4/IPv6 addresses or CIDR ranges.

Reject incoming requests whose verified source IP falls outside the configured list.

Use the actual peer IP address unless the application is behind an explicitly configured, trusted reverse proxy.

Never trust arbitrary `X-Forwarded-For` headers from clients.

If no IP allowlist is set, allow requests subject to other authentication and security controls.

## 3.5 Payload Limits

Set a global request body maximum.

Allow administrators to choose a smaller limit per integration.

Never allow an integration policy to exceed the server-wide maximum.

Reject oversized requests before parsing their bodies.

## 3.6 Credentials

Support:

- Credential generation.
- Secure secret storage.
- Credential rotation.
- Credential revocation.
- Optional expiration.

Store hashed bearer secrets, not plaintext bearer tokens.

Show newly created secrets only once.

Store HMAC verification secrets using an appropriate protected configuration or encrypted storage design.

Never expose secrets in API responses, frontend logs, or server audit logs.

## 3.7 Enforcement Order

For every integration request:

1. Validate request path and HTTP method.
2. Apply basic transport and request-size limits.
3. Apply unauthenticated request throttling.
4. Resolve the integration configuration.
5. Check whether the integration is enabled.
6. Authenticate the request.
7. Check credential expiration/revocation.
8. Enforce operation permissions.
9. Enforce source-IP restrictions.
10. Enforce integration rate limits.
11. Parse and validate the payload.
12. Check replay/idempotency constraints.
13. Perform the authorized operation.
14. Record the security outcome.

Avoid logging sensitive request bodies or credentials.

## APIs

`GET /api/integrations/{integration_id}/policy`

`PATCH /api/integrations/{integration_id}/policy`

Administrative only.

The policy response returns non-sensitive settings.

Do not create separate public routes for every policy operation.

## Tests

**Rate Limiting**

- Set a low limit.
- Submit requests until the limit is reached.
- Confirm subsequent requests receive HTTP 429.
- Confirm a different authorized integration uses its own limit.
- Verify behavior after the window resets.

**Authorization**

- Configure append-only access.
- Confirm append works.
- Confirm read fails.
- Confirm verification fails.
- Confirm administrative policy modification fails.

**IP Restrictions**

- Configure an allowlist containing the test client address.
- Confirm requests are accepted.
- Configure a nonmatching CIDR.
- Confirm requests are rejected.

**Credential Expiration**

- Create an expiring credential.
- Verify access before expiration.
- Verify rejection after expiration.
- Rotate credentials and verify old credentials fail.

**Request Validation**

- Reject oversized bodies.
- Reject invalid JSON.
- Reject incorrect content types.
- Reject malformed authentication headers.

**Policy Isolation**

- Create two integrations.
- Modify the first integration's policy.
- Verify the second integration is unaffected.

## Checkpoint

Administrators can configure API policies through the dashboard, and the backend enforces them without code changes or redeployment.

---

# PHASE 4 — LEDGER ENGINE AND INTEGRITY VERIFICATION

## Objective

Preserve and validate the existing cryptographic recording engine.

No replacement hashing system should be written.

## 4.1 Block Structure

Continue using the existing canonical block structure.

Each block contains:

- Schema version.
- Block index.
- Record ID.
- Server timestamp.
- Submitted JSON data.
- Previous block hash.
- Current block hash.

## 4.2 Hash Calculation

Continue using SHA-256.

The block hash is computed from the canonical serialization of the block excluding its own hash value.

All existing canonical JSON restrictions must remain in force.

## 4.3 Hash Chaining

For every new block:

`previous_hash = previous_block.hash`

The block index increments sequentially.

Append operations must remain transactional and concurrency-safe.

## 4.4 Verification

Verification checks:

1. Block structure.
2. Hash correctness.
3. Previous-hash linkage.
4. Block index continuity.
5. Record identifier uniqueness.
6. Genesis consistency.
7. Stored chain-head consistency.

Return structured verification results.

Example:

```json
{
  "valid": false,
  "checked_blocks": 18,
  "head_hash": "CURRENT_HEAD_HASH",
  "errors": [
    {
      "index": 7,
      "message": "Stored hash does not match recalculated hash"
    }
  ]
}
```

Do not fabricate clean verification results when records cannot be read.

## Existing APIs

`POST /api/ledgers/{ledger_id}/blocks`

`GET /api/ledgers/{ledger_id}`

`POST /api/ledgers/{ledger_id}/verify`

`GET /api/ledgers/{ledger_id}/checkpoint`

The existing checkpoint endpoint currently exposes a chain-head reference. It must remain distinct from the new signed checkpoint functionality.

## Tests

1. Append 100 valid events.
2. Confirm index continuity.
3. Confirm correct hash linkage.
4. Verify the entire chain.
5. Submit concurrent append requests.
6. Confirm no duplicate indexes or broken chains.
7. Modify a block directly in a disposable test database.
8. Confirm verification fails.
9. Remove a block in a disposable test database.
10. Confirm verification identifies the inconsistency.
11. Verify that ordinary application APIs cannot edit historical blocks.
12. Confirm existing historical hashes remain compatible.

## Checkpoint

The hash-chain engine retains its existing behavior, and actual historical modifications can be detected.

---

# PHASE 5 — SIGNED CHECKPOINTS AND INDEPENDENT VERIFICATION

## Objective

Provide independently verifiable evidence of a ledger's previously recorded state.

This is one of ChainGuard's most important cybersecurity capabilities.

## 5.1 Signed Checkpoint

Generate an Ed25519 key pair using a maintained cryptographic library.

The private signing key must remain outside the public frontend and database.

Do not hardcode keys.

Do not include private keys in Git.

Assign a key identifier to each public verification key.

A signed checkpoint contains:

- Checkpoint format version.
- Ledger ID.
- Block index.
- Chain-head hash.
- Creation timestamp.
- Key ID.
- Digital signature.

The signature is calculated over a defined canonical representation of the checkpoint fields, excluding the signature itself.

## 5.2 Independent Trust

A checkpoint must be downloadable as a JSON file.

The public verification key must be exportable.

Provide a standalone verification CLI that can run without connecting to the ChainGuard server.

The verifier must accept a trusted public key supplied independently.

It must not automatically trust a new verification key merely because the server returned it.

The verifier checks:

- Checkpoint structure.
- Checkpoint signature.
- Expected ledger identity.
- Checkpoint index.
- Chain-head hash against corresponding stored history.

A valid signature confirms the checkpoint was produced using the corresponding private signing key; its trustworthiness depends on the integrity of that key and its distribution.

## 5.3 Checkpoint History

Store checkpoint metadata in PostgreSQL.

Expose authorized checkpoint history.

For stronger evidence, the user can retain exported checkpoints on an independent machine or storage service.

Do not claim that records held only inside ChainGuard provide independent trust.

## New APIs

`POST /api/ledgers/{ledger_id}/signed-checkpoints`

Create a signed checkpoint.

`GET /api/ledgers/{ledger_id}/signed-checkpoints`

List checkpoint metadata.

`GET /api/ledgers/{ledger_id}/signed-checkpoints/{checkpoint_id}`

Download a checkpoint.

`GET /api/verification-keys/{key_id}`

Obtain the public key.

`POST /api/ledgers/{ledger_id}/verify-checkpoint`

Verify a submitted signed checkpoint against the available ledger history.

This online verification endpoint is supplementary. The standalone offline verifier is required.

## Tests

1. Generate a signed checkpoint.
2. Export the checkpoint.
3. Export the trusted public key.
4. Verify the checkpoint offline.
5. Modify the checkpoint's hash.
6. Confirm signature verification fails.
7. Modify the checkpoint's block index.
8. Confirm verification fails.
9. Append new ledger records.
10. Confirm the older checkpoint still validates its recorded prefix.
11. Rewrite a checkpointed historical block in a disposable database.
12. Confirm comparison against the original checkpoint fails.
13. Supply the wrong public key.
14. Confirm signature verification fails.
15. Restart the server and verify stored checkpoint metadata remains accessible.

## Checkpoint

An independent program can verify signed ledger evidence without trusting the ChainGuard web interface.

---

# PHASE 6 — ENCRYPTED SNAPSHOT MANAGEMENT

## Objective

Preserve the existing encrypted snapshot mechanism and enforce private-ledger authorization.

## Features

- Export complete ledger snapshots.
- Export individual block snapshots.
- Preserve Fernet authenticated encryption.
- Validate snapshot authenticity.
- Compare downloaded snapshots with current records.
- Identify changed or missing records.
- Restrict private snapshot access.

## Existing APIs

`GET /api/ledgers/{ledger_id}/snapshot`

`POST /api/ledgers/{ledger_id}/validate-snapshot`

Keep the existing file format and encryption behavior.

Do not introduce a second snapshot implementation.

## Tests

1. Export a snapshot.
2. Validate it against the unchanged ledger.
3. Confirm validation succeeds.
4. Modify a block in a disposable database.
5. Validate the original snapshot again.
6. Confirm the mismatch is reported.
7. Corrupt the snapshot file.
8. Confirm validation rejects it.
9. Attempt access using an unauthorized account.
10. Confirm private snapshot access is denied.
11. Validate compatibility with existing snapshot behavior.

## Checkpoint

Encrypted snapshots continue functioning and are protected by the appropriate access-control rules.

---

# PHASE 7 — CUSTOMER-FACING SHIPMENT VERIFICATION

## Objective

Allow authorized customers to inspect the recorded history of a shipment and verify its integrity without accessing internal logistics data.

This is a read-only feature.

It must not become a complete shipment management or tracking application.

## 7.1 Customer Access Model

A customer must not obtain access merely by guessing a tracking number.

Implement shipment-scoped access grants.

Each grant contains:

- Grant ID.
- Ledger ID.
- Shipment ID.
- Random, unguessable access secret.
- Expiration timestamp.
- Revoked/active state.

Only the ledger owner or administrator can create and revoke access grants.

Store an appropriate hash of the grant secret.

An authorized customer receives a protected access link or token.

Do not expose internal access credentials inside customer-facing URLs beyond the explicitly scoped verification capability.

Avoid leaking access tokens through logs, referrer headers, analytics, or public sharing.

## 7.2 Customer Timeline

The customer can view:

- Shipment identifier.
- Recorded event type.
- Event timestamp.
- Public-safe location description, if provided.
- Source-system label when appropriate.
- Record integrity status.
- Latest verified checkpoint information.

The customer cannot view:

- Other shipments.
- Internal employee identifiers.
- Integration credentials.
- Private ledger metadata.
- Internal security logs.
- Full administrative block payloads.
- Customer-sensitive details belonging to other users.

## 7.3 Integrity Status

Display:

**Verified**

The selected record is consistent with the chain and trusted checkpoint evidence available to the verifier.

**Verification Failed**

A cryptographic or historical mismatch was found.

**Not Yet Independently Verified**

The record is internally consistent but lacks a trusted external checkpoint covering it.

Do not describe mere internal hash consistency as full independent authenticity verification.

For the MVP, allow authorized customers to download the applicable signed proof and verify it against a separately trusted public key.

No customer blockchain wallet is required.

## 7.4 New APIs

`POST /api/ledgers/{ledger_id}/shipment-access`

Create a shipment-scoped customer access grant.

`DELETE /api/ledgers/{ledger_id}/shipment-access/{grant_id}`

Revoke access.

`GET /api/customer/shipments/{shipment_id}/events`

Return the authorized shipment's public-safe event timeline.

`GET /api/customer/shipments/{shipment_id}/proof`

Return the relevant verification evidence.

Customer endpoints require a valid shipment-scoped access capability.

Use strict expiry checks, bounded responses, and request throttling.

Do not allow customer access tokens to write ledger events or access administrative APIs.

## 7.5 Live Updates

Implement basic near-real-time customer updates using bounded HTTP polling.

Suggested initial interval: 10–15 seconds.

Do not introduce WebSockets solely for this feature.

Events appear when a genuine integrated application submits them.

ChainGuard does not invent new shipment updates or guarantee that a logistics provider publishes them instantly.

## Tests

1. Record real external application events associated with a shipment ID.
2. Create a customer access grant for that shipment.
3. Access its timeline with the valid grant.
4. Confirm only permitted fields appear.
5. Attempt to access a different shipment using the same grant.
6. Confirm access is denied.
7. Attempt access without a grant.
8. Confirm access is denied.
9. Expire or revoke the grant.
10. Confirm access is denied.
11. Append a new event through the external integration.
12. Confirm the customer timeline updates on the next polling cycle.
13. Export the applicable proof.
14. Verify it using the trusted public key.
15. Modify a checkpointed historical record in a disposable database.
16. Confirm the verification status reports failure.

## Checkpoint

An authorized customer can inspect an actual integrated shipment-event history and check its cryptographic integrity without gaining access to unrelated information.

---

# PHASE 8 — SECURITY AUDIT LOGGING

## Objective

Record security-relevant activity for administrators and investigators.

This is not a full SIEM or intrusion detection platform.

## Events to Record

- Invalid integration credentials.
- Revoked or expired credential attempts.
- Unauthorized API operations.
- Rate-limit violations.
- Rejected IP addresses.
- Invalid webhook signatures.
- Replay or idempotency conflicts.
- Security policy changes.
- Integration credential rotation.
- Integrity verification failures.
- Signed checkpoint creation.
- Private customer access grant creation or revocation.

## Security Event Structure

Each event contains:

- Event ID.
- Event timestamp.
- Event category.
- Severity.
- Integration identifier, when available.
- Ledger identifier, when applicable.
- Request operation.
- Outcome.
- Sanitized reason code.
- Source IP, when legitimately required and permitted.

Do not store passwords, bearer tokens, HMAC secrets, private signing keys, or entire sensitive request bodies.

## Storage

Store security events in a dedicated PostgreSQL table.

Security events are administrative telemetry, not blockchain records.

Do not recursively append every internal logging action into the same monitored ledger.

Apply retention limits.

Choose a bounded default retention period of 30 days for MVP logs, configurable by the administrator.

Do not allow anonymous users to retrieve security events.

## API

`GET /api/security/events`

Administrator-only.

Supported filters:

- Severity.
- Event category.
- Integration ID.
- Ledger ID.
- Time range.
- Result limit and pagination.

No automatic email, SMS, Slack, or SIEM integrations.

## Tests

1. Trigger an invalid token request.
2. Confirm a sanitized security event is recorded.
3. Trigger a rate-limit violation.
4. Confirm the event is recorded.
5. Modify a policy.
6. Confirm an audit record is created.
7. Trigger a failed ledger verification.
8. Confirm the event is visible.
9. Inspect logs for leaked secrets.
10. Confirm no credentials appear.
11. Attempt to retrieve logs as a normal user.
12. Confirm access is denied.

## Checkpoint

Administrators can inspect meaningful, privacy-conscious security activity without requiring additional monitoring infrastructure.

---

# PHASE 9 — REACT DASHBOARD

## Objective

Extend the existing frontend into a complete ChainGuard management interface.

Do not rewrite the entire frontend.

Reuse existing components, API helpers, visual styling, and authentication flow.

## 9.1 Dashboard Navigation

Use these main sections:

1. Overview.
2. Ledgers.
3. Integrations.
4. API Security.
5. Verification.
6. Security Events.
7. Account / Administration.

The customer shipment verification page is separate from the administrative navigation.

## 9.2 Overview

Display:

- Total ledgers.
- Active ledgers.
- Total recorded blocks.
- Configured integrations.
- Current ledger integrity statuses.
- Recent rejected API requests.
- Recent verification failures.

Use actual database-backed values.

Do not display invented analytics.

## 9.3 Ledger Management

Preserve:

- Ledger creation.
- Ledger listing.
- Custom field schema.
- Ledger states.
- Block viewing.
- Ledger verification.
- Token rotation.
- Snapshot download.

Add:

- Logistics ledger templates.
- Private-ledger visibility.
- Signed checkpoint actions.

## 9.4 Integration Management

Display:

- Integration name.
- Target ledger.
- Authentication mode.
- Integration status.
- Creation timestamp.
- Last accepted event timestamp, if available.

Provide:

- Create integration.
- Edit integration.
- Disable integration.
- Rotate secret.
- Copy endpoint URL.
- Copy request example.

Never display previously stored secrets.

## 9.5 API Security Policy Editor

For the selected integration, provide controls for:

- Enabled state.
- Requests per minute.
- Allowed API operations.
- IP/CIDR allowlist.
- Maximum request body size.
- Credential expiration.

Show current policy settings.

Require authorization and an appropriate CSRF token when saving.

Provide clear feedback for validation errors.

## 9.6 Verification Interface

Display:

- Ledger integrity status.
- Number of blocks checked.
- Chain-head hash.
- Identified inconsistencies.
- Signed checkpoint history.
- Trusted verification key identifier.
- Snapshot verification results.

Allow users to inspect details without exposing private credentials.

## 9.7 Security Events

Display a paginated table with:

- Timestamp.
- Category.
- Severity.
- Integration.
- Ledger.
- Outcome.
- Sanitized explanation.

Provide basic filtering.

## 9.8 Customer Verification Page

Display:

- Shipment identifier.
- Event timeline.
- Last recorded update.
- Verification result.
- Applicable signed checkpoint.
- Exportable verification evidence.

No administrative controls.

## Tests

1. Login and logout work.
2. Existing ledger views work.
3. Ledger templates appear.
4. Integration creation works.
5. Policy updates persist.
6. Sensitive secrets are not exposed.
7. Verification results display correctly.
8. Security event filters work.
9. Customer access isolation works.
10. All new frontend API requests handle errors.
11. The frontend builds successfully.
12. The UI remains usable on desktop and mobile.

## Checkpoint

A user can configure, monitor, and verify ChainGuard using the React interface without needing to manually interact with PostgreSQL or API endpoints.

---

# PHASE 10 — DEPLOYMENT AND ENVIRONMENT CONFIGURATION

## Objective

Maintain simple deployment while ensuring reasonable security defaults.

## 10.1 Backend Configuration

Preserve existing `.env` handling.

Add configuration only when necessary for:

- Signing-key location.
- Trusted public-key distribution.
- Global maximum API rate limit.
- Global maximum request body size.
- Customer verification access lifetime.
- Security event retention.
- Integration authentication settings.

Never commit production secrets.

Update `.env.example` using placeholders.

## 10.2 Windows

Preserve the existing `Start Ledger.bat` workflow.

It must still:

- Configure dependencies.
- Connect to PostgreSQL.
- Build the frontend.
- Launch the application.

## 10.3 Ubuntu

Preserve deployment scripts and systemd support.

Use an unprivileged application account.

Ensure private signing keys and secret configuration files are readable only by authorized processes.

## 10.4 HTTPS

Use the existing HTTPS deployment support.

Reject insecure production configurations where practical.

Keep secure cookies, origin validation, and security headers.

Do not disable TLS certificate verification to make integrations work.

## 10.5 Database Updates

All new database objects must be created through safe, repeatable migrations or startup initialization routines appropriate to the existing application.

Migration rules:

- Do not delete existing records.
- Do not recreate existing ledgers.
- Do not overwrite signing keys.
- Do not regenerate secrets after restarts.
- Do not silently rewrite historical data.
- Support repeated application startup safely.

## 10.6 Documentation

Update README with:

- Project overview.
- Architecture.
- Supported features.
- Security assumptions.
- Required environment configuration.
- Local setup.
- Ubuntu setup.
- REST integration instructions.
- Webhook integration instructions.
- API security policy documentation.
- Customer verification workflow.
- Signed checkpoint verification instructions.
- Known limitations.

## Tests

1. Start from a clean installation.
2. Create required database tables.
3. Launch backend and frontend.
4. Create an account.
5. Create a ledger.
6. Register an integration.
7. Submit a real event.
8. Verify the ledger.
9. Create a signed checkpoint.
10. Export and verify the checkpoint independently.
11. Restart the application.
12. Confirm existing records remain intact.
13. Confirm revoked credentials remain revoked.
14. Confirm previous checkpoints remain valid.
15. Confirm no secrets are committed to Git.

## Checkpoint

ChainGuard starts reliably using the existing deployment model and retains data and security configuration across restarts.

---

# PHASE 11 — COMPLETE END-TO-END SECURITY TESTING

## Objective

Demonstrate that the individual modules work together.

All testing must use systems and databases owned or explicitly authorized by the developer.

The final demonstration must not depend on a simulated DHL backend.

## Scenario A — Genuine External Integration

Connect a real application to ChainGuard.

Trigger a genuine application event.

The application sends an authenticated HTTP request.

Verify:

- ChainGuard receives the event.
- Authentication succeeds.
- Schema validation succeeds.
- A new block is committed.
- The block appears in the dashboard.
- Ledger verification remains valid.

**PASS:** Genuine application event recorded successfully.

## Scenario B — Unauthorized API Request

Submit an append request with invalid credentials.

Verify:

- Request is rejected.
- No block is created.
- The incident is recorded in security events.

**PASS:** Unauthorized modification prevented.

## Scenario C — Rate-Limit Enforcement

Configure an integration's rate limit.

Send more requests than permitted.

Verify:

- Permitted requests are processed.
- Excess requests return HTTP 429.
- No additional block is created for rejected requests.
- Security events show rate-limit violations.

**PASS:** Configured policy enforced.

## Scenario D — Endpoint Restriction

Configure an append-only integration.

Attempt a read or verification operation using that credential.

Verify access is denied.

**PASS:** Least-privilege permissions enforced.

## Scenario E — Replay Protection

Submit an event with a unique idempotency key.

Submit the same event again using the same key.

Verify:

- First request creates a block.
- Retry returns the existing block.
- The ledger contains only one corresponding record.

Submit different data with the same key.

Verify a conflict response.

**PASS:** Duplicate processing prevented.

## Scenario F — Historical Tampering

Create and externally retain a trusted signed checkpoint.

Modify a historical block in a disposable test database.

Run chain verification and checkpoint comparison.

Verify:

- Integrity verification fails.
- The affected block or history range is identified.
- The original trusted checkpoint does not match the altered history.
- The dashboard reports a failure.

Restore the test database from a known-good backup afterward.

**PASS:** Historical data modification detected.

## Scenario G — Signed Checkpoint Verification

Export a signed checkpoint.

Export the trusted public verification key.

Run the standalone offline verification utility.

Verify:

- Authentic checkpoint passes.
- Modified checkpoint fails.
- Incorrect public key fails.
- The verifier does not depend on the live ChainGuard API.

**PASS:** Independent digital-signature verification works.

## Scenario H — Customer Shipment Verification

Create a shipment-scoped access grant.

Use the grant to open the corresponding customer timeline.

Verify:

- Authorized shipment records appear.
- Unauthorized records do not appear.
- Verification status is accurate.
- A new genuine event appears after polling.
- An expired or revoked grant is rejected.

**PASS:** Customer verification is functional and access-controlled.

## Scenario I — Application Restart

Restart the ChainGuard service.

Verify:

- Existing ledgers remain accessible.
- Hashes remain unchanged.
- Integration configurations persist.
- API policies persist.
- Checkpoints remain available.
- Previous credential revocations remain effective.
- The verification result remains consistent.

**PASS:** Persistent behavior is correct.

## Scenario J — Security Regression

Run all existing and new tests.

Verify:

- Existing authentication works.
- Existing ledgers work.
- Existing snapshots work.
- Existing administration features work.
- Existing hashing behavior remains unchanged.
- Frontend builds without errors.
- No secrets are exposed.
- New API routes do not bypass existing authorization.

**PASS:** No identified regression remains.

---

# 5. FINAL API INVENTORY

The following is the complete intended API inventory. Existing routes should be reused; new routes should be introduced only for missing capabilities.

## Existing APIs — Preserve

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/register` | Account registration |
| POST | `/api/login` | Account login |
| POST | `/api/logout` | Account logout |
| GET | `/api/me` | Current user |
| GET | `/api/ledgers` | List visible ledgers |
| GET | `/api/my-ledgers` | List owned/admin ledgers |
| POST | `/api/ledgers` | Create ledger |
| GET | `/api/ledgers/{id}` | Read ledger |
| PATCH | `/api/ledgers/{id}` | Update permitted metadata |
| DELETE | `/api/ledgers/{id}` | Administrative deletion |
| POST | `/api/ledgers/{id}/blocks` | Append record |
| GET | `/api/ledgers/{id}/credentials` | Integration credentials metadata |
| POST | `/api/ledgers/{id}/rotate-token` | Rotate existing write token |
| POST | `/api/ledgers/{id}/verify` | Verify ledger |
| GET | `/api/ledgers/{id}/checkpoint` | Current unsigned chain-head reference |
| GET | `/api/ledgers/{id}/snapshot` | Export snapshot |
| POST | `/api/ledgers/{id}/validate-snapshot` | Validate snapshot |

Existing routes require authorization adjustments for private-ledger support.

## New APIs — Implement

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/ledger-templates` | Available ledger templates |
| POST | `/api/integrations` | Create integration |
| GET | `/api/integrations` | List integrations |
| GET | `/api/integrations/{id}` | Integration details |
| PATCH | `/api/integrations/{id}` | Update integration |
| DELETE | `/api/integrations/{id}` | Disable/remove integration |
| POST | `/api/integrations/{id}/rotate-secret` | Rotate integration secret |
| POST | `/api/integrations/{id}/webhook` | Receive webhook |
| GET | `/api/integrations/{id}/policy` | Read API security policy |
| PATCH | `/api/integrations/{id}/policy` | Update API security policy |
| POST | `/api/ledgers/{id}/signed-checkpoints` | Create signed checkpoint |
| GET | `/api/ledgers/{id}/signed-checkpoints` | List signed checkpoints |
| GET | `/api/ledgers/{id}/signed-checkpoints/{checkpoint_id}` | Download checkpoint |
| GET | `/api/verification-keys/{key_id}` | Obtain public verification key |
| POST | `/api/ledgers/{id}/verify-checkpoint` | Verify checkpoint against ledger |
| GET | `/api/security/events` | View security audit events |
| POST | `/api/ledgers/{id}/shipment-access` | Create customer access grant |
| DELETE | `/api/ledgers/{id}/shipment-access/{grant_id}` | Revoke access grant |
| GET | `/api/customer/shipments/{shipment_id}/events` | Read authorized shipment timeline |
| GET | `/api/customer/shipments/{shipment_id}/proof` | Obtain shipment verification evidence |

**Implementation note:** Treat this as a contract inventory, not permission to create duplicate endpoints or duplicate authentication systems. Reuse existing services wherever possible.

---

# 6. FINAL SECURITY REQUIREMENTS

These conditions are mandatory.

1. All external write operations require authentication.
2. Every private resource requires server-side authorization.
3. API security policies are deny-by-default.
4. Integration credentials cannot administer the application.
5. Database queries must be parameterized.
6. Secret values cannot appear in logs or ordinary API responses.
7. Cryptographic comparisons must use appropriate constant-time functions.
8. All externally supplied JSON must be validated.
9. Request body and response sizes must be bounded.
10. Replay and duplicate requests must be handled safely.
11. IP restrictions must use verified client addresses.
12. Rate limiting must be enforced by the backend.
13. The application must not trust arbitrary forwarding headers.
14. Signed checkpoint private keys must remain protected.
15. Existing historical hashes must not change unexpectedly.
16. Customer access must be shipment-scoped.
17. Tracking numbers alone must not grant access.
18. Private ledger data and snapshots must not be publicly accessible.
19. Failed security operations must not accidentally append blocks.
20. Database errors must not leak connection strings or secrets.
21. Production TLS verification must remain enabled.
22. New administrative APIs must preserve existing session/CSRF protections.
23. Security controls must remain effective after application restarts.
24. The project must never claim that cryptographic integrity proves physical delivery.
25. All testing must use authorized infrastructure.

---

# 7. IMPLEMENTATION PRIORITIES AND TIME LIMIT

The project must remain achievable within two development days using Codex.

Reuse is mandatory.

## DAY 1 — BACKEND FUNCTIONALITY

**Checkpoint 0:** Baseline verification.

**Checkpoint 1:** Logistics ledger templates.

**Checkpoint 2:** External REST and webhook integrations.

**Checkpoint 3:** API security policies.

**Checkpoint 4:** Existing cryptographic ledger regression tests.

**Checkpoint 5:** Signed checkpoint generation and offline verification.

Expected Day 1 deliverable:

An existing application can submit actual business events, ChainGuard can authenticate and protect the ingestion API, records can be verified, and independent checkpoints can be generated.

## DAY 2 — VERIFICATION, FRONTEND, AND DELIVERY

**Checkpoint 6:** Snapshot access restrictions and regression tests.

**Checkpoint 7:** Customer shipment verification.

**Checkpoint 8:** Security audit logging.

**Checkpoint 9:** Dashboard integration.

**Checkpoint 10:** Deployment and documentation.

**Checkpoint 11:** End-to-end integration and security testing.

Expected Day 2 deliverable:

A complete working MVP with functioning APIs, a usable dashboard, customer verification, real external event ingestion, documented deployment, and automated tests.

## Priority Order If Time Becomes Limited

P0 — Must be completed:

- Existing ledger functionality.
- Authenticated REST event ingestion.
- Configurable API security.
- Integrity verification.
- Signed checkpoint creation.
- Independent checkpoint verification.
- Real external-system integration.
- Passing regression tests.

P1 — Complete after P0:

- Configurable webhook adapter.
- Private-ledger enforcement.
- Customer shipment-scoped verification.
- Security audit logging.
- Dashboard integration.

P2 — Polish only:

- Dashboard visual refinements.
- Additional filters.
- Improved documentation examples.

If time becomes limited, do not compromise authentication, authorization, data integrity, or regression testing to complete cosmetic features.

Do not falsely claim that unfinished P1 or P2 features are implemented.

---

# 8. STRICT OUT-OF-SCOPE FEATURES

Do not build:

- A new blockchain network.
- Distributed consensus.
- Proof-of-work or proof-of-stake.
- Cryptocurrency.
- Smart contracts.
- AI or ML anomaly detection.
- GPS spoofing detection.
- Warehouse management software.
- Delivery routing systems.
- Order management systems.
- A logistics data simulator.
- Mock DHL shipment feeds.
- Unapproved DHL-specific integrations.
- SAP/Oracle proprietary connectors.
- Kafka or distributed event streaming.
- Kubernetes.
- Microservice decomposition.
- Mobile applications.
- Customer payment systems.
- Complex enterprise RBAC frameworks.
- SAML, LDAP, or enterprise SSO.
- A universal API reverse proxy.
- Automatic incident-response systems.
- Automatic third-party alerts.
- Publicly readable customer tracking histories.
- Full customer identity management.
- Blockchain wallets or customer cryptographic key management.
- A redesigned ledger hashing algorithm.
- A completely rewritten frontend.
- Any feature not explicitly specified in this document.

---

# 9. FINAL DELIVERY REQUIREMENTS

The completed project must contain:

1. The updated ChainGuard source code.
2. The preserved existing ledger engine.
3. Functional authenticated integration APIs.
4. Configurable API security policies.
5. A working webhook adapter.
6. Logistics ledger templates.
7. Independently verifiable signed checkpoints.
8. Preserved encrypted snapshots.
9. Security audit records.
10. Read-only authorized customer shipment verification.
11. An updated React dashboard.
12. Automated unit and integration tests.
13. Updated README documentation.
14. Working Windows deployment.
15. Working Ubuntu deployment.
16. A reproducible end-to-end demonstration using a genuine external application.
17. A security limitations section describing the platform's actual guarantees.

## Final Acceptance Standard

The project is complete only when:

- A real external application can submit an event.
- ChainGuard authenticates and authorizes the request.
- The configured API policy is enforced.
- Valid events are recorded correctly.
- Invalid requests are rejected.
- The event appears in the ledger.
- The recorded history verifies successfully.
- A signed checkpoint can be generated.
- An independent verifier can validate checkpoint evidence.
- Historical tampering can be detected using trusted evidence.
- Authorized customers can access only their permitted shipment records.
- The dashboard displays accurate operational and security information.
- All relevant automated tests pass.
- Existing functionality remains intact.
- Deployment is documented and reproducible.

---

# 10. FINAL DHL PROJECT PITCH

**ChainGuard — Blockchain-Based Supply Chain Integrity & Security Audit System**

ChainGuard is a plug-and-play cybersecurity middleware platform that integrates with existing logistics systems through secure REST APIs and webhooks. It protects external integrations using configurable API security policies, records shipment and operational events in tamper-evident ledgers, and enables organizations and authorized customers to independently verify the integrity of historical records without replacing existing infrastructure.

## Project Value

For logistics organizations:

- Secure system-to-system event ingestion.
- Greater visibility into unauthorized API activity.
- Cryptographically verifiable historical records.
- More reliable evidence for audit and dispute investigation.
- Reusable integration with existing enterprise applications.

For authorized customers:

- Shipment event histories sourced from integrated systems.
- Clear record-integrity verification status.
- Access to independently verifiable cryptographic evidence.

## Final Engineering Principle

**Build a security product, not a blockchain demonstration.**

The existing ledger is the foundation.

The actual product value comes from secure integration, configurable API protection, independently verifiable audit evidence, and practical enterprise usability.

---

# 11. INSTRUCTIONS TO CODEX

You are working inside the existing repository:

https://github.com/pulkits2609/Blockchain-Ledger

Your assignment is to transform it into ChainGuard according to this specification.

Follow these implementation rules:

1. Read the existing repository before changing anything.
2. Preserve the working architecture unless a change is essential.
3. Implement phases sequentially.
4. Before each phase, identify the exact existing files to modify.
5. Avoid unnecessary dependencies.
6. Prefer small, focused modules and reusable functions.
7. Never introduce duplicate ledger storage or hashing engines.
8. Preserve compatibility with existing ledgers and snapshots.
9. Use safe, repeatable database migrations.
10. Write automated tests with each new feature.
11. Run the affected tests after each phase.
12. Fix failures before continuing.
13. Run the complete test suite at major checkpoints.
14. Do not bypass security checks merely to make tests pass.
15. Do not invent external integration credentials, business data, or DHL API behavior.
16. Do not implement unspecified features.
17. Do not silently weaken existing security controls.
18. Do not declare a phase complete until its acceptance criteria pass.
19. Document any remaining limitation or unverified assumption.
20. Keep the two-day MVP target in mind and prioritize the core security guarantees.

After each checkpoint, report:

- Phase completed.
- Files created or modified.
- APIs added or changed.
- Tests executed.
- Test results.
- Remaining issues.
- Any deviation from this frozen scope.

At the end, run the full regression suite, verify the frontend build, and provide a concise implementation summary documenting which features are actually complete.

**END OF CHAINGUARD IMPLEMENTATION BLUEPRINT — VERSION 1.0**

