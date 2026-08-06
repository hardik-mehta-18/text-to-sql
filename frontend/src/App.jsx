import { useState } from 'react';
import Connect from './components/Connect';
import Training from './components/Training';
import Chat from './components/Chat';
import Auth from './components/Auth';
import Dashboard from './components/Dashboard';
import AdminLLM from './components/AdminLLM';
import './App.css';

function App() {
  const [token, setToken] = useState(localStorage.getItem('token') || null);
  const [page, setPage] = useState(token ? 'dashboard' : 'auth');
  const [sessionInfo, setSessionInfo] = useState({
    sessionId: null,
    dialect: null,
    dbName: null,
    commonFilterKeys: []
  });
  const [filterValues, setFilterValues] = useState(null);
  const [showFilterModal, setShowFilterModal] = useState(false);

  const handleLogin = (newToken) => {
    localStorage.setItem('token', newToken);
    setToken(newToken);
    setPage('dashboard');
  };

  const disconnect = async () => {
    if (sessionInfo.sessionId) {
      try {
        await fetch(`/api/session/${sessionInfo.sessionId}`, {
          method: 'DELETE'
        });
      } catch (err) {
        console.error("Disconnect error:", err);
      }
    }
    setSessionInfo({ sessionId: null, dialect: null, dbName: null, commonFilterKeys: [] });
    setFilterValues(null);
    setPage('dashboard');
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    setToken(null);
    setPage('auth');
  };

  const handleSaveAndChat = async (keys = []) => {
    if (token && sessionInfo.sessionId) {
      // Update local state if keys passed
      if (keys.length > 0) {
        setSessionInfo(prev => ({ ...prev, commonFilterKeys: keys }));
      }
      
      try {
        await fetch(`/api/models/save`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify({
            session_id: sessionInfo.sessionId,
            name: `${sessionInfo.dbName} Analytics`
          })
        });
      } catch (err) {
        console.error("Failed to auto-save model", err);
      }
    }
    
    // Check if we need to prompt for filter values (Skip for owners)
    // When we just saved/trained, we are the owner.
    setPage('chat');
  };

  const loadSavedModel = (info) => {
    setSessionInfo({
      sessionId: info.sessionId,
      dialect: info.dialect,
      dbName: info.dbName,
      commonFilterKeys: info.commonFilterKeys || [],
      isOwner: info.isOwner
    });
    
    // Only show modal for non-owners
    if (!info.isOwner && info.commonFilterKeys && info.commonFilterKeys.length > 0) {
      setShowFilterModal(true);
    } else {
      setPage('chat');
    }
  };

  const handleFilterSubmit = async (values) => {
    console.log("Submitting values for session:", sessionInfo.sessionId, values);
    try {
      const resp = await fetch(`/api/session/${sessionInfo.sessionId}/filter-values`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionInfo.sessionId, values })
      });
      if (!resp.ok) throw new Error("Failed to set session filters");
      
      setFilterValues(values);
      setShowFilterModal(false);
      setPage('chat');
    } catch (err) {
      console.error("Filter submit error:", err);
      alert(err.message);
    }
  };

  return (
    <div className="app-container">
      <nav className="glass-panel navbar">
        <div className="logo" style={{cursor: 'pointer'}} onClick={() => token ? setPage('dashboard') : setPage('auth')}>
          🗄️ Universal DB Chatbot
        </div>
        <div style={{display:'flex', gap: '10px'}}>
          {page === 'chat' && (
             <button className="secondary btn-sm" onClick={disconnect}>🔌 Disconnect & Dashboard</button>
          )}
          {token && page !== 'admin' && (
             <button className="secondary btn-sm" onClick={() => setPage('admin')}>⚙️ LLM Config</button>
          )}
          {token && (
             <button className="secondary btn-sm" onClick={handleLogout}>🚪 Logout</button>
          )}
        </div>
      </nav>
      
      <main className="main-content">
        {!token && (page === 'auth' || !token) && <Auth onLogin={handleLogin} />}
        {token && page === 'dashboard' && <Dashboard token={token} onLoadModel={loadSavedModel} onConnectNew={() => setPage('connect')} />}
        {token && page === 'connect' && <Connect onConnected={(info) => { setSessionInfo(info); setPage('training'); }} />}
        {token && page === 'training' && <Training sessionInfo={sessionInfo} onComplete={handleSaveAndChat} onError={disconnect} />}
        {token && page === 'chat' && <Chat sessionInfo={sessionInfo} />}
        {token && page === 'admin' && <AdminLLM token={token} onBack={() => setPage('dashboard')} />}

        {showFilterModal && (
          <FilterValuesModal 
            key={sessionInfo.commonFilterKeys.join(',')}
            keys={sessionInfo.commonFilterKeys} 
            onSubmit={handleFilterSubmit} 
            onCancel={() => { setShowFilterModal(false); setPage('dashboard'); }} 
          />
        )}
      </main>
    </div>
  );
}

function FilterValuesModal({ keys, onSubmit, onCancel }) {
  const [values, setValues] = useState(
    keys.reduce((acc, k) => ({ ...acc, [k]: '' }), {})
  );

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit(values);
  };

  return (
    <div className="modal-overlay" style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(10px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
    }}>
      <div className="glass-panel animate-scale-in" style={{
        maxWidth: '400px', width: '90%', padding: '2rem', border: '1px solid var(--border-color)'
      }}>
        <h2 style={{marginTop: 0, marginBottom: '0.5rem'}}>Session Identity</h2>
        <p className="text-muted" style={{marginBottom: '1.5rem', fontSize: '0.9rem'}}>
          This model has <strong>mandatory filters</strong>. Please provide your identification values for this session.
        </p>
        <form onSubmit={handleSubmit}>
          {keys.map(key => (
            <div key={key} style={{marginBottom: '1.2rem'}}>
              <label style={{display: 'block', marginBottom: '0.5rem', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-color)'}}>
                {key.toUpperCase()}
              </label>
              <input
                type="text"
                required
                value={values[key]}
                onChange={(e) => setValues({ ...values, [key]: e.target.value })}
                placeholder={`Enter ${key}...`}
                style={{width: '100%', padding: '0.8rem'}}
              />
            </div>
          ))}
          <div style={{display: 'flex', gap: '10px', marginTop: '2rem'}}>
            <button type="button" className="secondary" onClick={onCancel} style={{flex: 1}}>Cancel</button>
            <button type="submit" style={{flex: 1}}>Enter Chat →</button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default App;
