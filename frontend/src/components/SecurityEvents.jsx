import { useState } from 'react';
import useApiData from '../useApiData.js';
import { formatTime } from '../dashboardHelpers.js';

const PageSize = 25;

export default function SecurityEvents() {
  const [filters, setFilters] = useState({ severity: '', category: '', integration_id: '', ledger_id: '', since: '', until: '' });
  const [applied, setApplied] = useState('');
  const [offset, setOffset] = useState(0);
  const query = new URLSearchParams(applied);
  query.set('limit', PageSize); query.set('offset', offset);
  const { data, busy, error, refresh } = useApiData(`/api/security/events?${query}`);
  function submit(event) {
    event.preventDefault();
    const values = Object.fromEntries(Object.entries(filters).filter(([, value]) => value));
    if (values.since) { values.from = new Date(values.since).getTime(); delete values.since; }
    if (values.until) { values.to = new Date(values.until).getTime(); delete values.until; }
    setOffset(0); setApplied(new URLSearchParams(values).toString());
  }
  const rows = data?.events || [];
  return <section>
    <header className="toolbar"><h1>Security events</h1><button onClick={refresh} disabled={busy}>Refresh</button></header>
    <p className="muted">Administrative telemetry with sanitized reason codes. Retention is configured on the server.</p>
    <form className="filter-grid form-panel" onSubmit={submit}>
      <label>Severity<select value={filters.severity} onChange={event => setFilters({ ...filters, severity: event.target.value })}><option value="">All severities</option>{['info', 'warning', 'error', 'critical'].map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Category<input maxLength={80} value={filters.category} onChange={event => setFilters({ ...filters, category: event.target.value })}/></label>
      <label>Integration ID<input maxLength={128} value={filters.integration_id} onChange={event => setFilters({ ...filters, integration_id: event.target.value })}/></label>
      <label>Ledger ID<input maxLength={128} value={filters.ledger_id} onChange={event => setFilters({ ...filters, ledger_id: event.target.value })}/></label>
      <label>From<input type="datetime-local" value={filters.since} onChange={event => setFilters({ ...filters, since: event.target.value })}/></label>
      <label>To<input type="datetime-local" value={filters.until} onChange={event => setFilters({ ...filters, until: event.target.value })}/></label>
      <div className="button-row"><button className="primary" disabled={busy}>Apply filters</button><button type="button" onClick={() => { setFilters({ severity: '', category: '', integration_id: '', ledger_id: '', since: '', until: '' }); setApplied(''); setOffset(0); }}>Clear</button></div>
    </form>
    {error && <p className="notice warning" role="alert">{error}</p>}
    {busy && <p role="status">Reading security events…</p>}
    <div className="table-scroll"><table><caption>Security audit history</caption><thead><tr><th>Time</th><th>Category / severity</th><th>Integration / ledger</th><th>Operation</th><th>Outcome</th><th>Reason</th></tr></thead><tbody>{rows.map(event => <tr key={event.event_id || event.id}><td>{formatTime(event.timestamp_ms ?? event.timestamp ?? event.created_at)}</td><td>{event.category}<br/><span className={`status-pill ${event.severity === 'error' || event.severity === 'critical' ? 'bad' : ''}`}>{event.severity}</span></td><td className="mono">{event.integration_id || '—'}<br/>{event.ledger_id || '—'}</td><td>{event.operation}</td><td>{event.outcome}</td><td>{event.reason_code}</td></tr>)}</tbody></table></div>
    {!busy && !rows.length && <p className="empty-chain">No events match these filters.</p>}
    <div className="pagination"><button disabled={busy || offset === 0} onClick={() => setOffset(value => Math.max(0, value - PageSize))}>Previous</button><span>Page {offset / PageSize + 1}</span><button disabled={busy || !data?.has_more} onClick={() => setOffset(value => value + PageSize)}>Next</button></div>
  </section>;
}
