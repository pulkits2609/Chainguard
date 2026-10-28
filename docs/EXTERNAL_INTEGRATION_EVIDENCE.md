# Independent application integration evidence

Run: 2026-10-28T18:45:32+00:00

```json
{
  "result": "PASS",
  "source_application": "Existing independently running AudioShare session backend",
  "source_operation": "POST /v1/session",
  "event_type": "SESSION_CREATED",
  "resource_id": "73bbb953-ca75-4b9d-8316-2252f4e457f7",
  "actual_voice_lobby_created": true,
  "rest_ingestion": "201",
  "idempotent_rest_retry": "200, duplicate=true",
  "conflicting_retry": "409",
  "invalid_credential": "403",
  "webhook_retry": "200, duplicate=true",
  "recorded_business_events": 1,
  "ledger_verification": "valid",
  "offline_full_history_verification": "valid",
  "source_code_modified": false,
  "production_database_used": false,
  "source_entry_sha256": "e42df4f10098a2ab3885b771e8a21391bc3745a8ae372f7206ac4cb8e6211a72",
  "trust_limit": "The adapter observes a real application API result. This is operational session evidence, not a DHL shipment or proof of a physical delivery."
}
```

Reproduce from ChainGuard with `.venv\Scripts\python.exe tools/test_external_integration.py`. Requires the existing AudioShare checkout, its built service, Node, actual LiveKit and private voice executables, and the local `chainguard_test` connection in `.env`. No media simulator is used. Test accounts, ledgers, connector secrets and owned processes are removed after the run. The generic adapter forwards only source event type, source-system label, server-generated resource identity and event time; JWTs and access tokens are excluded. A passing run demonstrates a real separate application's operational event. It does not establish a logistics-provider integration.
