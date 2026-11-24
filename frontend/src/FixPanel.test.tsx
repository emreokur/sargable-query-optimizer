import { cleanup, render, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fmtMs, type Fix, type PlanNode } from './api'
import FixPanel from './FixPanel'
import { expectSpeedup, hasNumber, optimizeResult as result, speedups, testableIndex } from './fixtures'

afterEach(cleanup)

const { beforeMs, fixes } = result
const count = (n: PlanNode): number => 1 + n.children.reduce((s, c) => s + count(c), 0)
const panel = (fix: Fix, fastest = false) => render(<FixPanel fix={fix} beforeMs={beforeMs} fastest={fastest} />)

describe('FixPanel', () => {
  it('F10 shows title, rationale, ddl and rewritten sql on a data-fix root', () => {
    for (const fix of fixes) {
      const { container, unmount } = panel(fix)
      expect(container.firstElementChild).toHaveAttribute('data-fix')
      expect(container.querySelectorAll('[data-fix]')).toHaveLength(1)
      expect(container).toHaveTextContent(fix.title)
      expect(container).toHaveTextContent(fix.rationale)
      for (const stmt of fix.ddl) expect(container).toHaveTextContent(stmt)
      if (fix.kind === 'rewrite') expect(container).toHaveTextContent(fix.sql)
      unmount()
    }
  })

  it('F10 timed fix shows before, after, speed-up and its own plan without a bottleneck mark', () => {
    for (const fix of fixes.filter((f) => f.afterMs !== undefined)) {
      const { container, unmount } = panel(fix)
      expect(container).toHaveTextContent(fmtMs(beforeMs))
      expect(container).toHaveTextContent(fmtMs(fix.afterMs!))
      expectSpeedup(container, beforeMs / fix.afterMs!)
      expect(container.querySelectorAll('[data-node-id]')).toHaveLength(count(fix.plan!))
      expect(container.querySelectorAll('[aria-current]')).toHaveLength(0)
      unmount()
    }
  })

  it('F10 failed fix shows the error and no speed-up or plan rows', () => {
    const { container } = panel(fixes[3])
    expect(container.querySelector('[data-error]')).toHaveTextContent(fixes[3].error!)
    expect(speedups(container)).toEqual([])
    expect(container.querySelector('[data-node-id]')).toBeNull()
  })

  it('S14 estimated fix shows both costs with separators and their ratio, no plan rows', () => {
    for (const fix of fixes.filter((f) => f.estimate)) {
      const { container, unmount } = panel(fix)
      const est = container.querySelector('[data-estimate]')!
      expect(est).not.toBeNull()
      const { beforeCost, afterCost } = fix.estimate!
      expect(hasNumber(est, Math.round(beforeCost).toLocaleString('en-US'))).toBe(true)
      expect(hasNumber(est, Math.round(afterCost).toLocaleString('en-US'))).toBe(true)
      expectSpeedup(est, beforeCost / afterCost)
      expect(container.querySelector('[data-node-id]')).toBeNull()
      unmount()
    }
  })

  it('S14 skipped fix shows the reason, no speed-up and no plan rows', () => {
    const { container } = panel(fixes[5])
    expect(container.querySelector('[data-skipped]')).toHaveTextContent(fixes[5].skipped!)
    expect(speedups(container)).toEqual([])
    expect(container.querySelector('[data-node-id]')).toBeNull()
  })

  it('F10 S14 error, warning, estimate, skipped and fastest markers appear only when they apply', () => {
    for (const fix of fixes) {
      for (const fastest of [true, false]) {
        const { container, unmount } = panel(fix, fastest)
        const has = (attr: string) => container.querySelector(`[${attr}]`) !== null
        expect(has('data-fastest')).toBe(fastest)
        expect(has('data-warning')).toBe(fix.rowsMatch === false)
        expect(has('data-error')).toBe(fix.error !== undefined)
        expect(has('data-estimate')).toBe(fix.estimate !== undefined)
        expect(has('data-skipped')).toBe(fix.skipped !== undefined)
        unmount()
      }
    }
  })

  it('B1 a testable fix offers a Test button inside its skipped state that calls onTest and is disabled while testing', async () => {
    const user = userEvent.setup()
    const onTest = vi.fn()
    const fix = fixes[testableIndex]
    const { container, rerender } = render(<FixPanel fix={fix} beforeMs={beforeMs} fastest={false} onTest={onTest} />)
    const skipped = container.querySelector<HTMLElement>('[data-skipped]')!
    expect(skipped).toHaveTextContent(fix.skipped!)
    const button = within(skipped).getByRole('button', { name: /^Test/ })
    expect(button).toBeEnabled()
    await user.click(button)
    expect(onTest).toHaveBeenCalledTimes(1)
    rerender(<FixPanel fix={fix} beforeMs={beforeMs} fastest={false} onTest={onTest} testing />)
    expect(within(container).getByRole('button', { name: /^Test/ })).toBeDisabled()
    await user.click(within(container).getByRole('button', { name: /^Test/ }))
    expect(onTest).toHaveBeenCalledTimes(1)
  })

  it('B1 no Test button for any fix that is not testable', () => {
    for (const fix of fixes.filter((f) => !f.testable)) {
      const { container, unmount } = render(<FixPanel fix={fix} beforeMs={beforeMs} fastest={false} onTest={() => {}} />)
      expect(within(container).queryByRole('button', { name: /^Test/ })).toBeNull()
      unmount()
    }
  })
})
