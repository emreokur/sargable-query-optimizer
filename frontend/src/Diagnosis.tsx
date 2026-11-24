import { fmtMs, type Optimize } from './api'
import { speedup } from './speedup'

const cost = (n: number) => Math.round(n).toLocaleString('en-US')

type Props = { result: Optimize; fastest?: number; onOpen: (index: number) => void }

export default function Diagnosis({ result, fastest, onOpen }: Props) {
  const { beforeMs, fixes } = result
  const scale = Math.max(beforeMs, ...fixes.map((f) => f.afterMs ?? 0))
  const bar = (ms: number, className: string) => (
    <span className="track"><span className={className} style={{ width: `${(ms / scale) * 100}%` }} /></span>
  )

  return (
    <>
      <p className="diagnosis">{result.diagnosis}</p>
      <ul className="compare">
        <li className="cmp">
          <span>
            Your query <small>run again beside the fixes</small>
          </span>
          {bar(beforeMs, 'before')}
          <span className="n">{fmtMs(beforeMs)}<small> ms</small></span>
          <span className="n muted">-</span>
        </li>
        {fixes.map((f, i) => (
          <li key={i} data-fix-row>
            <button className="cmp" onClick={() => onOpen(i)}>
              <span className="name">
                {f.title}
                <span className="pill">{f.kind === 'index' ? 'Index' : 'Rewrite'}</span>
                {i === fastest && <span data-fastest className="pill pos">Fastest</span>}
                {f.rowsMatch === false && <span data-warning className="pill warn">Rows differ</span>}
                {f.estimate && <span className="pill">Estimate</span>}
              </span>
              {f.error !== undefined ? (
                <span data-error className="error wide">Failed: {f.error}</span>
              ) : f.estimate ? (
                <span data-estimate className="wide est">
                  <span>
                    Estimated planner cost {cost(f.estimate.beforeCost)} to {cost(f.estimate.afterCost)}
                    {!f.estimate.usesIndex && ', index not used'}
                  </span>{' '}
                  <span className="n">{speedup(f.estimate.beforeCost, f.estimate.afterCost)}×</span>
                </span>
              ) : f.skipped ? (
                <span data-skipped className="wide muted">{f.skipped}</span>
              ) : (
                <>
                  {bar(f.afterMs!, 'after')}
                  <span className="n">{fmtMs(f.afterMs!)}<small> ms</small></span>
                  <span className="n">{speedup(beforeMs, f.afterMs!)}×</span>
                </>
              )}
            </button>
          </li>
        ))}
      </ul>
    </>
  )
}
