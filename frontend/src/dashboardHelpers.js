export function formatTime(value) {
  if (value === null || value === undefined || value === '') return 'Not available';
  const timestamp = typeof value === 'number' && value < 100000000000 ? value * 1000 : value;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? 'Invalid timestamp' : date.toLocaleString();
}

export function customerLocation(location) {
  const shipment = new URLSearchParams(location.search).get('shipment');
  if (!shipment) return null;
  return { shipment, token: new URLSearchParams(location.hash.slice(1)).get('token') || '' };
}

export function customerLink(shipment, token, origin = window.location.origin) {
  return `${origin}/?shipment=${encodeURIComponent(shipment)}#token=${encodeURIComponent(token)}`;
}

export function localDateTime(value) {
  if (!value) return '';
  const date = new Date(typeof value === 'number' && value < 100000000000 ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function expiryTimestamp(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error('Choose a valid expiration time.');
  return date.toISOString();
}

export function policyPayload(values) {
  const requests = Number(values.requests_per_minute);
  const bytes = Number(values.max_body_bytes);
  if (!Number.isSafeInteger(requests) || requests < 1) throw new Error('Requests per minute must be a positive whole number.');
  if (!Number.isSafeInteger(bytes) || bytes < 1 || bytes > 65536) throw new Error('Body limit must be between 1 and 65,536 bytes.');
  return { enabled: values.enabled, requests_per_minute: requests,
    allowed_operations: [...values.allowed_operations],
    allowed_source_cidrs: values.allowed_source_cidrs.split(/[\n,]/).map(item => item.trim()).filter(Boolean),
    max_body_bytes: bytes, credential_expires_at: expiryTimestamp(values.credential_expires_at),
    require_authentication: true };
}

export function webhookExample(integration, fields, timestamp = new Date().toISOString()) {
  const payload = {};
  function assign(path, value) {
    const segments = path.split('.');
    let current = payload;
    for (const segment of segments.slice(0, -1)) {
      if (!Object.hasOwn(current, segment) || typeof current[segment] !== 'object' || current[segment] === null) {
        Object.defineProperty(current, segment, { value: {}, enumerable: true, configurable: true, writable: true });
      }
      current = current[segment];
    }
    Object.defineProperty(current, segments.at(-1), { value, enumerable: true, configurable: true, writable: true });
  }
  for (const field of fields || []) {
    const path = integration.field_mapping?.[field.name];
    if (!path) continue;
    const values = { string: `REPLACE_${field.name.toUpperCase()}`, integer: 1, boolean: true, object: {}, array: [] };
    assign(path, values[field.type]);
  }
  assign(integration.event_id_field, 'REPLACE_WITH_STABLE_SOURCE_EVENT_ID');
  assign(integration.timestamp_field, timestamp);
  return payload;
}

export function saveJson(value, filename) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// This is the checkpoint format only; it never changes the ledger's block hashing.
export function checkpointSignedBytes(checkpoint) {
  const fields = ['checkpoint_id', 'created_at_ms', 'format_version', 'hash', 'index', 'key_id', 'ledger_id'];
  if (!checkpoint || checkpoint.format_version !== 1 || !Number.isSafeInteger(checkpoint.index) || checkpoint.index < 0
      || !Number.isSafeInteger(checkpoint.created_at_ms) || checkpoint.created_at_ms < 0
      || !/^[a-f0-9]{64}$/.test(checkpoint.hash)
      || fields.some(field => !Object.hasOwn(checkpoint, field))
      || ['checkpoint_id', 'key_id', 'ledger_id'].some(field => typeof checkpoint[field] !== 'string' || !checkpoint[field])
      || Object.keys(checkpoint).some(field => ![...fields, 'signature'].includes(field))) {
    throw new Error('Unsupported or malformed checkpoint.');
  }
  const unsigned = Object.fromEntries(fields.map(field => [field, checkpoint[field]]));
  return new TextEncoder().encode(JSON.stringify(unsigned));
}

export async function verifyCheckpointSignature(checkpoint, trustedKey) {
  if (!trustedKey || trustedKey.format_version !== 1 || trustedKey.algorithm !== 'Ed25519' || trustedKey.key_id !== checkpoint?.key_id) {
    throw new Error('The independently trusted key does not match this checkpoint.');
  }
  if (!globalThis.crypto?.subtle) throw new Error('This browser needs a secure context for signature verification. Use the offline verifier instead.');
  const decode = value => Uint8Array.from(atob(value), character => character.charCodeAt(0));
  const bytes = checkpointSignedBytes(checkpoint);
  const raw = decode(trustedKey.public_key);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', raw));
  const keyId = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('');
  if (keyId !== trustedKey.key_id) throw new Error('The key identifier does not match the supplied public key.');
  const key = await crypto.subtle.importKey('raw', raw, { name: 'Ed25519' }, false, ['verify']);
  return crypto.subtle.verify('Ed25519', key, decode(checkpoint.signature), bytes);
}
