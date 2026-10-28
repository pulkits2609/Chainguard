import { useState } from 'react';
import { apiRequest } from '../api.js';

export default function Account({ onSuccess }) {
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      const result = await apiRequest(register ? '/api/register' : '/api/login', {
        method: 'POST', body: JSON.stringify({ username, password }),
      });
      setPassword('');
      onSuccess(result.user);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return <section className="form-panel account-panel">
    <h1>{register ? 'Create account' : 'Log in'}</h1>
    <p>{register ? 'Create and secure your own ledgers. Private records require authorization.' : 'Open your ledgers and their integration settings.'}</p>
    <form onSubmit={submit}>
      <label>Username<input required autoComplete="username" maxLength={32} value={username} onChange={event => setUsername(event.target.value)}/></label>
      <label>Password<input required type="password" autoComplete={register ? 'new-password' : 'current-password'} minLength={register ? 12 : undefined} maxLength={128} value={password} onChange={event => setPassword(event.target.value)}/></label>
      {register && <small>Use at least 12 characters. The administrator account is configured on the server.</small>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      <button className="primary" disabled={busy}>{busy ? 'Please wait…' : register ? 'Create account' : 'Log in'}</button>
    </form>
    <button className="text-button" disabled={busy} onClick={() => { setRegister(value => !value); setPassword(''); setError(''); }}>{register ? 'Already have an account? Log in' : 'Need an account? Register'}</button>
  </section>;
}
