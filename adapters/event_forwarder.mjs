// Application-side delivery only. This does not run inside the ChainGuard API.
import { createHash } from 'node:crypto';

const Fields = ['event_type', 'source_system', 'resource_id', 'event_time'];
const Token = /^cg_[0-9a-f]{32}_[A-Za-z0-9_-]{43}$/;

export function validateEvent(event) {
  if (!event || typeof event !== 'object' || Array.isArray(event)
      || Object.keys(event).length !== Fields.length || Fields.some(key => !Object.hasOwn(event, key))
      || Fields.some(key => typeof event[key] !== 'string')) throw new TypeError('Invalid operational event fields');
  if (!/^[A-Z][A-Z0-9_]{0,63}$/.test(event.event_type)
      || !/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(event.source_system)
      || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(event.resource_id)
      || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(event.event_time)
      || !Number.isFinite(Date.parse(event.event_time))) throw new TypeError('Invalid operational event values');
  return Object.fromEntries(Fields.map(key => [key, event[key]]));
}

export function eventId(event) {
  const checked = validateEvent(event);
  // Source resource identity and operation identify a stable logical event; retry
  // timestamps and delivery attempt counts never become part of its identifier.
  return createHash('sha256').update(`${checked.source_system}\0${checked.event_type}\0${checked.resource_id}`).digest('hex');
}

export function targetUrl(value, allowLoopbackHttp = false) {
  let url;
  try { url = new URL(value); }
  catch { throw new TypeError('Adapter target URL is invalid'); }
  const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  if (url.username || url.password || url.search || url.hash
      || !/^\/api\/ledgers\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/blocks$/.test(url.pathname)
      || (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback && allowLoopbackHttp))) {
    throw new TypeError('Adapter target must be a configured HTTPS ledger append endpoint; loopback HTTP requires explicit opt-in');
  }
  return url.href;
}

export class EventForwarder {
  #token;
  constructor({ endpoint, token, allowLoopbackHttp = false, queueCapacity = 100,
    maxAttempts = 3, timeoutMs = 5000, onFailure = () => {} }) {
    this.endpoint = targetUrl(endpoint, allowLoopbackHttp);
    if (typeof token !== 'string' || !Token.test(token)) throw new TypeError('A managed ChainGuard integration credential is required');
    for (const [value, maximum] of [[queueCapacity, 1000], [maxAttempts, 5], [timeoutMs, 30000]]) {
      if (!Number.isInteger(value) || value < 1 || value > maximum) throw new TypeError('Invalid bounded adapter configuration');
    }
    if (typeof onFailure !== 'function') throw new TypeError('Adapter failure reporter must be a function');
    this.#token = token;
    this.queueCapacity = queueCapacity; this.maxAttempts = maxAttempts; this.timeoutMs = timeoutMs;
    this.onFailure = onFailure; this.queue = []; this.running = false; this.waiters = [];
    this.counts = { delivered: 0, failed: 0, dropped: 0 };
  }

  snapshot() { return { ...this.counts, pending: this.queue.length + Number(this.running) }; }

  reportFailure(result) {
    try { this.onFailure(result); } catch { /* Monitoring cannot crash the source application. */ }
  }

  enqueue(event) {
    const safe = validateEvent(event);
    if (this.queue.length + Number(this.running) >= this.queueCapacity) {
      this.counts.dropped += 1; this.reportFailure({ reason: 'queue_full', attempts: 0 }); return false;
    }
    this.queue.push({ event: safe, id: eventId(safe) });
    if (!this.running) void this.drain();
    return true;
  }

  async deliver(item) {
    let status = null;
    for (let attempt = 1; attempt <= this.maxAttempts; attempt += 1) {
      try {
        const response = await fetch(this.endpoint, { method: 'POST', redirect: 'error',
          signal: AbortSignal.timeout(this.timeoutMs), headers: { Authorization: `Bearer ${this.#token}`,
            'Content-Type': 'application/json', 'Idempotency-Key': item.id }, body: JSON.stringify(item.event) });
        status = response.status;
        await response.body?.cancel();
        if (status === 200 || status === 201) { this.counts.delivered += 1; return; }
        if (status !== 429 && status < 500) {
          this.counts.failed += 1; this.reportFailure({ reason: 'request_rejected', status, attempts: attempt }); return;
        }
      } catch {
        // Never expose URLs, response bodies, credentials or transport errors.
        status = null;
      }
      if (attempt < this.maxAttempts) await new Promise(resolve => setTimeout(resolve, Math.min(100 * 2 ** (attempt - 1), 1000)));
    }
    this.counts.failed += 1;
    this.reportFailure({ reason: 'delivery_exhausted', status, attempts: this.maxAttempts });
  }

  async drain() {
    this.running = true;
    try { while (this.queue.length) await this.deliver(this.queue.shift()); }
    finally {
      this.running = false;
      for (const resolve of this.waiters.splice(0)) resolve(this.snapshot());
    }
  }

  flush() {
    return this.running || this.queue.length ? new Promise(resolve => this.waiters.push(resolve)) : Promise.resolve(this.snapshot());
  }
}
