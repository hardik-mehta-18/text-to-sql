import { useState } from 'react';


function Connect({ onConnected }) {
  const [dbType, setDbType] = useState('SQLite (Upload)');
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // Form State
  const [sqliteFile, setSqliteFile] = useState(null);
  const [host, setHost] = useState('localhost');
  const [port, setPort] = useState('5432');
  const [dbName, setDbName] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [server, setServer] = useState('');
  const [service, setService] = useState('');

  const buildConnStr = () => {
    switch (dbType) {
      case 'PostgreSQL': return `postgresql+psycopg2://${username}:${password}@${host}:${port}/${dbName}`;
      case 'MySQL': return `mysql+pymysql://${username}:${password}@${host}:${port}/${dbName}`;
      case 'MS SQL Server': return `mssql+pyodbc://${username}:${password}@${server}/${dbName}?driver=ODBC+Driver+17+for+SQL+Server`;
      case 'Oracle': return `oracle+cx_oracle://${username}:${password}@${host}:${port}/?service_name=${service}`;
      default: return '';
    }
  };

  const handleConnect = async (e) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const formData = new FormData();
      if (dbType === 'SQLite (Upload)') {
        if (!sqliteFile) throw new Error("Please upload a file");
        formData.append('source_type', 'upload');        // was 'type'
        formData.append('db_file', sqliteFile);
      } else {
        const str = buildConnStr();
        if (!str || (!username && !dbName)) throw new Error("Missing credentials");
        formData.append('source_type', 'connection_string');  // was 'type'
        formData.append('connection_string', str);
      }

      const resp = await fetch(`/api/db/connect`, {
        method: 'POST',
        body: formData,
      });

      const data = await resp.json();
      if (!resp.ok) {
        const detail = data.detail;
        const message = Array.isArray(detail)
          ? detail.map(e => e.msg).join(', ')   // FastAPI validation errors
          : (typeof detail === 'string' ? detail : JSON.stringify(detail));
        throw new Error(message);
      }

      onConnected({
        sessionId: data.session_id,
        dialect: data.dialect,
        dbName: data.db_name
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="connect-panel glass-panel animate-fade-in">
      <div className="header-text">
        <h1>Connect Your Database</h1>
        <p className="text-muted">Chat with your data securely using plain English</p>
      </div>

      <form onSubmit={handleConnect}>
        <div className="form-group">
          <label>Database Engine</label>
          <select value={dbType} onChange={e => {
            setDbType(e.target.value);
            if (e.target.value === "MySQL") setPort("3306");
            if (e.target.value === "PostgreSQL") setPort("5432");
            if (e.target.value === "Oracle") setPort("1521");
          }}>
            <option>SQLite (Upload)</option>
            <option>PostgreSQL</option>
            <option>MySQL</option>
            <option>MS SQL Server</option>
            <option>Oracle</option>
          </select>
        </div>

        {dbType === 'SQLite (Upload)' ? (
          <div className="form-group">
            <label>Upload Database (.sqlite, .db)</label>
            <input type="file" accept=".db,.sqlite,.sqlite3" onChange={e => setSqliteFile(e.target.files[0])} />
          </div>
        ) : (
          <>
            {dbType !== 'MS SQL Server' ? (
              <div className="form-row form-group">
                <div><label>Host</label><input type="text" value={host} onChange={e => setHost(e.target.value)} placeholder="localhost" /></div>
                <div><label>Port</label><input type="text" value={port} onChange={e => setPort(e.target.value)} /></div>
              </div>
            ) : (
              <div className="form-group"><label>Server</label><input type="text" value={server} onChange={e => setServer(e.target.value)} placeholder="hostname\SQLEXPRESS" /></div>
            )}

            <div className="form-group">
              <label>{dbType === 'Oracle' ? 'Service Name' : 'Database Name'}</label>
              <input type="text"
                value={dbType === 'Oracle' ? service : dbName}
                onChange={e => dbType === 'Oracle' ? setService(e.target.value) : setDbName(e.target.value)}
                placeholder={dbType === 'Oracle' ? 'ORCL' : 'mydb'} />
            </div>

            <div className="form-row form-group">
              <div><label>Username</label><input type="text" value={username} onChange={e => setUsername(e.target.value)} /></div>
              <div><label>Password</label><input type="password" value={password} onChange={e => setPassword(e.target.value)} /></div>
            </div>
          </>
        )}

        {error && <div className="error-box">{error}</div>}

        <div style={{ marginTop: '2rem' }}>
          <button type="submit" style={{ width: '100%' }} disabled={loading}>
            {loading ? "Connecting..." : "Connect & Analyze →"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default Connect;
