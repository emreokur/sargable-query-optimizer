import { useState } from 'react'
import type { ConnectionDetails } from './api'

type Props = {
  initial?: Partial<ConnectionDetails>
  busy: boolean
  error?: string
  onSubmit: (details: ConnectionDetails) => void
  onDemo: () => void
  onCancel?: () => void
}

export default function ConnectionForm({ initial, busy, error, onSubmit, onDemo, onCancel }: Props) {
  const [host, setHost] = useState(initial?.host ?? '')
  const [port, setPort] = useState(String(initial?.port ?? 5432))
  const [database, setDatabase] = useState(initial?.database ?? '')
  const [user, setUser] = useState(initial?.user ?? '')
  const [password, setPassword] = useState('')
  const [sslmode, setSslmode] = useState(initial?.sslmode ?? 'verify-full')
  const [caCert, setCaCert] = useState('')
  const [sandbox, setSandbox] = useState(initial?.sandbox ?? false)
  const portOk = /^\d+$/.test(port) && +port >= 1 && +port <= 65535
  const ready = !busy && portOk && [host, database, user].every((s) => s.trim())

  return (
    <form
      aria-label="Database connection"
      className="connform"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit({ host: host.trim(), port: +port, database: database.trim(), user: user.trim(), password, sslmode, sandbox,
          ca_cert: sslmode === 'verify-full' ? caCert : '' })
        setPassword('')
      }}
    >
      <h2>Connect to a database</h2>
      <div className="pair narrow">
        <div className="field">
          <label className="field">
            Host
            <input
              className="input"
              aria-describedby="host-hint"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              spellCheck={false}
            />
          </label>
          <span id="host-hint" className="hint">For a database on this computer, use host.docker.internal.</span>
        </div>
        <label className="field">
          Port
          <input className="input" inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value)} />
        </label>
      </div>
      <div className="pair">
        <label className="field">
          Database
          <input className="input" value={database} onChange={(e) => setDatabase(e.target.value)} spellCheck={false} />
        </label>
        <label className="field">
          User
          <input className="input" value={user} onChange={(e) => setUser(e.target.value)} spellCheck={false} />
        </label>
      </div>
      <div className="field">
        <label className="field">
          Password
          <input
            className="input"
            type="password"
            autoComplete="off"
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <span id="password-hint" className="hint">
          Kept in memory only. You type it again after Sargable restarts.
        </span>
      </div>
      <label className="field">
        SSL mode
        <select className="input" value={sslmode} onChange={(e) => setSslmode(e.target.value as ConnectionDetails['sslmode'])}>
          <option value="verify-full">verify-full</option>
          <option value="require">require</option>
          <option value="disable">disable</option>
        </select>
      </label>
      {sslmode === 'verify-full' && (
        <div className="field">
          <label className="field">
            CA certificate (optional)
            <textarea
              className="input cert"
              aria-describedby="ca-hint"
              rows={4}
              value={caCert}
              onChange={(e) => setCaCert(e.target.value)}
              spellCheck={false}
            />
          </label>
          <span id="ca-hint" className="hint">
            Only needed for providers with their own certificate authority, such as Amazon RDS, Azure and Google Cloud
            SQL. Leave it empty to use the system's trusted certificates.
          </span>
        </div>
      )}
      <div className="field">
        <label className="check">
          <input type="checkbox" checked={sandbox} onChange={(e) => setSandbox(e.target.checked)} />
          Sandbox (a disposable copy)
        </label>
        <span data-sandbox-warning className="hint">
          Sandbox really builds indexes and runs write statements before rolling them back, so it is only for a
          disposable copy. Leave it off for anything real.
        </span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="actions">
        <button className="btn primary" disabled={!ready}>Connect</button>
        <button type="button" className="btn secondary" onClick={onDemo} disabled={busy}>Use the demo database</button>
        {onCancel && <button type="button" className="link" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  )
}
