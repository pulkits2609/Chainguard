import { useEffect, useState } from 'react';
import { apiRequest } from '../api.js';

export function LedgerRows({ ledgers, onOpen, children }) {
  return <div className="ledger-list">{ledgers.map(ledger => <article className="ledger-entry" key={ledger.id}>
    <div><h2>{ledger.name}</h2><p>{ledger.description || 'No description'}</p><small>{ledger.visibility || 'public'} · {ledger.state === 'read_only' ? 'Read-only' : ledger.state} · {ledger.block_count} blocks</small></div>
    <div className="entry-actions"><button onClick={() => onOpen(ledger.id)}>View chain</button>{children?.(ledger)}</div>
  </article>)}</div>;
}

export default function LedgerList({ onOpen }) {
  const [ledgers, setLedgers] = useState([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    apiRequest('/api/ledgers', { signal: controller.signal }).then(result => {
      setLedgers(result.ledgers); setError('');
    }).catch(requestError => {
      if (!controller.signal.aborted) setError(requestError.message);
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [refresh]);

  return <section>
    <header className="toolbar"><h1>Public ledgers</h1><button disabled={busy} onClick={() => setRefresh(value => value + 1)}>Refresh</button></header>
    <p className="muted">Only publicly visible ledgers appear here. Private shipment histories require an authorized access grant.</p>
    {error && <p className="notice warning" role="alert">{error}</p>}
    {busy && <p>Loading ledgers…</p>}
    <LedgerRows ledgers={ledgers} onOpen={onOpen}/>
    {!busy && !ledgers.length && <p className="empty-chain">No ledgers have been created yet.</p>}
  </section>;
}

