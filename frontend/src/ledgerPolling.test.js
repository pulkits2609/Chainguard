import test from 'node:test';
import assert from 'node:assert/strict';
import { LedgerPollDelay, retryAfterMilliseconds, startLedgerPolling } from './ledgerPolling.js';

const settle = async () => { for (let index = 0; index < 6; index += 1) await Promise.resolve(); };

function environment(hidden = false) {
  let time = 0;
  let nextId = 0;
  const timers = new Map();
  const listeners = new Set();
  const documentTarget = { hidden,
    addEventListener: (name, listener) => { assert.equal(name, 'visibilitychange'); listeners.add(listener); },
    removeEventListener: (name, listener) => { assert.equal(name, 'visibilitychange'); listeners.delete(listener); },
  };
  return {
    documentTarget, timers, listeners,
    now: () => time,
    setTimer: (callback, delay) => { const id = ++nextId; timers.set(id, { callback, at: time + delay }); return id; },
    clearTimer: id => timers.delete(id),
    async advance(milliseconds) {
      const target = time + milliseconds;
      while (true) {
        const due = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        time = due[1].at; timers.delete(due[0]); due[1].callback(); await settle();
      }
      time = target; await settle();
    },
    visibility(value) { documentTarget.hidden = value; for (const listener of listeners) listener(); },
  };
}

function start(clock, read, extra = {}) {
  return startLedgerPolling({ ...clock, read, onValue: () => {}, onError: () => {}, ...extra });
}

test('two visible viewers poll every ten seconds without exhausting the default source budget', async () => {
  assert.equal(LedgerPollDelay, 10000);
  const clock = environment();
  const requests = [];
  const readers = [1, 2].map(id => start(clock, async () => { requests.push({ id, time: clock.now() }); return {}; }));
  await settle();
  await clock.advance(180000);
  assert.equal(requests.length, 38);
  const windows = Map.groupBy(requests, request => Math.floor(request.time / 60000));
  assert.ok([...windows.values()].every(window => window.length <= 12));
  readers.forEach(reader => reader.stop());
  assert.equal(clock.timers.size, 0);
});

test('hidden tabs do not poll and returning visible performs one immediate refresh', async () => {
  const clock = environment(true);
  let requests = 0;
  const reader = start(clock, async () => { requests += 1; return {}; });
  await clock.advance(180000);
  assert.equal(requests, 0);
  clock.visibility(false); await clock.advance(0);
  assert.equal(requests, 1);
  clock.visibility(true); await clock.advance(180000);
  assert.equal(requests, 1);
  clock.visibility(false); await clock.advance(0);
  assert.equal(requests, 2);
  reader.stop();
});

test('hide cancels in-flight work, suppresses stale data and prevents overlap on resume', async () => {
  const clock = environment();
  const requests = [];
  const values = [];
  const errors = [];
  const reader = start(clock, signal => new Promise(resolve => requests.push({ signal, resolve })),
    { onValue: value => values.push(value), onError: error => errors.push(error) });
  clock.visibility(true);
  assert.equal(requests[0].signal.aborted, true);
  clock.visibility(false); reader.refresh(); await clock.advance(0);
  assert.equal(requests.length, 1);
  requests[0].resolve('stale'); await settle(); await clock.advance(0);
  assert.equal(requests.length, 2);
  requests[1].resolve('current'); await settle();
  assert.deepEqual(values, ['current']);
  assert.deepEqual(errors, []);
  reader.stop();
});

test('manual refresh queues one request without overlap and cleanup removes listeners and timers', async () => {
  const clock = environment();
  const requests = [];
  const values = [];
  const reader = start(clock, signal => new Promise(resolve => requests.push({ signal, resolve })), { onValue: value => values.push(value) });
  reader.refresh(); reader.refresh(); await clock.advance(0);
  assert.equal(requests.length, 1);
  requests[0].resolve('first'); await settle(); await clock.advance(0);
  assert.equal(requests.length, 2);
  reader.stop();
  assert.equal(requests[1].signal.aborted, true);
  assert.equal(clock.listeners.size, 0);
  assert.equal(clock.timers.size, 0);
  requests[1].resolve('after stop'); await settle();
  assert.deepEqual(values, ['first']);
});

test('429 Retry-After cannot be bypassed by manual refresh or visibility changes', async () => {
  const clock = environment();
  let requests = 0;
  const reader = start(clock, async () => {
    requests += 1;
    if (requests === 1) throw Object.assign(new Error('Limited'), { status: 429, retryAfterMs: 90000 });
    return {};
  });
  await settle();
  reader.refresh(); await clock.advance(10000);
  clock.visibility(true); clock.visibility(false); reader.refresh();
  await clock.advance(79999);
  assert.equal(requests, 1);
  await clock.advance(1);
  assert.equal(requests, 2);
  reader.stop();
});

test('a timed-out read aborts and schedules the next request only after completion', async () => {
  const clock = environment();
  let requests = 0;
  const errors = [];
  const reader = start(clock, signal => {
    requests += 1;
    return new Promise((resolve, reject) => signal.addEventListener('abort',
      () => reject(Object.assign(new Error('Timed out'), { name: 'AbortError' })), { once: true }));
  }, { onError: error => errors.push(error.name) });
  await clock.advance(4999);
  assert.equal(errors.length, 0);
  await clock.advance(1);
  assert.deepEqual(errors, ['AbortError']);
  await clock.advance(9999);
  assert.equal(requests, 1);
  await clock.advance(1);
  assert.equal(requests, 2);
  reader.stop(); await settle();
  assert.equal(clock.timers.size, 0);
});

test('Retry-After accepts integer seconds and HTTP dates, rejecting invalid values', () => {
  const now = Date.parse('2026-10-29T00:00:00Z');
  assert.equal(retryAfterMilliseconds('45', now), 45000);
  assert.equal(retryAfterMilliseconds('Thu, 29 Oct 2026 00:01:00 GMT', now), 60000);
  for (const value of ['', undefined, '-1', 'not a date', 'Wed, 28 Oct 2026 00:01:00 GMT']) {
    assert.equal(retryAfterMilliseconds(value, now), 0);
  }
});
