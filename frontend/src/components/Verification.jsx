import { useState } from 'react';
import { apiRequest, ledgerPath } from '../api.js';
import useApiData from '../useApiData.js';
import { customerLink, expiryTimestamp, formatTime, saveJson, verifyCheckpointSignature } from '../dashboardHelpers.js';

function VerificationResult({ result }) {
  if (!result) return null;
  return <section className={`notice ${result.valid === false ? 'warning' : ''}`} role="status"><strong>{result.valid === false ? 'Verification failed' : 'Verification completed'}</strong>
    {result.checked_blocks !== undefined && <p>{result.checked_blocks} blocks checked</p>}
    {result.head_hash && <p className="mono">Head: {result.head_hash}</p>}
    {result.signature_valid !== undefined && <p>Checkpoint signature: {result.signature_valid ? 'verified' : 'failed'}</p>}
    {result.prefix_verified && <p>Checkpointed prefix: verified through block #{result.anchored_prefix_index} ({result.anchored_prefix_count} blocks, including genesis).</p>}
    {result.unanchored_suffix_count > 0 && <p>{result.unanchored_suffix_count} later blocks are internally consistent but are not anchored by this checkpoint.</p>}
    {result.history_verified !== undefined && <p>Full-record history: {result.history_verified ? 'all supplied records matched the checkpoint in this server check' : 'not fully anchored by this checkpoint'}</p>}
    {(result.errors || []).length > 0 && <ul>{result.errors.map((error, index) => <li key={index}>{error.index !== undefined ? `Block ${error.index ?? 'unknown'}: ` : ''}{error.message}</li>)}</ul>}
  </section>;
}

function ShipmentGrants({ ledgerId }) {
  const [offset, setOffset] = useState(0);
  const grants = useApiData(`${ledgerPath(ledgerId)}/shipment-access?limit=25&offset=${offset}`);
  const [shipmentId, setShipmentId] = useState('');
  const [expires, setExpires] = useState('');
  const [created, setCreated] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function create(event) {
    event.preventDefault(); setBusy(true); setError(''); setCreated(null);
    try {
      const result = await apiRequest(`${ledgerPath(ledgerId)}/shipment-access`, { method: 'POST', body: JSON.stringify({ shipment_id: shipmentId, ...(expires ? { expires_at: expiryTimestamp(expires) } : {}) }) });
      setCreated(result); setOffset(0); grants.refresh();
    } catch (requestError) { setError(requestError.message); } finally { setBusy(false); }
  }
  async function revoke(grantId) {
    setBusy(true); setError('');
    try {
      await apiRequest(`${ledgerPath(ledgerId)}/shipment-access/${encodeURIComponent(grantId)}`, { method: 'DELETE', body: '{}' });
      if (created?.grant?.grant_id === grantId) setCreated(null);
      grants.refresh();
    } catch (requestError) { setError(requestError.message); } finally { setBusy(false); }
  }
  return <section className="form-panel"><h2>Customer shipment access</h2><p className="muted">Issue a read-only grant for one shipment. Share its link privately; anyone holding it can read that shipment until expiry or revocation.</p>
    <form onSubmit={create}><div className="form-grid"><label>Shipment ID<input required maxLength={128} value={shipmentId} onChange={event => setShipmentId(event.target.value)}/></label><label>Expiration (local time)<input type="datetime-local" value={expires} onChange={event => setExpires(event.target.value)}/><small>Leave empty to use the server's bounded default.</small></label></div><button disabled={busy} className="primary">{busy ? 'Working…' : 'Create shipment access grant'}</button></form>
    {(error || grants.error) && <p className="inline-error" role="alert">{error || grants.error}</p>}
    {created && <div className="secret-result"><p className="notice warning">Save this scoped access link now. It is shown once. Its fragment contains the access secret.</p><label>Customer access link<textarea readOnly spellCheck="false" value={customerLink(created.grant.shipment_id, created.access_token)}/></label><button onClick={() => setCreated(null)}>Dismiss access secret</button></div>}
    <div className="table-scroll"><table><caption>Recent access grants</caption><thead><tr><th>Shipment</th><th>Expiration</th><th>Status</th><th>Action</th></tr></thead><tbody>{(grants.data?.grants || []).map(grant => <tr key={grant.grant_id}><td>{grant.shipment_id}</td><td>{formatTime(grant.expires_at)}</td><td>{grant.revoked ? 'Revoked' : Number(grant.expires_at) * 1000 <= Date.now() ? 'Expired' : 'Active'}</td><td><button disabled={busy || grant.revoked} onClick={() => revoke(grant.grant_id)}>Revoke</button></td></tr>)}</tbody></table></div>
    {grants.data?.total > 25 && <div className="pagination"><button disabled={grants.busy || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 25))}>Previous grants</button><span>Page {offset / 25 + 1} · {grants.data.total} grants</span><button disabled={grants.busy || offset + 25 >= grants.data.total} onClick={() => setOffset(value => value + 25)}>Next grants</button></div>}
    {!grants.busy && !grants.data?.grants?.length && <p className="muted">No grants have been issued for this ledger.</p>}
  </section>;
}

function LedgerEvidence({ ledger }) {
  const [offset, setOffset] = useState(0);
  const checkpoints = useApiData(`${ledgerPath(ledger.id)}/signed-checkpoints?limit=25&offset=${offset}`);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [trustedKey, setTrustedKey] = useState(null);
  const [localResult, setLocalResult] = useState('');
  async function action(name, operation) {
    setBusy(name); setError('');
    try { await operation(); } catch (requestError) { setError(requestError.message); } finally { setBusy(''); }
  }
  async function createCheckpoint() {
    const response = await apiRequest(`${ledgerPath(ledger.id)}/signed-checkpoints`, { method: 'POST', body: '{}' });
    saveJson(response.checkpoint || response, `checkpoint-${ledger.id}.json`); setOffset(0); checkpoints.refresh();
  }
  async function exportKey(checkpoint) {
    const key = await apiRequest(`/api/verification-keys/${encodeURIComponent(checkpoint.key_id)}`);
    saveJson(key, `verification-key-${checkpoint.key_id}.json`);
  }
  async function importTrustedKey(event) {
    const file = event.target.files?.[0]; event.target.value = '';
    if (!file) return;
    try {
      if (file.size > 16384) throw new Error('Verification key file exceeds 16 KiB.');
      const key = JSON.parse(await file.text());
      if (key.algorithm !== 'Ed25519' || typeof key.public_key !== 'string' || typeof key.key_id !== 'string') throw new Error('Choose an Ed25519 verification-key JSON file.');
      setTrustedKey(key); setLocalResult('Trusted key loaded from the file you selected. Verify its fingerprint through a separate trusted channel.');
    } catch (requestError) { setError(requestError.message); }
  }
  async function checkUploaded(event) {
    const file = event.target.files?.[0]; event.target.value = '';
    if (!file) return;
    await action('uploaded', async () => {
      if (file.size > 65536) throw new Error('Checkpoint file exceeds 64 KiB.');
      const checkpoint = JSON.parse(await file.text());
      setResult(await apiRequest(`${ledgerPath(ledger.id)}/verify-checkpoint`, { method: 'POST', body: JSON.stringify({ checkpoint }) }));
    });
  }
  return <>
    <section className="form-panel"><h2>Integrity and evidence</h2><p className="muted">Server checks verify current ledger consistency. To independently verify full-record history, retain the exported history, checkpoint, and a separately trusted public key and use the standalone verifier.</p>
      <div className="button-row action-row"><button disabled={Boolean(busy)} onClick={() => action('verify', async () => setResult(await apiRequest(`${ledgerPath(ledger.id)}/verify`, { method: 'POST', body: '{}' })))}>Verify ledger</button><button disabled={Boolean(busy)} onClick={() => action('create', createCheckpoint)}>Create and download signed checkpoint</button><button disabled={Boolean(busy)} onClick={() => action('history', async () => saveJson(await apiRequest(`${ledgerPath(ledger.id)}/history-export`), `history-${ledger.id}.json`))}>Export full history</button><label className="file-button">Check checkpoint against server<input type="file" accept="application/json,.json" disabled={Boolean(busy)} onChange={checkUploaded}/></label></div>
      <p className="notice warning">Full-history exports contain private payloads. Keep them with authorized owners and investigators.</p>
      <VerificationResult result={result}/>
      {(error || checkpoints.error) && <p className="inline-error" role="alert">{error || checkpoints.error}</p>}
    </section>
    <section className="form-panel"><div className="details-heading"><h2>Signed checkpoint history</h2><button disabled={checkpoints.busy} onClick={checkpoints.refresh}>Refresh</button></div>
      <p className="muted">A valid signature authenticates a checkpoint under its signing key. A key downloaded from this server needs independent trust before it can establish independent evidence.</p>
      <label className="file-button">Load independently trusted public key<input type="file" accept="application/json,.json" onChange={importTrustedKey}/></label>
      {trustedKey && <p className="mono">Selected key: {trustedKey.key_id}</p>}{localResult && <p className="notice" role="status">{localResult}</p>}
      <div className="ledger-list">{(checkpoints.data?.checkpoints || []).map(checkpoint => <article className="checkpoint-row" key={checkpoint.checkpoint_id}><div><strong>Block #{checkpoint.index}</strong><p>{formatTime(checkpoint.created_at_ms)}</p><p className="mono">Key {checkpoint.key_id}</p><p className="mono">{checkpoint.hash}</p></div><div className="button-row"><button onClick={() => saveJson(checkpoint, `checkpoint-${checkpoint.checkpoint_id}.json`)}>Download</button><button disabled={Boolean(busy)} onClick={() => action('key', () => exportKey(checkpoint))}>Export public key</button><button disabled={!trustedKey || Boolean(busy)} onClick={() => action('signature', async () => { const valid = await verifyCheckpointSignature(checkpoint, trustedKey); setLocalResult(valid ? `Checkpoint signature for block #${checkpoint.index} verified using your supplied trusted key. This does not independently verify redacted shipment contents.` : 'Verification failed: checkpoint signature does not match the supplied key.'); })}>Verify signature locally</button></div></article>)}</div>
      {checkpoints.data?.total > 25 && <div className="pagination"><button disabled={checkpoints.busy || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 25))}>Previous checkpoints</button><span>Page {offset / 25 + 1} · {checkpoints.data.total} checkpoints</span><button disabled={checkpoints.busy || offset + 25 >= checkpoints.data.total} onClick={() => setOffset(value => value + 25)}>Next checkpoints</button></div>}
      {!checkpoints.busy && !checkpoints.data?.checkpoints?.length && <p className="muted">No signed checkpoints yet.</p>}
    </section>
    <ShipmentGrants ledgerId={ledger.id}/>
  </>;
}

export default function Verification() {
  const { data, busy, error } = useApiData('/api/my-ledgers');
  const [selectedId, setSelectedId] = useState('');
  const ledgers = data?.ledgers || [];
  const selected = ledgers.find(ledger => ledger.id === selectedId);
  return <section><header className="toolbar"><h1>Verification</h1></header><p className="muted">Verify recorded history, retain independent checkpoint evidence, and issue scoped customer access.</p>
    {error && <p className="notice warning" role="alert">{error}</p>}{busy && <p role="status">Loading authorized ledgers…</p>}
    <label className="section-select">Ledger<select value={selectedId} onChange={event => setSelectedId(event.target.value)}><option value="">Select a ledger</option>{ledgers.map(ledger => <option key={ledger.id} value={ledger.id}>{ledger.name}</option>)}</select></label>
    {selected && <LedgerEvidence key={selected.id} ledger={selected}/>}
    {!busy && !ledgers.length && <p className="empty-chain">Create a ledger before generating verification evidence.</p>}
  </section>;
}
