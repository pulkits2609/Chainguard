import { useEffect, useState } from 'react';
import { apiRequest } from '../api.js';

const FieldTypes = ['string', 'integer', 'boolean', 'object', 'array'];

export default function CreateLedger({ onCreated, onCancel }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [fields, setFields] = useState([{ name: 'title', type: 'string', required: true }]);
  const [templates, setTemplates] = useState([]);
  const [templateId, setTemplateId] = useState('');
  const [visibility, setVisibility] = useState('public');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    apiRequest('/api/ledger-templates', { signal: controller.signal }).then(result => setTemplates(result.templates))
      .catch(requestError => { if (!controller.signal.aborted) setError(requestError.message); });
    return () => controller.abort();
  }, []);

  function chooseTemplate(id) {
    const template = templates.find(item => item.id === id);
    setTemplateId(id); setConfirmed(false); setVisibility(id ? 'private' : 'public');
    setFields(template ? template.fields.map(field => ({ ...field })) : [{ name: 'title', type: 'string', required: true }]);
  }

  function change(index, key, value) {
    setConfirmed(false);
    setFields(current => current.map((field, position) => position === index ? { ...field, [key]: value } : field));
  }
  async function create(event) {
    event.preventDefault();
    if (!confirmed) return;
    setBusy(true); setError('');
    try {
      const result = await apiRequest('/api/ledgers', { method: 'POST', body: JSON.stringify({ name, description, fields, visibility, ...(templateId ? { template_id: templateId } : {}) }) });
      onCreated(result);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return <section className="form-panel">
    <h2>Create a ledger</h2>
    <form onSubmit={create}>
      <label>Ledger template<select value={templateId} onChange={event => chooseTemplate(event.target.value)}><option value="">Custom ledger</option>{templates.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label>
      <label>Name<input required maxLength={80} value={name} onChange={event => setName(event.target.value)}/></label>
      <label>Description<textarea maxLength={500} value={description} onChange={event => setDescription(event.target.value)}/></label>
      <h3>Block data structure</h3><p className="muted">Each new record must follow these fields. Use integer units for fractional amounts; floating-point values are not supported. Objects and arrays contain JSON values.</p>
      <label>Visibility<select value={visibility} onChange={event => setVisibility(event.target.value)}><option value="private">Private · owner and administrator only</option>{!templateId && <option value="public">Public · anyone can read and export</option>}</select></label>
      {templateId && <p className="muted">Logistics templates start private. You can change visibility later through ledger management. Template fields are fixed.</p>}
      <fieldset disabled={Boolean(templateId) || busy} className="schema-fields"><legend>Fields</legend>{fields.map((field, index) => <div className="schema-field" key={index}>
        <label>Field name<input required pattern="[A-Za-z_][A-Za-z0-9_]*" maxLength={64} value={field.name} onChange={event => change(index, 'name', event.target.value)}/></label>
        <label>Type<select value={field.type} onChange={event => change(index, 'type', event.target.value)}>{FieldTypes.map(type => <option key={type}>{type}</option>)}</select></label>
        <label className="check-label"><input type="checkbox" checked={field.required} onChange={event => change(index, 'required', event.target.checked)}/>Required</label>
        <button type="button" disabled={fields.length === 1 || busy} onClick={() => { setFields(current => current.filter((_, position) => position !== index)); setConfirmed(false); }}>Remove</button>
      </div>)}</fieldset>
      <button type="button" disabled={Boolean(templateId) || fields.length >= 32 || busy} onClick={() => { setFields(current => [...current, { name: '', type: 'string', required: true }]); setConfirmed(false); }}>Add field</button>
      <label className="check-label schema-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/>I understand that genesis and this structure cannot be changed after creation.</label>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="button-row"><button className="primary" disabled={busy || !confirmed}>{busy ? 'Creating…' : 'Create ledger'}</button><button type="button" disabled={busy} onClick={onCancel}>Cancel</button></div>
    </form>
  </section>;
}
