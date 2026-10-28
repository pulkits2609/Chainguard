import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import { checkpointSignedBytes, customerLink, customerLocation, expiryTimestamp, policyPayload, verifyCheckpointSignature, webhookExample } from './dashboardHelpers.js';

test('customer links keep scoped secrets in the fragment and parse without persisting them', () => {
  const url = new URL(customerLink('SHP/a & b', 'grant.secret+/#?', 'https://chainguard.example'));
  assert.equal(url.searchParams.get('shipment'), 'SHP/a & b');
  assert.equal(url.search.includes('secret'), false);
  assert.equal(url.pathname, '/');
  assert.deepEqual(customerLocation(url), { shipment: 'SHP/a & b', token: 'grant.secret+/#?' });
  assert.equal(customerLocation(new URL('https://chainguard.example/')), null);
});

test('webhook examples respect explicit nested mapping without modifying object prototypes', () => {
  const integration = { event_id_field: 'metadata.id', timestamp_field: 'metadata.time', field_mapping: { quantity: 'details.count', nested: '__proto__.value' } };
  const result = webhookExample(integration, [{ name: 'quantity', type: 'integer' }, { name: 'nested', type: 'string' }], '2026-10-29T00:00:00Z');
  assert.equal(result.details.count, 1);
  assert.equal(result.metadata.id, 'REPLACE_WITH_STABLE_SOURCE_EVENT_ID');
  assert.equal(result.metadata.time, '2026-10-29T00:00:00Z');
  assert.equal(Object.hasOwn(result, '__proto__'), true);
  assert.equal(Object.prototype.value, undefined);
  assert.equal(result.__proto__.value, 'REPLACE_NESTED');
});

test('policy serialization keeps authentication mandatory and bounds numeric input', () => {
  const values = { enabled: true, requests_per_minute: '60', allowed_operations: ['append'],
    allowed_source_cidrs: ' 127.0.0.1/32\n::1/128,\n', max_body_bytes: '65536', credential_expires_at: '' };
  assert.deepEqual(policyPayload(values), { enabled: true, requests_per_minute: 60, allowed_operations: ['append'],
    allowed_source_cidrs: ['127.0.0.1/32', '::1/128'], max_body_bytes: 65536, credential_expires_at: null, require_authentication: true });
  for (const value of ['0', '-1', '1.5', 'NaN', 'Infinity']) assert.throws(() => policyPayload({ ...values, requests_per_minute: value }));
  assert.throws(() => policyPayload({ ...values, max_body_bytes: '65537' }));
  assert.throws(() => expiryTimestamp('not-a-time'));
});

test('browser signature verification matches canonical Ed25519 checkpoints and rejects changes', async () => {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  const raw = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32);
  const keyId = createHash('sha256').update(raw).digest('hex');
  const trusted = { format_version: 1, algorithm: 'Ed25519', key_id: keyId, public_key: raw.toString('base64') };
  const checkpoint = { format_version: 1, checkpoint_id: '404bef61-46e0-441e-a026-3f41e68578b9',
    ledger_id: '9562a05a-3384-468b-9fe2-d697172d670c', index: 12, hash: 'a'.repeat(64), created_at_ms: 1793210400000, key_id: keyId };
  const canonical = JSON.stringify(Object.fromEntries(Object.entries(checkpoint).sort(([a], [b]) => a.localeCompare(b))));
  checkpoint.signature = sign(null, Buffer.from(canonical), privateKey).toString('base64');
  assert.equal(new TextDecoder().decode(checkpointSignedBytes(checkpoint)), canonical);
  assert.equal(await verifyCheckpointSignature(checkpoint, trusted), true);
  assert.equal(await verifyCheckpointSignature({ ...checkpoint, index: 13 }, trusted), false);
  assert.throws(() => checkpointSignedBytes({ ...checkpoint, index: true }), /malformed/);
  assert.throws(() => checkpointSignedBytes({ ...checkpoint, ignored: 'field' }), /malformed/);
  await assert.rejects(verifyCheckpointSignature(checkpoint, { ...trusted, public_key: Buffer.alloc(32).toString('base64') }), /identifier/);
  await assert.rejects(verifyCheckpointSignature(checkpoint, { ...trusted, key_id: 'b'.repeat(64) }), /does not match/);
});
