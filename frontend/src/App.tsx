import { useEffect, useState, type ReactNode } from 'react'
import {
  connect, explain, fmtMs, getConnection, getSchema, optimize, resetConnection,
  type Connection, type ConnectionDetails, type Explain, type Fix, type Optimize, type PlanNode, type Table } from './api'
import ConnectionForm from './ConnectionForm'
import Diagnosis from './Diagnosis'
import FixPanel from './FixPanel'
import PlanTree from './PlanTree'
import { samples } from './samples'
import { speedup } from './speedup'

type Tab = 'plan' | 'diagnosis' | number

const REMEMBERED = 'sargable.connection'

const bars = (n: number, className: string) =>
  Array.from({ length: n }, (_, i) => <span key={i} className={`skeleton ${className}`} />)
const find = (n: PlanNode, id: number): PlanNode | undefined =>
  n.id === id ? n : n.children.map((c) => find(c, id)).find(Boolean)
const count = (n: number) => n.toLocaleString('en-US')

function Finding({ ex }: { ex: Explain }) {
  const b = find(ex.plan, ex.bottleneckId)!
  return (
    <p className="finding">
      {b.type}
      {b.relation && ` on ${b.relation}`} took {fmtMs(b.selfMs)} ms of {fmtMs(ex.executionMs)} ms
      {b.rowsRemoved ? ` and discarded ${count(b.rowsRemoved)} rows to keep ${count(b.rows)}` : ''}.
    </p>
  )
}

export default function App() {
  const [sql, setSql] = useState('')
  const [conn, setConn] = useState<Connection | null>(null)
  const [tables, setTables] = useState<Table[]>([])
  const [busy, setBusy] = useState<'explain' | 'optimize' | null>(null)
  const [plan, setPlan] = useState<Explain | null>(null)
  const [fixes, setFixes] = useState<Optimize | null>(null)
  const [planError, setPlanError] = useState('')
  const [fixesError, setFixesError] = useState('')
  const [tab, setTab] = useState<Tab>('plan')
  const [testing, setTesting] = useState<number | null>(null)
  const [editing, setEditing] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState('')

  useEffect(() => {
    getConnection().then((c) => {
      setConn(c)
      if (c.demo) setSql((s) => s || samples[0].sql)
    })
    getSchema().then(setTables)
  }, [])

  const problems = conn?.problems ?? []
  const live = conn?.mode === 'live'
  const showForm = editing || problems.length > 0

  async function change(request: Promise<Connection>, details?: ConnectionDetails) {
    setConnecting(true)
    setConnectError('')
    try {
      const c = await request
      if (details) {
        const { host, port, database, user, sslmode } = details
        localStorage.setItem(REMEMBERED, JSON.stringify({ host, port, database, user, sslmode }))
      }
      setConn(c)
      setSql(c.demo ? samples[0].sql : '')
      setPlan(null)
      setFixes(null)
      setPlanError('')
      setFixesError('')
      setTab('plan')
      setEditing(false)
      getSchema().then(setTables)
    } catch (e) {
      setConnectError((e as Error).message)
    }
    setConnecting(false)
  }

  async function run() {
    setPlan(null)
    setFixes(null)
    setPlanError('')
    setFixesError('')
    setTab('plan')
    setBusy('explain')
    try {
      setPlan(await explain(sql))
    } catch (e) {
      setPlanError((e as Error).message)
      setBusy(null)
      return
    }
    setBusy('optimize')
    try {
      setFixes(await optimize(sql))
    } catch (e) {
      setFixesError((e as Error).message)
    }
    setBusy(null)
    setTab('diagnosis')
  }

  async function test(i: number) {
    const fix = fixes!.fixes[i]
    let next: Fix
    setTesting(i)
    try {
      const ex = await explain(fix.sql)
      next = { ...fix, skipped: undefined, testable: undefined, afterMs: ex.executionMs, plan: ex.plan, rowsMatch: ex.plan.rows === plan!.plan.rows }
    } catch (e) {
      next = { ...fix, skipped: undefined, testable: undefined, error: (e as Error).message }
    }
    setFixes((r) => r && { ...r, fixes: r.fixes.map((f, j) => (j === i ? next : f)) })
    setTesting(null)
  }

  const fastest = fixes?.fixes.reduce<number | undefined>(
    (best, f, i, all) =>
      f.afterMs !== undefined && f.rowsMatch !== false && (best === undefined || f.afterMs < all[best].afterMs!)
        ? i
        : best,
    undefined,
  )

  const tabs: [Tab, ReactNode][] = [
    ['plan', <>Plan{plan && <small className="n"> {fmtMs(plan.executionMs)} ms</small>}</>],
    ['diagnosis', 'Diagnosis'],
    ...(fixes?.fixes ?? []).map((f, i): [Tab, ReactNode] => [
      i,
      <>
        Fix {i + 1}{' '}
        {f.error !== undefined ? (
          <small className="error">failed</small>
        ) : f.estimate ? (
          <small>est.</small>
        ) : f.testable ? (
          <small className="todo">test</small>
        ) : f.skipped ? (
          <small>not run</small>
        ) : f.rowsMatch === false ? (
          <small className="warn">rows differ</small>
        ) : (
          <small className={i === fastest ? 'n best' : 'n'}>{speedup(fixes!.beforeMs, f.afterMs!)}×</small>
        )}
      </>,
    ]),
  ]

  let body: ReactNode
  if (tab === 'plan') {
    body = problems.length ? (
      <div className="stack">
        <div role="alert" className="problems">
          <p>Sargable will not run on this connection until these are fixed:</p>
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
        <p>Connect as a role that can only read:</p>
        <pre><code>{"CREATE ROLE sargable LOGIN PASSWORD '...';\nGRANT pg_read_all_data TO sargable;"}</code></pre>
        <p>Then connect as that role with the form on the left.</p>
      </div>
    ) : plan ? (
      <>
        <Finding ex={plan} />
        <PlanTree plan={plan.plan} bottleneckId={plan.bottleneckId} />
        <p className="note">
          Planning {fmtMs(plan.planningMs)} ms, execution {fmtMs(plan.executionMs)} ms. Parallel query is off for this
          run, so the self times add up to the total.
        </p>
      </>
    ) : busy === 'explain' ? (
      <div className="loading">{bars(1, 'line')}{bars(6, 'row')}</div>
    ) : planError ? (
      <p role="alert" className="error">Postgres could not run this query: {planError}</p>
    ) : (
      <p className="empty">Run a query to see where Postgres spends its time.</p>
    )
  } else if (tab === 'diagnosis') {
    body = fixes ? (
      <Diagnosis result={fixes} fastest={fastest} onOpen={setTab} />
    ) : busy === 'optimize' ? (
      <div className="loading">
        <p>
          Claude is reading the plan, then Postgres{' '}
          {live
            ? 'estimates each index without building it; rewrites wait for you to test them'
            : 'builds each proposed index and runs the query again'}
          . This takes 10 to 40 seconds.
        </p>
        {bars(1, 'line')}
        {bars(4, 'row')}
      </div>
    ) : fixesError ? (
      <p role="alert" className="error">Could not get fixes: {fixesError}</p>
    ) : (
      <p className="empty">Claude's diagnosis and the measured fixes land here after the plan.</p>
    )
  } else {
    body = (
      <FixPanel
        fix={fixes!.fixes[tab]}
        beforeMs={fixes!.beforeMs}
        fastest={tab === fastest}
        onTest={() => test(tab)}
        testing={testing === tab}
      />
    )
  }

  return (
    <main className="canvas">
      <aside className="side">
        <header className="head">
          <div className="brand">
            <svg className="mark" viewBox="0 0 20 20" aria-hidden="true">
              <rect x="2" y="3" width="16" height="3" rx="1" />
              <rect x="6" y="8.5" width="12" height="3" rx="1" />
              <rect x="10" y="14" width="8" height="3" rx="1" />
            </svg>
            <h1>Sargable</h1>
          </div>
          {conn && (
            <div className="conn">
              <span className="scope">
                {conn.database} on {conn.host}
                {conn.serverVersion && `, PostgreSQL ${conn.serverVersion.split(' ')[0]}`}
              </span>
              <span data-mode={conn.mode} className={`mode ${conn.mode}`}>
                {live ? 'Live, read-only' : 'Sandbox'}
              </span>
              {!showForm && (
                <button className="link" disabled={busy !== null || testing !== null} onClick={() => setEditing(true)}>
                  Change connection
                </button>
              )}
            </div>
          )}
        </header>

        {showForm ? (
          <ConnectionForm
            initial={JSON.parse(localStorage.getItem(REMEMBERED) ?? 'null') ?? undefined}
            busy={connecting}
            error={connectError}
            onSubmit={(d) => change(connect(d), d)}
            onDemo={() => change(resetConnection())}
            onCancel={problems.length ? undefined : () => setEditing(false)}
          />
        ) : (
          <section className="editor" aria-labelledby="query-h">
            <h2 id="query-h">Query</h2>
            <textarea aria-label="SQL query" value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} wrap="off" rows={11} />
            <div className="controls">
              {conn?.demo && (
                <select aria-label="Example queries" className="input" onChange={(e) => setSql(samples[+e.target.value].sql)}>
                  {samples.map((s, i) => (
                    <option key={s.label} value={i}>{s.label}</option>
                  ))}
                </select>
              )}
              <button className="btn primary" disabled={busy !== null || testing !== null || !sql.trim() || problems.length > 0} onClick={run}>
                {busy ? 'Running' : 'Run and find fixes'}
              </button>
            </div>
            <p data-disclosure className="disclosure">
              Claude receives the query and plan with literal values masked, plus table and column names. Row data is
              never sent.
            </p>
          </section>
        )}

        <section className="schema" aria-labelledby="schema-h">
          <h2 id="schema-h">Schema</h2>
          <dl>
            {tables.map((t) => (
              <div key={t.name}>
                <dt>
                  <span className="table" title={t.name}>{t.name}</span>
                  <span className="n">{t.rows.toLocaleString('en-US')}<small> rows</small></span>
                </dt>
                <dd>{t.columns.map((c) => c.name).join(', ')}</dd>
              </div>
            ))}
          </dl>
        </section>
      </aside>

      <section className="results" aria-label="Results">
        <div role="tablist" className="tabs">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              aria-controls={tab === id ? 'tabpanel' : undefined}
              aria-busy={(id === 'plan' && busy === 'explain') || (id === 'diagnosis' && busy === 'optimize') || id === testing}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="tabpanel" className="tabbody" tabIndex={0}>
          {body}
        </div>
      </section>
    </main>
  )
}
