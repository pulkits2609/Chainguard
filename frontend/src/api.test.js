import test from 'node:test';
import assert from 'node:assert/strict';
import { apiRequest, customerRequest, ledgerPath } from './api.js';

test('API uses cookie sessions and current CSRF without persisting secrets', async () => {
  const original = globalThis.fetch;
  const requests = [];
  const replies = [
    { user: { id: 'owner' }, csrf_token: 'first-csrf' },
    { ledger: { id: 'ledger' }, write_token: 'shown-once', write_path: '/api/ledgers/ledger/blocks' },
    { ok: true },
    { user: { id: 'next' }, csrf_token: 'next-csrf' },
    { ok: true },
  ];
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    return { ok: true, json: async () => replies.shift() };
  };
  try {
    await apiRequest('/api/me');
    await apiRequest('/api/ledgers', { method: 'POST', body: '{}' });
    await apiRequest('/api/logout', { method: 'POST', body: '{}' });
    await apiRequest('/api/login', { method: 'POST', body: '{}' });
    await apiRequest('/api/ledgers/x', { method: 'DELETE', body: '{"confirm":"x"}' });
    assert.equal(requests[0].options.credentials, 'same-origin');
    assert.equal(requests[1].options.headers['X-CSRF-Token'], 'first-csrf');
    assert.equal(requests[2].options.headers['X-CSRF-Token'], 'first-csrf');
    assert.equal(requests[3].options.headers['X-CSRF-Token'], undefined);
    assert.equal(requests[4].options.headers['X-CSRF-Token'], 'next-csrf');
    assert.ok(requests.every(request => !request.options.headers.Authorization));
    assert.equal(ledgerPath('a/b'), '/api/ledgers/a%2Fb');
  } finally { globalThis.fetch = original; }
});

test('customer capability requests never carry cookies or CSRF and do not expire admin sessions', async () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  let request;
  let dispatched = false;
  globalThis.window = { dispatchEvent: () => { dispatched = true; } };
  globalThis.fetch = async (path, options) => {
    request = { path, options };
    return { ok: false, status: 401, json: async () => ({ error: 'Grant expired' }) };
  };
  try {
    await assert.rejects(customerRequest('shipment/a', 'events', 'grant.secret'), error => error.status === 401 && error.message === 'Grant expired');
    assert.equal(request.path, '/api/customer/shipments/shipment%2Fa/events');
    assert.equal(request.options.credentials, 'omit');
    assert.deepEqual(request.options.headers, { Authorization: 'Bearer grant.secret' });
    assert.equal(dispatched, false);
    await assert.rejects(customerRequest('shipment', '../admin', 'token'), /Unsupported customer operation/);
  } finally { globalThis.fetch = originalFetch; globalThis.window = originalWindow; }
});

test('binary validation preserves content type and caller cancellation', async () => {
  const original = globalThis.fetch;
  const controller = new AbortController();
  globalThis.fetch = async (_, options) => {
    assert.equal(options.headers['Content-Type'], 'application/octet-stream');
    assert.equal(options.signal, controller.signal);
    return { ok: true, json: async () => ({ valid: false }) };
  };
  try {
    const result = await apiRequest('/api/ledgers/example/validate-snapshot', {
      method: 'POST', body: new Uint8Array([1]), headers: { 'Content-Type': 'application/octet-stream' }, signal: controller.signal,
    });
    assert.equal(result.valid, false);
  } finally { globalThis.fetch = original; }
});
