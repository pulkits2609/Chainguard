import useApiData from '../useApiData.js';

export default function Overview({ onOpen, isAdmin }) {
  const { data, busy, error, refresh } = useApiData('/api/overview');
  const metrics = [['Total ledgers', 'total_ledgers'], ['Active ledgers', 'active_ledgers'],
    ['Recorded blocks', 'total_blocks'], ['Configured integrations', 'configured_integrations']];
  return <section>
    <header className="toolbar"><div><p className="eyebrow">Secure · Record · Verify</p><h1>Operational overview</h1></div><button disabled={busy} onClick={refresh}>Refresh</button></header>
    <p className="muted">Live database values for ledgers you are authorized to manage. Integrity checks describe recorded history, not real-world delivery.</p>
    {error && <p className="notice warning" role="alert">{error}</p>}
    {busy && <p role="status">Loading operational status…</p>}
    {data && <>
      <div className="metric-grid">{metrics.map(([label, key]) => <article className="metric" key={key}><span>{label}</span><strong>{data[key] ?? 'Unavailable'}</strong></article>)}</div>
      {isAdmin && <div className="metric-grid compact"><article className="metric"><span>Recent rejected API requests</span><strong>{data.recent_rejected_requests ?? 'Unavailable'}</strong></article><article className="metric"><span>Recent verification failures</span><strong>{data.recent_verification_failures ?? 'Unavailable'}</strong></article></div>}
      <section className="form-panel"><h2>Ledger integrity</h2><p className="muted">Hash consistency is checked on the server. Trusted exported checkpoints provide evidence against a later rewrite.</p>
        <div className="ledger-list">{(data.ledger_statuses || []).map(ledger => <article className="ledger-entry" key={ledger.id}><div><h3>{ledger.name}</h3><span className={`status-pill ${ledger.valid ? 'good' : 'bad'}`}>{ledger.valid ? 'Internally consistent' : 'Verification failed'}</span><p>{ledger.checked_blocks ?? 'Unknown'} blocks checked</p></div><button onClick={() => onOpen(ledger.id)}>Inspect ledger</button></article>)}</div>
        {!data.ledger_statuses?.length && <p className="muted">No ledgers to verify yet.</p>}
      </section>
    </>}
  </section>;
}
