import type { CSSProperties } from 'react'
import { fmtMs, type PlanNode } from './api'

const flatten = (n: PlanNode, depth = 0): [PlanNode, number][] => [
  [n, depth],
  ...n.children.flatMap((c) => flatten(c, depth + 1)),
]
const count = (n: number) => n.toLocaleString('en-US')

export default function PlanTree({ plan, bottleneckId }: { plan: PlanNode; bottleneckId?: number }) {
  const nodes = flatten(plan)
  const max = Math.max(...nodes.map(([n]) => n.selfMs)) || 1

  return (
    <table className="plan">
      <thead>
        <tr>
          <th>Node</th>
          <th className="n">Rows</th>
          <th className="n">Planned</th>
          <th className="n">Filtered out</th>
          <th className="n">Self time</th>
          <th>
            <span className="sr">Share of time</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {nodes.map(([n, depth]) => (
          <tr key={n.id} data-node-id={n.id} aria-current={n.id === bottleneckId || undefined}>
            <td className="node">
              <div style={{ '--depth': depth } as CSSProperties}>
                <span className="type">{n.type}</span>
                {n.index && <> using <code>{n.index}</code></>}
                {n.relation && <> on {n.relation}</>}
                {n.detail && <code className="detail">{n.detail}</code>}
              </div>
            </td>
            <td className="n" data-label="Rows">{count(n.rows)}</td>
            <td className="n muted" data-label="Planned">{count(n.planRows)}</td>
            <td className="n" data-label="Filtered out">
              {n.rowsRemoved === undefined ? <span className="muted">-</span> : count(n.rowsRemoved)}
            </td>
            <td className="n self">
              {fmtMs(n.selfMs)}
              <small> ms</small>
            </td>
            <td className="bar">
              <span data-bar style={{ width: `${(n.selfMs / max) * 100}%` }} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
