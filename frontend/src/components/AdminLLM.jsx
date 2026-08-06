import { useState, useEffect } from 'react';

function AdminLLM({ token, onBack }) {
  const [configs, setConfigs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  useEffect(() => {
    fetchConfigs();
  }, []);

  const fetchConfigs = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch('/api/admin/llm-configs', {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (!resp.ok) {
        throw new Error('Failed to load LLM configurations');
      }
      const data = await resp.json();
      // Ensure sorted by sequence_order
      const sorted = [...data].sort((a, b) => a.sequence_order - b.sequence_order);
      setConfigs(sorted);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleFieldChange = (index, field, value) => {
    setConfigs(prev => {
      const updated = [...prev];
      updated[index] = { ...updated[index], [field]: value };
      return updated;
    });
  };

  const moveUp = (index) => {
    if (index === 0) return;
    setConfigs(prev => {
      const list = [...prev];
      const temp = list[index];
      list[index] = list[index - 1];
      list[index - 1] = temp;
      
      // Update sequence orders
      return list.map((item, idx) => ({ ...item, sequence_order: idx + 1 }));
    });
  };

  const moveDown = (index) => {
    if (index === configs.length - 1) return;
    setConfigs(prev => {
      const list = [...prev];
      const temp = list[index];
      list[index] = list[index + 1];
      list[index + 1] = temp;

      // Update sequence orders
      return list.map((item, idx) => ({ ...item, sequence_order: idx + 1 }));
    });
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const resp = await fetch('/api/admin/llm-configs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ configs })
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || 'Failed to save configurations');
      }
      const updated = await resp.json();
      const sorted = [...updated].sort((a, b) => a.sequence_order - b.sequence_order);
      setConfigs(sorted);
      setSuccess('LLM configurations saved successfully!');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="glass-panel animate-fade-in" style={{ padding: '2rem', maxWidth: '1000px', margin: '2rem auto', width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
        <div>
          <h1 style={{ fontSize: '1.75rem', marginBottom: '0.25rem' }}>⚙️ LLM Fallback Configuration</h1>
          <p className="text-muted" style={{ fontSize: '0.9rem' }}>
            Set parameters, model names, and the prioritization sequence for LLMs. Drag/reorder to change fallback order.
          </p>
        </div>
        <button className="secondary" onClick={onBack}>← Back</button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '3rem' }}>
          <div className="spinner" style={{ border: '4px solid rgba(0,0,0,0.1)', width: '36px', height: '36px', borderRadius: '50%', borderLeftColor: 'var(--primary)', animation: 'spin 1s linear infinite', margin: '0 auto' }}></div>
          <p style={{ marginTop: '1rem', color: 'var(--text-muted)' }}>Loading configurations...</p>
        </div>
      ) : (
        <form onSubmit={handleSave}>
          {error && <div className="error-box" style={{ marginBottom: '1.5rem', marginTop: 0 }}>{error}</div>}
          {success && <div style={{ background: '#ecfdf5', border: '1px solid #a7f3d0', color: '#065f46', padding: '12px 16px', borderRadius: '8px', marginBottom: '1.5rem', fontSize: '0.9rem' }}>{success}</div>}

          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
            {configs.map((cfg, index) => (
              <div 
                key={cfg.provider} 
                className="glass-panel" 
                style={{ 
                  padding: '1.5rem', 
                  borderLeft: `5px solid ${cfg.is_enabled ? 'var(--primary)' : '#94a3b8'}`,
                  background: cfg.is_enabled ? 'var(--bg-card)' : '#f8fafc',
                  opacity: cfg.is_enabled ? 1 : 0.8,
                  transition: 'all 0.2s ease'
                }}
              >
                <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start' }}>
                  {/* Reorder Buttons */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', paddingTop: '1.5rem' }}>
                    <button 
                      type="button" 
                      onClick={() => moveUp(index)} 
                      disabled={index === 0}
                      className="secondary btn-sm"
                      style={{ padding: '4px 8px', fontSize: '0.75rem', minWidth: '32px' }}
                    >
                      ▲
                    </button>
                    <div style={{ fontWeight: 600, fontSize: '0.8rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                      #{index + 1}
                    </div>
                    <button 
                      type="button" 
                      onClick={() => moveDown(index)} 
                      disabled={index === configs.length - 1}
                      className="secondary btn-sm"
                      style={{ padding: '4px 8px', fontSize: '0.75rem', minWidth: '32px' }}
                    >
                      ▼
                    </button>
                  </div>

                  {/* Config Inputs */}
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                      <h3 style={{ textTransform: 'capitalize', margin: 0, fontSize: '1.15rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {cfg.provider}
                        <span style={{ fontSize: '0.75rem', padding: '2px 8px', borderRadius: '12px', background: cfg.is_enabled ? '#dbeafe' : '#f1f5f9', color: cfg.is_enabled ? 'var(--primary)' : '#64748b', fontWeight: 600 }}>
                          Priority {cfg.sequence_order}
                        </span>
                      </h3>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 500 }}>
                        <input 
                          type="checkbox" 
                          checked={cfg.is_enabled}
                          onChange={(e) => handleFieldChange(index, 'is_enabled', e.target.checked)}
                          style={{ width: 'auto', cursor: 'pointer' }}
                        />
                        Enabled
                      </label>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', marginBottom: '1rem' }}>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)' }}>Model Name</label>
                        <input 
                          type="text" 
                          required
                          value={cfg.model}
                          onChange={(e) => handleFieldChange(index, 'model', e.target.value)}
                          placeholder="e.g. gemini-3.6-flash"
                          disabled={!cfg.is_enabled}
                          style={{ padding: '8px 12px', fontSize: '0.9rem' }}
                        />
                      </div>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)' }}>Max Tokens</label>
                        <input 
                          type="number" 
                          required
                          min="1"
                          value={cfg.max_tokens}
                          onChange={(e) => handleFieldChange(index, 'max_tokens', parseInt(e.target.value) || 0)}
                          disabled={!cfg.is_enabled}
                          style={{ padding: '8px 12px', fontSize: '0.9rem' }}
                        />
                      </div>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)' }}>Temperature</label>
                        <input 
                          type="number" 
                          required
                          step="0.1"
                          min="0"
                          max="2"
                          value={cfg.temperature}
                          onChange={(e) => handleFieldChange(index, 'temperature', parseFloat(e.target.value) || 0)}
                          disabled={!cfg.is_enabled}
                          style={{ padding: '8px 12px', fontSize: '0.9rem' }}
                        />
                      </div>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)' }}>Timeout (sec)</label>
                        <input 
                          type="number" 
                          required
                          min="1"
                          value={cfg.timeout_seconds}
                          onChange={(e) => handleFieldChange(index, 'timeout_seconds', parseInt(e.target.value) || 0)}
                          disabled={!cfg.is_enabled}
                          style={{ padding: '8px 12px', fontSize: '0.9rem' }}
                        />
                      </div>
                    </div>

                    <div className="form-group" style={{ marginBottom: 0 }}>
                      <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)' }}>API Key Override (Optional)</label>
                      <input 
                        type="password" 
                        value={cfg.api_key || ''}
                        onChange={(e) => handleFieldChange(index, 'api_key', e.target.value)}
                        placeholder="Leave blank to use environment/KeyManager default credentials"
                        disabled={!cfg.is_enabled}
                        style={{ padding: '8px 12px', fontSize: '0.9rem' }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: '1rem', marginTop: '2rem', justifyContent: 'flex-end' }}>
            <button type="button" className="secondary" onClick={fetchConfigs} disabled={saving}>Reset</button>
            <button type="submit" disabled={saving}>
              {saving ? 'Saving Configurations...' : '💾 Save Configurations'}
            </button>
          </div>
        </form>
      )}

      {/* Animation spinner style */}
      <style>{`
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

export default AdminLLM;
