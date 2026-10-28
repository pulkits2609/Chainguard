import { useEffect, useState } from 'react';

let csrfToken = '';
const PollDelay = 1500;

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
    throw new Error(body.error || `Request failed (${response.status}).`);
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
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let stopped = false;
    let timer;
    let controller;
    let requestTimeout;
    setLedger(null);
    setConnection('loading');
    setError('');
    setLastReceived(null);
    if (!id) return undefined;

    async function readLedger() {
      controller = new AbortController();
      requestTimeout = window.setTimeout(() => controller.abort(), 5000);
      try {
        const result = await apiRequest(ledgerPath(id), { signal: controller.signal });
        if (!Array.isArray(result.blocks) || typeof result.verification?.valid !== 'boolean') {
          throw new Error('The server returned an incomplete ledger response.');
        }
        if (stopped) return;
        setLedger(result);
        setConnection('online');
        setError('');
        setLastReceived(new Date());
      } catch (requestError) {
        if (stopped) return;
        setConnection('offline');
        setError(requestError.name === 'AbortError' ? 'The server did not respond within five seconds.' : requestError.message);
      } finally {
        window.clearTimeout(requestTimeout);
        // Wait for this request before scheduling another poll.
        if (!stopped) timer = window.setTimeout(readLedger, PollDelay);
      }
    }
    readLedger();
    return () => {
      stopped = true;
      controller?.abort();
      window.clearTimeout(requestTimeout);
      window.clearTimeout(timer);
    };
  }, [id, refreshKey]);

  return { ledger, connection, error, lastReceived, refresh: () => setRefreshKey(value => value + 1) };
}
