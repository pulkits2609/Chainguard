import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import test from 'node:test';
import { EventForwarder, eventId, targetUrl, validateEvent } from './event_forwarder.mjs';
import { observeSession } from './audioshare_observer.mjs';

const ledger = '11111111-1111-4111-8111-111111111111';
const identity = '22222222-2222-4222-8222-222222222222';
const token = `cg_${'a'.repeat(32)}_${'b'.repeat(43)}`;
const event = { event_type: 'SESSION_CREATED', source_system: 'audioShare', resource_id: identity, event_time: '2026-10-29T01:00:00Z' };

async function server(handler) {
  const instance = createServer(handler); instance.listen(0, '127.0.0.1'); await once(instance, 'listening');
  return { instance, origin: `http://127.0.0.1:${instance.address().port}`,
    close: () => new Promise(resolve => { instance.closeAllConnections(); instance.close(resolve); }) };
}

test('target configuration rejects remote HTTP, credentials, queries and arbitrary paths', () => {
  for (const url of [`http://example.com/api/ledgers/${ledger}/blocks`, `https://user:secret@example.com/api/ledgers/${ledger}/blocks`,
    `https://example.com/api/ledgers/${ledger}/blocks?secret=1`, 'https://example.com/other']) assert.throws(() => targetUrl(url, true));
  assert.throws(() => targetUrl(`http://127.0.0.1/api/ledgers/${ledger}/blocks`));
  assert.equal(targetUrl(`https://example.com/api/ledgers/${ledger}/blocks`), `https://example.com/api/ledgers/${ledger}/blocks`);
});

test('only operational fields are accepted and retry identity is stable', () => {
  assert.deepEqual(validateEvent(event), event);
  assert.throws(() => validateEvent({ ...event, token: 'never-forward-a-jwt' }));
  assert.equal(eventId(event), eventId({ ...event, event_time: '2026-10-29T01:00:01Z' }));
  assert.notEqual(eventId(event), eventId({ ...event, resource_id: ledger }));
});

test('the actual finish event automatically delivers only safe source fields', async () => {
  const captured = [];
  const sink = await server(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    captured.push({ body: JSON.parse(Buffer.concat(chunks)), id: req.headers['idempotency-key'] }); res.writeHead(201).end('{}');
  });
  const forwarder = new EventForwarder({ endpoint: `${sink.origin}/api/ledgers/${ledger}/blocks`, token, allowLoopbackHttp: true });
  const source = await server((req, res) => {
    observeSession(req, res, forwarder);
    res.writeHead(req.headers['x-fail'] ? 401 : 200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ identity, voiceLobby: { groupId: 7 }, token: 'PRIVATE-JWT', name: 'PRIVATE-NAME' }));
  });
  try {
    await (await fetch(`${source.origin}/v1/session`, { method: 'POST' })).body.cancel();
    await forwarder.flush();
    assert.equal(captured.length, 1); assert.equal(captured[0].body.resource_id, identity);
    assert.deepEqual(Object.keys(captured[0].body).sort(), Object.keys(event).sort());
    assert.equal(captured[0].id, eventId(captured[0].body));
    assert.doesNotMatch(JSON.stringify(captured), /PRIVATE/);
    await (await fetch(`${source.origin}/v1/session`, { method: 'POST', headers: { 'x-fail': 'true' } })).body.cancel();
    await forwarder.flush(); assert.equal(captured.length, 1);
  } finally { await source.close(); await sink.close(); }
});

test('oversized and unrelated source responses never create events', async () => {
  let submitted = 0;
  const source = await server((req, res) => {
    observeSession(req, res, { enqueue() { submitted += 1; } });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ identity, voiceLobby: { groupId: 7 }, token: 'x'.repeat(20000) }));
  });
  try {
    await (await fetch(`${source.origin}/v1/session`, { method: 'POST' })).body.cancel();
    await (await fetch(`${source.origin}/health`)).body.cancel();
    assert.equal(submitted, 0);
  } finally { await source.close(); }
});

test('transient delivery retries keep one event id; redirects never carry credentials', async () => {
  const ids = []; let targetRequests = 0;
  const destination = await server((_req, res) => { targetRequests += 1; res.end('{}'); });
  const sink = await server((req, res) => {
    ids.push(req.headers['idempotency-key']); req.resume();
    if (ids.length === 1) res.writeHead(503).end('{}');
    else if (ids.length === 2) res.writeHead(201).end('{}');
    else res.writeHead(302, { Location: destination.origin }).end();
  });
  const failures = [];
  const forwarder = new EventForwarder({ endpoint: `${sink.origin}/api/ledgers/${ledger}/blocks`, token,
    allowLoopbackHttp: true, maxAttempts: 2, onFailure: result => failures.push(result) });
  try {
    forwarder.enqueue(event); await forwarder.flush();
    assert.equal(ids.length, 2); assert.equal(ids[0], ids[1]); assert.equal(forwarder.snapshot().delivered, 1);
    forwarder.enqueue(event); await forwarder.flush();
    assert.equal(targetRequests, 0); assert.equal(forwarder.snapshot().failed, 1);
    assert.doesNotMatch(JSON.stringify(failures), new RegExp(token));
  } finally { await sink.close(); await destination.close(); }
});

test('bounded queue exposes drops and finite delivery timeout', async () => {
  const sink = await server(req => req.resume());
  const failures = [];
  const forwarder = new EventForwarder({ endpoint: `${sink.origin}/api/ledgers/${ledger}/blocks`, token,
    allowLoopbackHttp: true, queueCapacity: 1, maxAttempts: 1, timeoutMs: 50, onFailure: result => failures.push(result) });
  try {
    assert.equal(forwarder.enqueue(event), true); assert.equal(forwarder.enqueue(event), false);
    await forwarder.flush();
    assert.equal(forwarder.snapshot().dropped, 1); assert.equal(forwarder.snapshot().failed, 1);
    assert.ok(failures.some(result => result.reason === 'queue_full'));
  } finally { await sink.close(); }
});
