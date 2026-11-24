import { cleanup, render, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fmtMs } from './api'
import Diagnosis from './Diagnosis'
import { expectSpeedup, fastestIndex, hasNumber, optimizeResult as result, speedups } from './fixtures'

afterEach(cleanup)

const rows = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>('[data-fix-row]')]

describe('Diagnosis', () => {
  it('F10 S14 B1 shows diagnosis, before time and one row per fix in API order with its state (testable rows are skipped)', () => {
    const { container } = render(<Diagnosis result={result} onOpen={() => {}} />)
    expect(container).toHaveTextContent(result.diagnosis)
    expect(container).toHaveTextContent(fmtMs(result.beforeMs))
    expect(rows(container)).toHaveLength(result.fixes.length)
    rows(container).forEach((row, i) => {
      const fix = result.fixes[i]
      const has = (attr: string) => row.querySelector(`[${attr}]`) !== null
      expect(row).toHaveTextContent(fix.title)
      if (fix.afterMs !== undefined) {
        expect(row).toHaveTextContent(fmtMs(fix.afterMs))
        expectSpeedup(row, result.beforeMs / fix.afterMs)
      } else if (fix.estimate) {
        const est = row.querySelector('[data-estimate]')!
        expect(hasNumber(est, fix.estimate.beforeCost.toLocaleString('en-US'))).toBe(true)
        expect(hasNumber(est, fix.estimate.afterCost.toLocaleString('en-US'))).toBe(true)
        expectSpeedup(est, fix.estimate.beforeCost / fix.estimate.afterCost)
      } else {
        expect(speedups(row)).toEqual([])
      }
      if (fix.skipped) expect(row.querySelector('[data-skipped]')).toHaveTextContent(fix.skipped)
      expect(has('data-error')).toBe(fix.error !== undefined)
      expect(has('data-warning')).toBe(fix.rowsMatch === false)
      expect(has('data-estimate')).toBe(fix.estimate !== undefined)
      expect(has('data-skipped')).toBe(fix.skipped !== undefined)
    })
  })

  it('F10 marks only the fastest row, none when fastest is undefined', () => {
    const { container, rerender } = render(<Diagnosis result={result} fastest={fastestIndex} onOpen={() => {}} />)
    expect(container.querySelectorAll('[data-fastest]')).toHaveLength(1)
    expect(rows(container)[fastestIndex].querySelector('[data-fastest]')).not.toBeNull()
    rerender(<Diagnosis result={result} fastest={0} onOpen={() => {}} />)
    expect(container.querySelectorAll('[data-fastest]')).toHaveLength(1)
    expect(rows(container)[0].querySelector('[data-fastest]')).not.toBeNull()
    rerender(<Diagnosis result={result} onOpen={() => {}} />)
    expect(container.querySelectorAll('[data-fastest]')).toHaveLength(0)
  })

  it('F10 each row has one button named after the fix that calls onOpen with its index', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    const { container } = render(<Diagnosis result={result} onOpen={onOpen} />)
    for (const [i, row] of rows(container).entries()) {
      const buttons = within(row).getAllByRole('button')
      expect(buttons).toHaveLength(1)
      expect(within(row).getByRole('button', { name: (name) => name.includes(result.fixes[i].title) })).toBe(buttons[0])
      await user.click(buttons[0])
      expect(onOpen).toHaveBeenLastCalledWith(i)
    }
    expect(onOpen).toHaveBeenCalledTimes(result.fixes.length)
  })
})
