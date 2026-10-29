// Preload middleware for the existing AudioShare Node HTTP application.
// No proxy and no source-code edits: observe its actual successful response event.
import { Server } from 'node:http';

const Identity = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MaximumCapture = 16384;

export function observeSession(req, res, forwarder) {
  if (req.method !== 'POST' || req.url !== '/v1/session') return;
  const chunks = [];
  let size = 0;
  function capture(chunk, encoding) {
    if (chunk === undefined || chunk === null || typeof chunk === 'function' || size > MaximumCapture) return;
    const codec = typeof encoding === 'string' ? encoding : 'utf8';
    const length = typeof chunk === 'string' ? Buffer.byteLength(chunk, codec) : chunk.byteLength;
    if (!Number.isSafeInteger(length) || length < 0) { size = MaximumCapture + 1; chunks.length = 0; return; }
    size += length;
    if (size > MaximumCapture) chunks.length = 0;
    else chunks.push(typeof chunk === 'string' ? Buffer.from(chunk, codec) : Buffer.from(chunk));
  }
  const write = res.write, end = res.end;
  res.write = function (...args) { capture(args[0], args[1]); return Reflect.apply(write, this, args); };
  res.end = function (...args) { capture(args[0], args[1]); return Reflect.apply(end, this, args); };
  res.once('finish', () => {
    try {
      if (res.statusCode !== 200 || size > MaximumCapture) return;
      const response = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!Identity.test(response.identity) || !response.voiceLobby || !Number.isSafeInteger(response.voiceLobby.groupId)) return;
      forwarder.enqueue({ event_type: 'SESSION_CREATED', source_system: 'audioShare', resource_id: response.identity,
        event_time: new Date().toISOString() });
    } catch {
      // Invalid/oversized source results never become audit records or log bodies.
    } finally { chunks.length = 0; }
  });
  res.once('close', () => { chunks.length = 0; });
}

export function installAudioShareEvents(forwarder, serverType = Server) {
  const emit = serverType.prototype.emit;
  function observedEmit(name, ...args) {
    if (name === 'request') observeSession(args[0], args[1], forwarder);
    return Reflect.apply(emit, this, [name, ...args]);
  }
  serverType.prototype.emit = observedEmit;
  return () => { if (serverType.prototype.emit === observedEmit) serverType.prototype.emit = emit; };
}

