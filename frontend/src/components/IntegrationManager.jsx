import { useEffect, useState } from 'react';
import { apiRequest } from '../api.js';
import useApiData from '../useApiData.js';
import { formatTime, webhookExample } from '../dashboardHelpers.js';

const Operations = ['append', 'read', 'verify', 'snapshot_export'];

function IntegrationForm({ ledgers, existing, onSaved, onClose }) {
  const [ledgerId, setLedgerId] = useState(existing?.ledger_id || ledgers[0]?.id || '');
  const [name, setName] = useState(existing?.name || '');
  const [authMethod, setAuthMethod] = useState(existing?.auth_method || 'bearer');
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [eventId, setEventId] = useState(existing?.event_id_field || 'event_id');
  const [timestamp, setTimestamp] = useState(existing?.timestamp_field || 'event_time');
  const [signatureHeader, setSignatureHeader] = useState(existing?.signature_header || 'X-ChainGuard-Signature');
  const [mapping, setMapping] = useState(existing?.field_mapping || {});
  const [scopes, setScopes] = useState(existing?.credential_scopes || ['append']);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const ledger = ledgers.find(item => item.id === ledgerId);
  useEffect(() => {
    if (!existing && ledger) setMapping(Object.fromEntries(ledger.fields.map(field => [field.name, field.name])));
  }, [ledgerId, existing]);
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('');
    const value = { name, enabled, field_mapping: Object.fromEntries(Object.entries(mapping).filter(([, source]) => source.trim())),
      event_id_field: eventId, timestamp_field: timestamp, signature_header: signatureHeader };
    if (!existing) Object.assign(value, { ledger_id: ledgerId, auth_method: authMethod, credential_scopes: scopes });
    try {
      onSaved(await apiRequest(existing ? `/api/integrations/${existing.id}` : '/api/integrations', {
        method: existing ? 'PATCH' : 'POST', body: JSON.stringify(value),
      }));
    } catch (requestError) { setError(requestError.message); } finally { setBusy(false); }
  }
  return <section className="form-panel"><div className="details-heading"><h2>{existing ? 'Edit integration' : 'Create integration'}</h2><button disabled={busy} onClick={onClose}>Close</button></div>
    <p className="muted">Map incoming JSON fields to the existing ledger schema. Dotted paths select nested fields; mapping never runs code.</p>
    <form onSubmit={submit}>
      <div className="form-grid"><label>Name<input required maxLength={80} value={name} onChange={event => setName(event.target.value)}/></label>
        <label>Target ledger<select required disabled={Boolean(existing)} value={ledgerId} onChange={event => setLedgerId(event.target.value)}><option value="" disabled>Select a ledger</option>{ledgers.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>Authentication<select disabled={Boolean(existing)} value={authMethod} onChange={event => setAuthMethod(event.target.value)}><option value="bearer">Bearer token</option><option value="hmac">HMAC-SHA256</option></select></label>
        <label className="check-label"><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)}/>Integration enabled</label>
        <label>Event ID source field<input required maxLength={200} value={eventId} onChange={event => setEventId(event.target.value)}/></label>
        <label>Signed timestamp source field<input required maxLength={200} value={timestamp} onChange={event => setTimestamp(event.target.value)}/></label>
        {authMethod === 'hmac' && <label>Signature header<input required maxLength={80} value={signatureHeader} onChange={event => setSignatureHeader(event.target.value)}/></label>}
      </div>
      <fieldset><legend>Payload mapping</legend>{(ledger?.fields || []).map(field => <label className="mapping-row" key={field.name}><span><code>{field.name}</code> · {field.type}{field.required ? ' · required' : ''}</span><input required={field.required} maxLength={200} placeholder="source.path" value={mapping[field.name] || ''} onChange={event => setMapping(current => ({ ...current, [field.name]: event.target.value }))}/></label>)}</fieldset>
      {!existing && <fieldset><legend>Credential permissions</legend><p className="muted">Policies may restrict these permissions further. A policy cannot grant permissions absent from the credential.</p><div className="button-row">{Operations.map(operation => <label className="check-label" key={operation}><input type="checkbox" checked={scopes.includes(operation)} onChange={event => setScopes(current => event.target.checked ? [...current, operation] : current.filter(item => item !== operation))}/>{operation}</label>)}</div></fieldset>}
      {authMethod === 'hmac' && <p className="notice">ChainGuard signs the exact raw request bytes with HMAC-SHA256. The configured timestamp must be inside that signed JSON body. The signature header uses <code>sha256=&lt;hex digest&gt;</code>. Provider-specific protocols need matching documented configuration.</p>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      <button className="primary" disabled={busy || !ledgerId || (!existing && !scopes.length)}>{busy ? 'Saving…' : existing ? 'Save integration' : 'Create integration'}</button>
    </form>
  </section>;
}

export default function IntegrationManager() {
  const integrations = useApiData('/api/integrations');
  const ledgers = useApiData('/api/my-ledgers');
  const [editing, setEditing] = useState(null);
  const [secret, setSecret] = useState(null);
  const [busyId, setBusyId] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  function saved(result) { setEditing(null); setSecret(result.secret ? result : null); setNotice('Integration saved.'); integrations.refresh(); }
  async function action(integration, operation) {
    if (operation === 'rotate-secret' && !window.confirm('Rotate this integration secret? Existing producers must use the new secret immediately.')) return;
    if (operation === 'delete' && !window.confirm('Remove this integration? Its credential will stop working.')) return;
    setBusyId(integration.id); setError(''); setNotice(''); setSecret(null);
    try {
      const result = await apiRequest(`/api/integrations/${integration.id}${operation === 'rotate-secret' ? '/rotate-secret' : ''}`, {
        method: operation === 'rotate-secret' ? 'POST' : operation === 'delete' ? 'DELETE' : 'PATCH',
        body: operation === 'toggle' ? JSON.stringify({ enabled: !integration.enabled }) : '{}',
      });
      if (result.secret) setSecret(result);
      setNotice(operation === 'rotate-secret' ? 'Secret rotated. Update the producer before sending more events.' : 'Integration updated.');
      integrations.refresh();
    } catch (requestError) { setError(requestError.message); } finally { setBusyId(''); }
  }
  async function copy(value) {
    try { await navigator.clipboard.writeText(value); setNotice('Copied to clipboard.'); }
    catch { setError('Clipboard access failed. Select and copy the displayed text manually.'); }
  }
  const rows = integrations.data?.integrations || [];
  return <section>
    <header className="toolbar"><h1>Integrations</h1><div className="button-row"><button disabled={integrations.busy} onClick={integrations.refresh}>Refresh</button><button className="primary" disabled={ledgers.busy || !ledgers.data?.ledgers?.length} onClick={() => { setSecret(null); setEditing('new'); }}>Create integration</button></div></header>
    <p className="muted">Connect independently running applications using authenticated REST requests or webhooks. Accepted events are committed to the selected ledger.</p>
    {(error || integrations.error || ledgers.error) && <p className="notice warning" role="alert">{error || integrations.error || ledgers.error}</p>}
    {notice && <p role="status" className="notice">{notice}</p>}
    {secret && <section className="form-panel"><div className="details-heading"><h2>Save the new secret</h2><button onClick={() => setSecret(null)}>Dismiss secret</button></div><p className="notice warning">Shown only now. Store it in the producing application's protected configuration. It will not be shown again.</p><label>Integration secret<textarea readOnly spellCheck="false" value={secret.secret}/></label></section>}
    {editing && <IntegrationForm key={editing.id || 'new'} existing={editing === 'new' ? null : editing} ledgers={ledgers.data?.ledgers || []} onSaved={saved} onClose={() => setEditing(null)}/>}
    {integrations.busy && <p role="status">Loading integrations…</p>}
    {!ledgers.busy && !ledgers.data?.ledgers?.length && <p className="empty-chain">Create a ledger before registering an integration.</p>}
    <div className="ledger-list">{rows.map(integration => {
      const endpoint = new URL(integration.webhook_path || `/api/integrations/${integration.id}/webhook`, window.location.origin).href;
      const ledger = ledgers.data?.ledgers?.find(item => item.id === integration.ledger_id);
      const payload = webhookExample(integration, ledger?.fields);
      const example = `POST ${endpoint}\nContent-Type: application/json\n${integration.auth_method === 'bearer' ? 'Authorization: Bearer YOUR_INTEGRATION_SECRET' : `${integration.signature_header}: sha256=HEX_HMAC_OF_EXACT_RAW_BODY`}\n\n${JSON.stringify(payload, null, 2)}`;
      return <article className="form-panel" key={integration.id}>
        <div className="details-heading"><h2>{integration.name}</h2><span className={`status-pill ${integration.enabled ? 'good' : ''}`}>{integration.enabled ? 'Enabled' : 'Disabled'}</span></div>
        <dl className="metadata-grid"><div><dt>Ledger</dt><dd>{ledgers.data?.ledgers?.find(ledger => ledger.id === integration.ledger_id)?.name || integration.ledger_id}</dd></div><div><dt>Authentication</dt><dd>{integration.auth_method}</dd></div><div><dt>Created</dt><dd>{formatTime(integration.created_at)}</dd></div><div><dt>Last accepted event</dt><dd>{formatTime(integration.last_accepted_at)}</dd></div><div><dt>Credential permissions</dt><dd>{integration.credential_scopes?.join(', ') || 'None'}</dd></div></dl>
        <label>Webhook endpoint<input readOnly value={endpoint}/></label>
        <div className="button-row action-row"><button disabled={Boolean(busyId)} onClick={() => { setSecret(null); setEditing(integration); }}>Edit</button><button disabled={Boolean(busyId)} onClick={() => action(integration, 'toggle')}>{integration.enabled ? 'Disable' : 'Enable'}</button><button disabled={Boolean(busyId)} onClick={() => action(integration, 'rotate-secret')}>Rotate secret</button><button onClick={() => copy(endpoint)}>Copy endpoint</button><button onClick={() => copy(example)}>Copy request example</button><button className="danger" disabled={Boolean(busyId)} onClick={() => action(integration, 'delete')}>Remove</button></div>
        <details><summary>Request example and mapping</summary><p className="muted">Replace example values with genuine source-application data. This example is documentation, not recorded shipment activity.</p><pre>{example}</pre><pre>{JSON.stringify({ field_mapping: integration.field_mapping, event_id_field: integration.event_id_field, timestamp_field: integration.timestamp_field }, null, 2)}</pre>{integration.auth_method === 'bearer' && <p className="muted">REST producers may also use the existing ledger block endpoint with this bearer credential when its policy permits append. Keep event identifiers stable across retries.</p>}</details>
      </article>;
    })}</div>
    {!integrations.busy && !rows.length && <p className="empty-chain">No integrations configured yet.</p>}
  </section>;
}
