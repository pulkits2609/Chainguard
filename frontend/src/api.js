import { useEffect, useRef, useState } from 'react';
import { retryAfterMilliseconds, startLedgerPolling } from './ledgerPolling.js';

let csrfToken = '';

export async function apiRequest(path, options = {}) {
  const method = options.method || 'GET';
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (csrfToken && method !== 'GET') headers['X-CSRF-Token'] = csrfToken;
  const response = await fetch(path, { ...options, method, headers, credentials: 'same-origin',
    signal: options.signal || AbortSignal.timeout(15000) });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error('The server did not return JSON. Check that the ledger service is running.');
  }
  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('session-expired'));
    const error = new Error(body.error || `Request failed (${response.status}).`);
    error.status = response.status;
    error.retryAfterMs = retryAfterMilliseconds(response.headers?.get('Retry-After'));
    throw error;
  }
  if (Object.hasOwn(body, 'csrf_token')) csrfToken = body.csrf_token || '';
  if (path === '/api/logout') csrfToken = '';
  return body;
}

export function ledgerPath(id) {
  return `/api/ledgers/${encodeURIComponent(id)}`;
}

export async function customerRequest(shipment, operation, token, options = {}) {
  if (!['events', 'proof'].includes(operation)) throw new Error('Unsupported customer operation.');
  const query = options.query ? `?${options.query}` : '';
  const response = await fetch(`/api/customer/shipments/${encodeURIComponent(shipment)}/${operation}${query}`, {
    method: 'GET', credentials: 'omit', headers: { Authorization: `Bearer ${token}` },
    signal: options.signal || AbortSignal.timeout(15000),
  });
  const body = await response.json().catch(() => { throw new Error('The shipment service did not return JSON.'); });
  if (!response.ok) {
    const error = new Error(body.error || `Shipment access failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return body;
}

export async function downloadSnapshot(ledgerId, blockIndex) {
  let path = `${ledgerPath(ledgerId)}/snapshot`;
  if (blockIndex !== undefined) path += `?block=${blockIndex}`;
  const response = await fetch(path, { credentials: 'same-origin' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || 'The snapshot could not be downloaded.');
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = blockIndex === undefined ? `ledger-${ledgerId}.ledger` : `ledger-${ledgerId}-block-${blockIndex}.ledger`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function useLedger(id) {
  const [ledger, setLedger] = useState(null);
  const [connection, setConnection] = useState('loading');
  const [error, setError] = useState('');
  const [lastReceived, setLastReceived] = useState(null);
  const polling = useRef(null);

  useEffect(() => {
    setLedger(null);
    setConnection('loading');
    setError('');
    setLastReceived(null);
    if (!id) return undefined;

    const active = startLedgerPolling({
      read: async signal => {
        const result = await apiRequest(ledgerPath(id), { signal });
        if (!Array.isArray(result.blocks) || typeof result.verification?.valid !== 'boolean') {
          throw new Error('The server returned an incomplete ledger response.');
        }
        return result;
      },
      onValue: result => {
        setLedger(result);
        setConnection('online');
        setError('');
        setLastReceived(new Date());
      },
      onError: requestError => {
        setConnection('offline');
        setError(requestError.name === 'AbortError' ? 'The server did not respond within five seconds.' : requestError.message);
      },
    });
    polling.current = active;
    return () => {
      active.stop();
      if (polling.current === active) polling.current = null;
    };
  }, [id]);

  return { ledger, connection, error, lastReceived, refresh: () => polling.current?.refresh() };
}
