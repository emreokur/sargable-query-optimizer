import { fmtMs, type Fix } from './api'
import PlanTree from './PlanTree'
import { speedup } from './speedup'

const cost = (n: number) => Math.round(n).toLocaleString('en-US')

function Estimate({ beforeCost, afterCost, usesIndex }: NonNullable<Fix['estimate']>) {
  const scale = Math.max(beforeCost, afterCost)
  return (
    <div data-estimate className="stack">
      <div className="bars">
        <span>Before</span>
        <span className="track"><span className="before" style={{ width: `${(beforeCost / scale) * 100}%` }} /></span>
        <span className="n">{cost(beforeCost)}<small> cost</small></span>
        <span>After</span>
        <span className="track"><span className="after" style={{ width: `${(afterCost / scale) * 100}%` }} /></span>
        <span className="n">{cost(afterCost)}<small> cost</small></span>
      </div>
      <p className="speedup">
        <span className="n">{speedup(beforeCost, afterCost)}×</span> {afterCost < beforeCost ? 'lower' : 'no lower'} planner cost
      </p>
      <p className="muted">
        This is the planner's estimate with a hypothetical index, not a measurement. The index was not built.
        {!usesIndex && ' The planner would not use this index.'}
      </p>
    </div>
  )
}

type Props = { fix: Fix; beforeMs: number; fastest: boolean; onTest?: () => void; testing?: boolean }

export default function FixPanel({ fix, beforeMs, fastest, onTest, testing }: Props) {
  const after = fix.afterMs
  const scale = Math.max(beforeMs, after ?? 0)
  return (
    <article data-fix className="fix">
      <header>
        <h2>{fix.title}</h2>
        <span className="pill">{fix.kind === 'index' ? 'Index' : 'Rewrite'}</span>
        {fastest && <span data-fastest className="pill pos">Fastest</span>}
      </header>
      <p>{fix.rationale}</p>
      {fix.ddl.length > 0 && <pre><code>{fix.ddl.join('\n')}</code></pre>}
      {fix.kind === 'rewrite' && <pre><code>{fix.sql}</code></pre>}
      {fix.error && <p data-error className="error">Postgres rejected this fix: {fix.error}</p>}
      {fix.rowsMatch === false && (
        <p data-warning className="warning">
          Returns {fix.plan!.rows.toLocaleString('en-US')} rows, not the same count as your query, so it changes the
          result.
        </p>
      )}
      {fix.skipped && (
        <div data-skipped className="skipped">
          <p className="muted">{fix.testable ? fix.skipped : `Not measured: ${fix.skipped}`}</p>
          {fix.testable && (
            <button className="btn secondary" disabled={testing} onClick={onTest}>
              {testing ? 'Testing this rewrite' : 'Test this rewrite'}
            </button>
          )}
        </div>
      )}
      {fix.estimate && <Estimate {...fix.estimate} />}
      {after !== undefined && (
        <>
          <div className="bars">
            <span>Before</span>
            <span className="track"><span className="before" style={{ width: `${(beforeMs / scale) * 100}%` }} /></span>
            <span className="n">{fmtMs(beforeMs)}<small> ms</small></span>
            <span>After</span>
            <span className="track"><span className="after" style={{ width: `${(after / scale) * 100}%` }} /></span>
            <span className="n">{fmtMs(after)}<small> ms</small></span>
          </div>
          <p className="speedup">
            <span className="n">{speedup(beforeMs, after)}×</span> {after <= beforeMs ? 'faster' : 'slower'}
          </p>
          <h3>Plan with this fix</h3>
          <PlanTree plan={fix.plan!} />
        </>
      )}
    </article>
  )
}
