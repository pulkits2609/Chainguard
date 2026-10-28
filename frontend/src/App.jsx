import { useEffect, useState } from 'react';
import { apiRequest } from './api.js';
import Account from './components/Account.jsx';
import LedgerList from './components/LedgerList.jsx';
import LedgerManager from './components/LedgerManager.jsx';
import LedgerViewer from './components/LedgerViewer.jsx';
import Overview from './components/Overview.jsx';
import IntegrationManager from './components/IntegrationManager.jsx';
import SecurityPolicy from './components/SecurityPolicy.jsx';
import Verification from './components/Verification.jsx';
import SecurityEvents from './components/SecurityEvents.jsx';
import CustomerVerification from './components/CustomerVerification.jsx';
import { customerLocation } from './dashboardHelpers.js';

// Consume a capability before any request. It remains in memory and never enters
// server request paths, browser storage, or referrer URLs.
const customerEntry = typeof window === 'undefined' ? null : customerLocation(window.location);
if (customerEntry && window.location.hash) window.history.replaceState(null, '', window.location.pathname + window.location.search);

export default function App() {
  if (customerEntry) return <CustomerVerification initial={customerEntry}/>;
  return <Dashboard/>;
}

function Dashboard() {
  const [user, setUser] = useState(null);
  const [page, setPage] = useState('home');
  const [ledgerId, setLedgerId] = useState(null);
  const [error, setError] = useState('');
  const [sessionLoading, setSessionLoading] = useState(true);

  useEffect(() => {
    function expired() {
      setUser(null);
      setPage('account');
      setError('Your session ended. Log in again to manage your ledgers.');
    }
    window.addEventListener('session-expired', expired);
    return () => window.removeEventListener('session-expired', expired);
  }, []);

  useEffect(() => {
    let stopped = false;
    apiRequest('/api/me').then(result => {
      if (!stopped) setUser(result.user);
    }).catch(requestError => {
      if (!stopped) setError(requestError.message);
    }).finally(() => {
      if (!stopped) setSessionLoading(false);
    });
    return () => { stopped = true; };
  }, []);

  function navigate(next) {
    setError('');
    setPage(next);
  }

  async function logout() {
    try {
      await apiRequest('/api/logout', { method: 'POST', body: '{}' });
      setUser(null);
      navigate('home');
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  function openLedger(id) { setLedgerId(id); navigate('view'); }
  const sections = [['overview', 'Overview'], ['manage', 'Ledgers'], ['integrations', 'Integrations'], ['security', 'API security'], ['verification', 'Verification'], ...(user?.is_admin ? [['events', 'Security events']] : []), ['profile', user?.is_admin ? 'Administration' : 'Account']];

  return <main className="ledger-page">
    <nav className="page-nav" aria-label="Main navigation">
      <button className="nav-brand" onClick={() => navigate(user ? 'overview' : 'home')}>ChainGuard</button>
      <div className="nav-account">
        {user ? <><span>{user.username}{user.is_admin ? ' · Administrator' : ''}</span><button onClick={logout}>Log out</button></>
          : <button disabled={sessionLoading} onClick={() => navigate('account')}>Log in</button>}
      </div>
    </nav>
    {user && <nav className="dashboard-nav" aria-label="Dashboard sections">{sections.map(([id, label]) => <button key={id} className={page === id ? 'active' : ''} aria-current={page === id ? 'page' : undefined} onClick={() => navigate(id)}>{label}</button>)}</nav>}
    {error && <p className="notice warning" role="alert">{error}</p>}
    {page === 'home' && <section className="welcome">
      <h1 className="brand-heading"><img src="/ledger-mark.svg" width="48" height="48" alt=""/>ChainGuard</h1><p>Secure application integrations. Record operational events. Verify historical integrity.</p>
      <div className="welcome-options">
        <button onClick={() => navigate('browse')}><strong>View ledgers</strong><span>Browse public records and check a saved snapshot. No account needed.</span></button>
        <button disabled={sessionLoading} onClick={() => navigate(user ? 'overview' : 'account')}><strong>{user ? 'Open dashboard' : 'Log in / Create account'}</strong><span>Manage private logistics ledgers, integration security, and verification evidence.</span></button>
      </div>
    </section>}
    {page === 'account' && <Account onSuccess={nextUser => { setUser(nextUser); navigate('overview'); }}/>}
    {page === 'browse' && <LedgerList onOpen={openLedger}/>}
    {page === 'manage' && user && <LedgerManager key={user.id} user={user} onOpen={openLedger}/>}
    {page === 'overview' && user && <Overview onOpen={openLedger} isAdmin={user.is_admin}/>}
    {page === 'integrations' && user && <IntegrationManager/>}
    {page === 'security' && user && <SecurityPolicy/>}
    {page === 'verification' && user && <Verification/>}
    {page === 'events' && user?.is_admin && <SecurityEvents/>}
    {page === 'profile' && user && <section className="form-panel"><h1>{user.is_admin ? 'Administration' : 'Account'}</h1><p>Signed in as <strong>{user.username}</strong>.</p><p className="muted">{user.is_admin ? 'You can administer ledger visibility, availability, integrations, and security audit events. Existing ledger write tokens remain owner-managed.' : 'You can manage your own ledgers, integration credentials, security policies, and shipment access grants.'}</p><div className="button-row"><button onClick={() => navigate('manage')}>Manage ledgers</button><button onClick={() => navigate('browse')}>Browse public ledgers</button><button onClick={logout}>Log out</button></div></section>}
    {page === 'view' && ledgerId && <LedgerViewer key={ledgerId} ledgerId={ledgerId} onBack={() => navigate(user ? 'manage' : 'browse')}/>}
  </main>;
}
