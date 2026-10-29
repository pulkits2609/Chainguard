# Managed credentials for new template ledgers

New ledgers created from any predefined ChainGuard template require managed integration credentials. They do not issue a legacy ledger write token, and their legacy-token rotation endpoint is disabled. Create a connector in **Integrations**, choose its credential scopes, and configure its API Security policy before submitting events.

Both the direct REST append endpoint and webhook adapter use the managed IntegrationService authorization path. Source CIDRs, expiry/revocation, allowed operations, enabled state, body caps and rate limits therefore apply. Even a credential matching a manually installed legacy token hash cannot write to a managed-only ledger.

The registry flag `managed_writes_required` is set by the server, not a client-controlled ledger-creation or metadata-update field. It defaults to false when added to existing database rows. New template ledgers set it to true. Existing generic ledgers and template ledgers created before this change retain their legacy behavior; no stored block, genesis, hash, snapshot format or existing token is silently rewritten. Custom generic ledgers still support legacy write tokens for compatibility. Consequently, deployments that require every write to be policy-managed should use new template ledgers and avoid legacy generic credentials for that workload.

Read access/visibility is unchanged by this flag. New template ledgers still default to private; owners/admins may change visibility through the authorized API. Public visibility does not re-enable legacy writes.

`Store.append_authorized()` remains an internal trusted-service boundary shared by managed ingestion and engine tests. It is not a public API and does not independently authenticate callers. HTTP clients cannot invoke it directly.

Regression coverage includes all four templates, absent one-time legacy secrets, blocked legacy rotation, denial even with a matching stored legacy token hash, managed source/expiry/disable enforcement, real HTTP rejection of a policy-bypass attempt, and preservation of old generic/pre-remediation credentials and genesis hashes.
