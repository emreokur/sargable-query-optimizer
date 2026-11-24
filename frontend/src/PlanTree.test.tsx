import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { PlanNode } from './api'
import { plan } from './fixtures'
import PlanTree from './PlanTree'

afterEach(cleanup)

const nodes: PlanNode[] = []
const walk = (n: PlanNode) => { nodes.push(n); n.children.forEach(walk) }
walk(plan)

const row = (c: HTMLElement, id: number) => c.querySelector<HTMLElement>(`[data-node-id="${id}"]`)!

describe('PlanTree', () => {
  it('F9 renders every node exactly once, each row without its children rows', () => {
    const { container } = render(<PlanTree plan={plan} bottleneckId={3} />)
    expect(container.querySelectorAll('[data-node-id]')).toHaveLength(nodes.length)
    for (const n of nodes) {
      expect(container.querySelectorAll(`[data-node-id="${n.id}"]`)).toHaveLength(1)
      const r = row(container, n.id)
      expect(r.querySelector('[data-node-id]')).toBeNull()
      expect(r).toHaveTextContent(n.type)
      if (n.relation) expect(r).toHaveTextContent(n.relation)
    }
  })

  it('F9 marks only the bottleneck row with aria-current, none when bottleneckId is omitted', () => {
    const { container, rerender } = render(<PlanTree plan={plan} bottleneckId={3} />)
    for (const bottleneck of [3, 5, 0]) {
      rerender(<PlanTree plan={plan} bottleneckId={bottleneck} />)
      expect(container.querySelectorAll('[aria-current]')).toHaveLength(1)
      expect(row(container, bottleneck)).toHaveAttribute('aria-current', 'true')
    }
    rerender(<PlanTree plan={plan} />)
    expect(container.querySelectorAll('[aria-current]')).toHaveLength(0)
  })

  it('F9 sizes one bar per row in proportion to selfMs on one scale', () => {
    const { container } = render(<PlanTree plan={plan} bottleneckId={3} />)
    const widths = nodes.map((n) => {
      const bars = row(container, n.id).querySelectorAll<HTMLElement>('[data-bar]')
      expect(bars).toHaveLength(1)
      expect(bars[0].style.width).toMatch(/%$/)
      return parseFloat(bars[0].style.width)
    })
    const scale = widths[3] / nodes[3].selfMs
    expect(scale).toBeGreaterThan(0)
    nodes.forEach((n, i) => {
      expect(widths[i]).toBeLessThanOrEqual(100)
      expect(Math.abs(widths[i] - n.selfMs * scale)).toBeLessThanOrEqual(1)
    })
    expect(widths[4]).toBe(0)
  })
})
