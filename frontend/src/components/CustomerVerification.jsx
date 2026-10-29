import { useEffect, useState } from 'react';
import { customerRequest } from '../api.js';
import { formatTime, saveJson, verifyCheckpointSignature } from '../dashboardHelpers.js';

const PageSize = 50;

function CustomerSession({ shipment, token, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [proofBusy, setProofBusy] = useState(false);
  const [trustedKey, setTrustedKey] = useState(null);
  const [signatureResult, setSignatureResult] = useState(null);
  useEffect(() => {
    let stopped = false;
    let timer;
    let controller;
    setBusy(true); setData(null); setError('');
    async function poll() {
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 15000);
      let denied = false;
      try {
        const result = await customerRequest(shipment, 'events', token, { signal: controller.signal, query: new URLSearchParams({ limit: PageSize, offset }) });
        if (!stopped) { setData(result); setError(''); }
      } catch (requestError) {
        denied = requestError.status === 401 || requestError.status === 403 || requestError.status === 404;
        if (!stopped) { setError(requestError.name === 'AbortError' ? 'Shipment request timed out.' : requestError.message); if (denied) setData(null); }
      } finally {
        window.clearTimeout(timeout);
        if (!stopped) { setBusy(false); if (!denied) timer = window.setTimeout(poll, 15000); }
      }
    }
    poll();
    return () => { stopped = true; controller?.abort(); window.clearTimeout(timer); };
  }, [shipment, token, offset, revision]);
  async function proof() {
    setProofBusy(true); setError('');
    try { saveJson(await customerRequest(shipment, 'proof', token), `shipment-proof-${shipment.replace(/[^a-zA-Z0-9_-]/g, '_')}.json`); }
    catch (requestError) { setError(requestError.message); }
    finally { setProofBusy(false); }
  }
  async function loadTrustedKey(event) {
    const file = event.target.files?.[0]; event.target.value = '';
    if (!file) return;
    try {
      if (file.size > 16384) throw new Error('Key file exceeds 16 KiB.');
      const key = JSON.parse(await file.text());
      if (key.algorithm !== 'Ed25519' || !key.key_id || !key.public_key) throw new Error('Choose a trusted Ed25519 public-key JSON file.');
      setTrustedKey(key); setSignatureResult(null);
    } catch (requestError) { setError(requestError.message); }
  }
  async function verifySignature() {
    setProofBusy(true); setError('');
    try {
      const valid = await verifyCheckpointSignature(data.checkpoint, trustedKey);
      setSignatureResult({ signature: data.checkpoint.signature, message: valid ? 'Checkpoint signature verified with your supplied trusted key.' : 'Verification failed: checkpoint signature does not match the supplied trusted key.' });
    } catch (requestError) { setSignatureResult(null); setError(requestError.message); }
    finally { setProofBusy(false); }
  }
  const verification = data?.verification;
  const signatureStatus = signatureResult && signatureResult.signature === data?.checkpoint?.signature ? signatureResult.message : 'Not independently verified';
  return <>
    <header className="toolbar"><div><p className="eyebrow">Read-only shipment access</p><h1>{shipment}</h1></div><div className="button-row"><button disabled={busy} onClick={() => setRevision(value => value + 1)}>Refresh</button><button onClick={onClose}>Close access</button></div></header>
    <p className="muted">Updates are checked every 15 seconds while access remains valid. Events appear when the connected application records them.</p>
    {error && <p className="notice warning" role="alert">{error}</p>}{busy && <p role="status">Reading shipment history…</p>}
    {data && <>
      <section className={`form-panel ${verification?.status === 'verification_failed' ? 'verification-failed' : ''}`}><h2>Verification evidence</h2>
        <dl className="metadata-grid"><div><dt>Shipment records</dt><dd>{verification?.server_records_verified ? 'Verified by the ChainGuard server' : 'Verification failed'}</dd></div><div><dt>Server checkpoint check</dt><dd>{verification?.checkpoint_signature_verified ? 'Checkpoint signature verified by the server' : verification?.checkpoint_status === 'verification_failed' ? 'Verification failed' : 'No covering checkpoint available'}</dd></div><div><dt>Independent checkpoint signature</dt><dd>{signatureStatus}</dd></div><div><dt>Independent full-record history</dt><dd>Not independently verified in this redacted view</dd></div><div><dt>Last recorded update</dt><dd>{formatTime(data.last_recorded_at_ms)}</dd></div></dl>
        {verification?.checked_at_ms && <p className="muted">Server history check: {formatTime(verification.checked_at_ms)}. Cached for at most {verification.maximum_cache_age_seconds} seconds; ordinary database history changes invalidate the check.</p>}
        {verification?.all_checkpoints_compared === false && <p className="notice warning">The server compared the latest {verification.checkpoint_comparisons} checkpoints. Older checkpoint evidence was not checked by this response; retain and verify trusted evidence separately.</p>}
        <p className="notice">Verifying a checkpoint signature does not independently verify the contents of this redacted shipment timeline. It does not prove physical delivery. Full-record history verification is available to authorized ledger owners and administrators.</p>
        {data.checkpoint && <><p className="mono">Checkpoint block #{data.checkpoint.index} · key {data.checkpoint.key_id}</p>{verification?.checkpoint_covers_all_shipment_records === false && <p className="muted">This checkpoint covers an older prefix. Later shipment events are not covered by its signature.</p>}<div className="button-row"><button disabled={proofBusy} onClick={proof}>Download verification evidence</button><label className="file-button">Load separately trusted public key<input type="file" accept="application/json,.json" onChange={loadTrustedKey}/></label><button disabled={!trustedKey || proofBusy} onClick={verifySignature}>Verify checkpoint signature locally</button></div></>}
      </section>
      <section className="form-panel"><h2>Recorded timeline</h2><ol className="shipment-timeline">{(data.events || []).map((event, index) => <li key={event.block_index ?? index}><div className="timeline-marker"/><article><h3>{event.event_type || 'Recorded event'}</h3><time>{formatTime(event.event_time)}</time>{event.location && <p>{event.location}</p>}{event.source_system && <p className="muted">Source: {event.source_system}</p>}<small>Recorded {formatTime(event.recorded_at_ms)}</small></article></li>)}</ol>
        {!data.events?.length && <p className="muted">No recorded events for this shipment.</p>}
        <div className="pagination"><button disabled={busy || offset === 0} onClick={() => setOffset(value => Math.max(0, value - PageSize))}>Previous</button><span>Page {offset / PageSize + 1} · {data.total} events</span><button disabled={busy || offset + PageSize >= data.total} onClick={() => setOffset(value => value + PageSize)}>Next</button></div>
      </section>
    </>}
  </>;
}

export default function CustomerVerification({ initial }) {
  const [credentials, setCredentials] = useState(initial?.token ? initial : null);
  const [shipment, setShipment] = useState(initial?.shipment || '');
  const [token, setToken] = useState('');
  return <main className="ledger-page customer-page"><div className="customer-brand"><strong>ChainGuard</strong><span>Shipment verification</span></div>
    {credentials ? <CustomerSession key={`${credentials.shipment}:${credentials.token}`} shipment={credentials.shipment} token={credentials.token} onClose={() => { setCredentials(null); setToken(''); }}/>
      : <section className="form-panel account-panel"><h1>Open shipment verification</h1><p className="muted">Use the scoped access token provided by the organization. A shipment number alone does not grant access.</p><form onSubmit={event => { event.preventDefault(); setCredentials({ shipment: shipment.trim(), token: token.trim() }); setToken(''); }}><label>Shipment ID<input required maxLength={128} value={shipment} onChange={event => setShipment(event.target.value)}/></label><label>Access token<input required type="password" autoComplete="off" maxLength={512} value={token} onChange={event => setToken(event.target.value)}/></label><button className="primary">Open shipment</button></form></section>}
    <footer className="customer-footer">Cryptographic integrity describes recorded evidence. It does not establish the truth of an event submitted by an external application.</footer>
  </main>;
}
