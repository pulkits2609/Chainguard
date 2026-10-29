# Automatic operational event integration

ChainGuard now includes an **application-side event adapter** for the independently running AudioShare backend. It is not provider-native webhook support, a logistics feed, or a universal HTTP proxy.

## What runs automatically

The existing AudioShare Node application starts with `adapters/audioshare_events.mjs` as a Node preload. The adapter subscribes to the source HTTP server's actual request/response lifecycle. A completed successful `POST /v1/session` response containing a server-generated UUID identity and an actual voice-lobby binding causes a `SESSION_CREATED` event to be delivered automatically through ChainGuard's normal managed REST integration API.

No AudioShare source files are edited. No request is routed through a new proxy. A caller uses the same AudioShare API it normally would; it does not call ChainGuard. The acceptance harness now triggers the real source operation and polls the authorized ChainGuard ledger. It does **not** translate or forward the initial source response itself. Later manual retries in the harness exercise idempotency and conflict handling after automatic delivery has already happened.

The source adapter forwards exactly four fields: `event_type`, `source_system`, `resource_id` and `event_time`. It excludes JWTs, join tokens, display names, room details, arbitrary response fields, and source request bodies. The source response capture is capped at16KiB, checked before copying a chunk. Failed, malformed, oversized or unrelated source responses create no event.

## Configuration

Create a private Generic Security Events ledger and a managed bearer integration in ChainGuard. Give it append-only scope and the relevant policy. Keep its secret in the **producing application's** protected configuration; never give the producing application ChainGuard database credentials.

```dotenv
CHAINGUARD_EVENT_URL=https://chainguard.example/api/ledgers/LEDGER_UUID/blocks
CHAINGUARD_EVENT_TOKEN=MANAGED_INTEGRATION_SECRET
```

Replace both placeholders with the actual endpoint/credential. URLs containing embedded credentials, queries, fragments, non-append paths or remote plain HTTP are rejected. TLS certificate verification stays enabled, and redirects are never followed. Local development may additionally set `CHAINGUARD_ADAPTER_ALLOW_LOOPBACK_HTTP=true`, allowing HTTP only to `127.0.0.1`, `localhost` or `::1`.

Start the existing application with its configuration available **before** the preload runs. Node's `--env-file` is one option; a protected service environment is another:

```powershell
node --env-file="C:\private\audioshare-chainguard.env" --import="file:///C:/PATH/Chainguard/adapters/audioshare_events.mjs" "C:\PATH\audioShare\server\dist\index.js"
```

Use an absolute `file://` module URL for the Windows preload path, URL-encoding spaces. Keep the source application's existing LiveKit/private-voice configuration and dependencies. Its entry point's later `.env` loading does not configure a preload that has already executed; configure adapter variables in the process/service environment or `--env-file` first.

## Other applications

`adapters/event_forwarder.mjs` is reusable for an application's actual event notification callback:

```javascript
import { EventForwarder } from './event_forwarder.mjs';
const audit = new EventForwarder({ endpoint: process.env.CHAINGUARD_EVENT_URL,
  token: process.env.CHAINGUARD_EVENT_TOKEN });
applicationEvents.on('resource.created', resource => audit.enqueue({
  event_type: 'RESOURCE_CREATED', source_system: 'your_application',
  resource_id: resource.id, event_time: resource.createdAt,
}));
```

This snippet is an integration contract, not a claim that every application already emits that event. An application-specific adapter must register with the producer's real event mechanism. The AudioShare preload is the concrete supported adapter delivered here.

## Delivery and failure limits

The forwarder uses a bounded in-memory queue of100 events, one in-flight request, a five-second request timeout and at most three attempts. Idempotency uses a stable SHA-256 identifier derived from source-system label, operation and server resource identity. The source must therefore provide a unique resource identity for each logical operation of a given type; repeated callbacks for that operation deduplicate, while a new session UUID creates a new record.

HTTP200/201 acknowledges a committed ChainGuard result. Transient network/server/rate-limit failures receive finite backoff retries. Authentication/schema/permission failures stop immediately. Redirect destinations never receive credentials. Queue saturation and exhausted delivery emit sanitized reason/status/attempt diagnostics to stderr; no body, URL credential or exception text is logged. `EventForwarder.snapshot()` exposes pending/delivered/failed/dropped counters to an application's own monitoring integration.

This is **best-effort application telemetry**, not a durable broker or atomic business/database outbox. Process termination, response abort before `finish`, queue overflow, or exhausted retries can lose an event. The adapter does not block or roll back an already-successful source operation. Operators must monitor failures; guaranteed audit delivery would require an explicitly designed source outbox and is outside this MVP.

## Verification performed

On29 October2026, six Node regression tests passed for endpoint restrictions, safe projections, stable identifiers, real response-finish delivery, exclusion of failed/oversized responses, bounded queue/timeouts, retry identity and redirect refusal. The shared Python runner includes them through `tools/test_event_adapter.py`.

The updated actual acceptance harness passed using the existing AudioShare Node backend, real LiveKit executable and real private C++ voice service:

- An ordinary source API call created an actual session UUID and voice lobby.
- The preload automatically committed exactly one corresponding event to ChainGuard.
- The harness only observed initial delivery; no initial manual forwarding occurred.
- Matching REST/webhook retries returned the original record; conflicting retry returned409; invalid credentials were denied.
- Ledger verification and standalone offline full-history/checkpoint verification passed.
- The original source entry-point SHA-256 remained unchanged, and no production/original database was used.

The successful observed run included source session identity `d3602925-da27-4c1c-a9f5-3759fb7eea58`; its evidence is retained in the ignored test-output artifact when that run is recorded. Repeated source startup/session creation also exposed an intermittent dependency limitation: AudioShare sometimes closes its own `/v1/session` connection during real media/gateway initialization before sending a response. Such a run is reported **PENDING** with a sanitized source-operation blocker, rather than being counted as successful delivery. No source timeout, authentication, or media implementation was weakened to hide this behavior. This acceptance check proves the completed automatic flow on successful source operations, not general availability of AudioShare's native media stack.

Reproduce with `.venv\Scripts\python.exe tools/test_external_integration.py --evidence test-output/automatic-integration-evidence.md`. The harness requires the existing source checkout and its documented binaries. It uses only `chainguard_test`, unused loopback ports, an owned temporary source configuration, and exact owned process/resource cleanup. Source child environments deliberately exclude unrelated ChainGuard/ledger environment credentials.

This proves automatic controlled application-side operational integration. It does not establish native provider webhook support, a DHL/logistics-system integration, real shipment activity or physical-delivery authenticity.
