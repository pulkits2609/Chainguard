# Independent application integration evidence

Run: 2026-10-29T02:37:37+00:00

```json
{
  "result": "PASS",
  "source_application": "Existing independently running AudioShare session backend",
  "source_operation": "POST /v1/session",
  "event_type": "SESSION_CREATED",
  "resource_id": "76715d8e-1baf-4bbb-995c-15e5e00581de",
  "actual_voice_lobby_created": true,
  "automatic_forwarding": true,
  "forwarding_mechanism": "Application-side HTTP finish-event preload middleware",
  "harness_forwarded_initial_event": false,
  "native_provider_webhook": false,
  "rest_ingestion": "Committed automatically",
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
  "trust_limit": "Application-side middleware automatically observes a successful source HTTP completion. This is operational session evidence, not provider-native webhook support, a DHL shipment, or proof of a physical delivery."
}
```

Reproduce from ChainGuard with `.venv\Scripts\python.exe tools/test_external_integration.py`. Requires the existing AudioShare checkout, its built service, Node, actual LiveKit and private voice executables, and the local `chainguard_test` connection in `.env`. No media simulator is used. Test accounts, ledgers, connector secrets and owned processes are removed after the run. The application-side preload automatically forwards only source event type, source-system label, server-generated resource identity and event time; JWTs and access tokens are excluded. The acceptance script only triggers the source operation and polls for the initial event; later manual retries test the API contract. A passing run demonstrates a real separate application's operational event. It does not establish a logistics-provider integration.
