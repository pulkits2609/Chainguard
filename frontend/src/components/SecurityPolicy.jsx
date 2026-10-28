import { useState } from 'react';
import { apiRequest } from '../api.js';
import useApiData from '../useApiData.js';
import { localDateTime, policyPayload } from '../dashboardHelpers.js';

const Operations = ['append', 'read', 'verify', 'snapshot_export'];

function PolicyForm({ integration, initial, onSaved }) {
  const [values, setValues] = useState({ enabled: initial.enabled, requests_per_minute: initial.requests_per_minute,
    allowed_operations: initial.allowed_operations || [], allowed_source_cidrs: (initial.allowed_source_cidrs || []).join('\n'),
    max_body_bytes: initial.max_body_bytes, credential_expires_at: localDateTime(initial.credential_expires_at) });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  function field(name, value) { setNotice(''); setValues(current => ({ ...current, [name]: value })); }
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      await apiRequest(`/api/integrations/${integration.id}/policy`, { method: 'PATCH', body: JSON.stringify(policyPayload(values)) });
      setNotice('Security policy saved and active.'); onSaved();
    } catch (requestError) { setError(requestError.message); } finally { setBusy(false); }
  }
  return <form className="form-panel" onSubmit={submit}><h2>{integration.name}</h2><p className="muted">Effective access is the intersection of credential permissions and this policy. Global safety limits remain enforced.</p>
    <div className="form-grid"><label className="check-label"><input type="checkbox" checked={values.enabled} onChange={event => field('enabled', event.target.checked)}/>Policy enabled</label>
      <label>Requests per minute<input required type="number" min="1" step="1" value={values.requests_per_minute} onChange={event => field('requests_per_minute', event.target.value)}/></label>
      <label>Maximum body bytes<input required type="number" min="1" max="65536" step="1" value={values.max_body_bytes} onChange={event => field('max_body_bytes', event.target.value)}/></label>
      <label>Credential expiration (local time)<input type="datetime-local" value={values.credential_expires_at} onChange={event => field('credential_expires_at', event.target.value)}/><small>Leave empty for no configured expiration.</small></label>
    </div>
    <fieldset><legend>Allowed operations</legend><div className="button-row">{Operations.map(operation => <label key={operation} className="check-label"><input type="checkbox" checked={values.allowed_operations.includes(operation)} disabled={!integration.credential_scopes?.includes(operation) && !values.allowed_operations.includes(operation)} onChange={event => field('allowed_operations', event.target.checked ? [...values.allowed_operations, operation] : values.allowed_operations.filter(value => value !== operation))}/>{operation}</label>)}</div></fieldset>
    <label>Allowed source IP addresses / CIDRs<textarea placeholder="127.0.0.1/32\n2001:db8::/32" value={values.allowed_source_cidrs} onChange={event => field('allowed_source_cidrs', event.target.value)}/><small>One entry per line. Empty allows any verified source IP, subject to authentication and other controls.</small></label>
    <p className="muted">Authentication is mandatory. Policy settings cannot enable anonymous writes.</p>
    {error && <p className="inline-error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}
    <button className="primary" disabled={busy}>{busy ? 'Saving…' : 'Save security policy'}</button>
  </form>;
}

function SelectedPolicy({ integration, onSaved }) {
  const { data, busy, error } = useApiData(`/api/integrations/${integration.id}/policy`);
  return <>{busy && <p role="status">Loading security policy…</p>}{error && <p className="notice warning" role="alert">{error}</p>}{data && <PolicyForm key={integration.id} integration={integration} initial={data.policy} onSaved={onSaved}/>}</>;
}

export default function SecurityPolicy() {
  const { data, busy, error, refresh } = useApiData('/api/integrations');
  const [selectedId, setSelectedId] = useState('');
  const integrations = data?.integrations || [];
  const selected = integrations.find(item => item.id === selectedId);
  return <section><header className="toolbar"><h1>API security</h1><button disabled={busy} onClick={refresh}>Refresh integrations</button></header><p className="muted">Configure restrictions on ChainGuard ingestion APIs. These settings do not proxy or protect unrelated services.</p>
    {error && <p className="notice warning" role="alert">{error}</p>}
    <label className="section-select">Integration<select value={selectedId} onChange={event => setSelectedId(event.target.value)}><option value="">Select an integration</option>{integrations.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    {busy && <p role="status">Loading integrations…</p>}
    {selected && <SelectedPolicy key={selected.id} integration={selected} onSaved={() => {}}/>}
    {!busy && !integrations.length && <p className="empty-chain">Create an integration to configure its API policy.</p>}
  </section>;
}
