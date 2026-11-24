import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import App from './App'
import {
  demoConnection, EM_DASH, explainResult, fastestIndex, liveConnection, optimizeResult, refusedConnection, speedups, tables, expectSpeedup,
  otherConnection, testableIndex, testedExplain,
} from './fixtures'
import { samples } from './samples'

vi.mock('./api', async () => ({
  fmtMs: (await vi.importActual<typeof import('./api')>('./api')).fmtMs,
  getConnection: vi.fn(),
  connect: vi.fn(),
  resetConnection: vi.fn(),
  getSchema: vi.fn(),
  explain: vi.fn(),
  optimize: vi.fn(),
}))
const mocked = vi.mocked(api)

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  mocked.getConnection.mockResolvedValue(demoConnection)
  mocked.getSchema.mockResolvedValue(tables)
})
afterEach(cleanup)

const textarea = () => screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'SQL query' })
const run = () => screen.getByRole('button', { name: /^Run/ })
const results = () => screen.getByRole('region', { name: 'Results' })
const tabs = () => within(results()).getAllByRole('tab')
const tab = (name: RegExp) => within(results()).getByRole('tab', { name })
const fixTab = (i: number) => tab(new RegExp(`^Fix ${i + 1}\\b`))
const panel = () => {
  const panels = results().querySelectorAll<HTMLElement>('[role="tabpanel"]')
  expect(panels).toHaveLength(1)
  return panels[0]
}
const expectSelected = (name: RegExp) =>
  expect(within(results()).getAllByRole('tab', { selected: true })).toEqual([tab(name)])

async function ready() {
  render(<App />)
  await waitFor(() => expect(textarea().value).toBe(samples[0].sql))
  return userEvent.setup()
}

async function runToEnd(result = optimizeResult) {
  mocked.explain.mockResolvedValue(explainResult)
  mocked.optimize.mockResolvedValue(result)
  const user = await ready()
  await user.click(run())
  await waitFor(() => expect(tabs()).toHaveLength(2 + result.fixes.length))
  return user
}

describe('App', () => {
  it('F11 S14 initial state: empty until the demo connection loads, then first sample, tabs, empty panels, schema shown', async () => {
    const conn = deferred<api.Connection>()
    mocked.getConnection.mockReturnValue(conn.promise)
    render(<App />)
    expect(textarea().value).toBe('')
    expect(run()).toBeDisabled()
    conn.resolve(demoConnection)
    await waitFor(() => expect(textarea().value).toBe(samples[0].sql))
    const user = userEvent.setup()
    expect(run()).toBeEnabled()
    expect(within(results()).getAllByRole('tablist')).toHaveLength(1)
    expect(tabs()).toHaveLength(2)
    expectSelected(/^Plan/)
    for (const name of [/^Plan/, /^Diagnosis/]) expect(tab(name)).not.toHaveAttribute('aria-busy', 'true')
    expect(within(panel()).queryByRole('alert')).toBeNull()
    expect(panel().querySelector('[data-node-id]')).toBeNull()
    await user.click(tab(/^Diagnosis/))
    expectSelected(/^Diagnosis/)
    expect(within(panel()).queryByRole('alert')).toBeNull()
    expect(panel().querySelector('[data-fix-row]')).toBeNull()
    expect(mocked.explain).not.toHaveBeenCalled()
    await waitFor(() => expect(document.body).toHaveTextContent(tables[0].name))
    expect(mocked.getSchema).toHaveBeenCalledTimes(1)
    expect(mocked.getConnection).toHaveBeenCalledTimes(1)
  })

  it('F11 Example queries select lists every sample in order and fills the textarea', async () => {
    const user = await ready()
    expect(samples.length).toBeGreaterThanOrEqual(9)
    const select = screen.getByRole('combobox', { name: 'Example queries' })
    const options = within(select).getAllByRole<HTMLOptionElement>('option')
    expect(options.map((o) => o.textContent)).toEqual(samples.map((s) => s.label))
    for (const i of [...samples.keys()].reverse()) {
      await user.selectOptions(select, options[i])
      expect(textarea().value).toBe(samples[i].sql)
    }
  })

  it('F11 run: explain then optimize with busy tabs, Run disabled, Plan then Diagnosis selected, fix tabs replaced on rerun', async () => {
    const user = userEvent.setup()
    const ex = deferred<api.Explain>()
    const opt = deferred<api.Optimize>()
    mocked.explain.mockReturnValue(ex.promise)
    mocked.optimize.mockReturnValue(opt.promise)
    await ready()
    await user.clear(textarea())
    await user.type(textarea(), 'SELECT 42')
    await user.click(tab(/^Diagnosis/))
    await user.click(run())

    expect(mocked.explain).toHaveBeenCalledWith('SELECT 42')
    expect(mocked.optimize).not.toHaveBeenCalled()
    expect(run()).toBeDisabled()
    expectSelected(/^Plan/)
    expect(tab(/^Plan/)).toHaveAttribute('aria-busy', 'true')
    expect(tabs()).toHaveLength(2)

    ex.resolve(explainResult)
    await waitFor(() => expect(mocked.optimize).toHaveBeenCalledWith('SELECT 42'))
    await waitFor(() => expect(panel().querySelectorAll('[data-node-id]')).toHaveLength(6))
    expect(panel().querySelector(`[data-node-id="${explainResult.bottleneckId}"]`)).toHaveAttribute('aria-current', 'true')
    expect(tab(/^Plan/)).not.toHaveAttribute('aria-busy', 'true')
    expect(tab(/^Diagnosis/)).toHaveAttribute('aria-busy', 'true')
    expectSelected(/^Plan/)
    expect(run()).toBeDisabled()
    expect(tabs()).toHaveLength(2)

    opt.resolve(optimizeResult)
    await waitFor(() => expectSelected(/^Diagnosis/))
    expect(tab(/^Diagnosis/)).not.toHaveAttribute('aria-busy', 'true')
    expect(run()).toBeEnabled()
    expect(panel().querySelectorAll('[data-fix-row]')).toHaveLength(optimizeResult.fixes.length)
    expect(tabs()).toHaveLength(2 + optimizeResult.fixes.length)
    for (let i = 1; i < optimizeResult.fixes.length; i++) {
      expect(fixTab(i - 1).compareDocumentPosition(fixTab(i)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    expect(mocked.explain).toHaveBeenCalledTimes(1)
    expect(mocked.optimize).toHaveBeenCalledTimes(1)

    mocked.explain.mockReturnValue(deferred<api.Explain>().promise)
    await user.click(run())
    expectSelected(/^Plan/)
    expect(tabs()).toHaveLength(2)
  })

  it('F11 tabs: clicking selects, each fix tab shows that fix, Diagnosis row button opens its fix, fastest wired', async () => {
    const user = await runToEnd()
    const diagnosisRows = () => [...panel().querySelectorAll('[data-fix-row]')]
    expect(diagnosisRows().map((r) => r.querySelector('[data-fastest]') !== null))
      .toEqual(optimizeResult.fixes.map((_, i) => i === fastestIndex))

    for (const [i, fix] of optimizeResult.fixes.entries()) {
      await user.click(fixTab(i))
      expectSelected(new RegExp(`^Fix ${i + 1}\\b`))
      const fixes = panel().querySelectorAll('[data-fix]')
      expect(fixes).toHaveLength(1)
      expect(fixes[0]).toHaveTextContent(fix.title)
      expect(panel().querySelector('[data-fastest]') !== null).toBe(i === fastestIndex)
    }

    await user.click(tab(/^Plan/))
    expectSelected(/^Plan/)
    expect(panel().querySelectorAll('[data-node-id]')).toHaveLength(6)

    await user.click(tab(/^Diagnosis/))
    await user.click(within(diagnosisRows()[1] as HTMLElement).getByRole('button'))
    expectSelected(/^Fix 2\b/)
    expect(panel().querySelector('[data-fix]')).toHaveTextContent(optimizeResult.fixes[1].title)
  })

  it('F11 no fastest marker anywhere when no measured fix keeps the row count', async () => {
    const result = { ...optimizeResult, fixes: [optimizeResult.fixes[1], optimizeResult.fixes[3]] }
    const user = await runToEnd(result)
    expect(panel().querySelector('[data-fastest]')).toBeNull()
    for (const i of [0, 1]) {
      await user.click(fixTab(i))
      expect(panel().querySelector('[data-fastest]')).toBeNull()
    }
  })

  it('F12 a full run renders no em dash in any tab', async () => {
    const user = await runToEnd()
    for (const i of tabs().keys()) {
      await user.click(tabs()[i])
      expect(document.body.textContent).not.toContain(EM_DASH)
    }
  })

  it('F11 explain failure: alert under Plan, Run re-enabled, optimize never called', async () => {
    mocked.explain.mockRejectedValue(new Error('syntax error at or near "SELEC"'))
    const user = await ready()
    await user.click(run())
    expect(await within(panel()).findByRole('alert')).toHaveTextContent('syntax error at or near "SELEC"')
    expectSelected(/^Plan/)
    expect(run()).toBeEnabled()
    expect(tab(/^Plan/)).not.toHaveAttribute('aria-busy', 'true')
    expect(mocked.optimize).not.toHaveBeenCalled()
    expect(tabs()).toHaveLength(2)
  })

  it('F11 optimize failure: alert under Diagnosis, plan still under Plan', async () => {
    mocked.explain.mockResolvedValue(explainResult)
    mocked.optimize.mockRejectedValue(new Error('Claude declined to answer this request'))
    const user = await ready()
    await user.click(run())
    await waitFor(() => expectSelected(/^Diagnosis/))
    expect(within(panel()).getByRole('alert')).toHaveTextContent('Claude declined to answer this request')
    expect(panel().querySelector('[data-fix-row]')).toBeNull()
    expect(run()).toBeEnabled()
    expect(tab(/^Diagnosis/)).not.toHaveAttribute('aria-busy', 'true')
    expect(tabs()).toHaveLength(2)
    expect(mocked.optimize).toHaveBeenCalledWith(samples[0].sql)
    await user.click(tab(/^Plan/))
    expect(panel().querySelectorAll('[data-node-id]')).toHaveLength(6)
    expect(within(panel()).queryByRole('alert')).toBeNull()
  })

  it('S14 demo connection: sandbox mode shown with database and host, examples offered', async () => {
    await ready()
    const mode = document.querySelectorAll('[data-mode]')
    expect(mode).toHaveLength(1)
    expect(mode[0]).toHaveAttribute('data-mode', 'sandbox')
    expect(document.body).toHaveTextContent(demoConnection.database)
    expect(document.body).toHaveTextContent(demoConnection.host)
    expect(screen.getByRole('combobox', { name: 'Example queries' })).toBeInTheDocument()
  })

  it('S14 live connection: read-only mode shown, no examples, textarea stays empty and Run disabled', async () => {
    mocked.getConnection.mockResolvedValue(liveConnection)
    render(<App />)
    await waitFor(() => expect(document.querySelector('[data-mode]')).toHaveAttribute('data-mode', 'live'))
    expect(document.querySelectorAll('[data-mode]')).toHaveLength(1)
    expect(document.querySelector('[data-mode]')).toHaveTextContent(/read.?only/i)
    expect(document.body).toHaveTextContent(liveConnection.database)
    expect(document.body).toHaveTextContent(liveConnection.host)
    expect(screen.queryByRole('combobox', { name: 'Example queries' })).toBeNull()
    expect(textarea().value).toBe('')
    expect(run()).toBeDisabled()
  })

  it('S14 typing before the demo connection loads is not overwritten', async () => {
    const conn = deferred<api.Connection>()
    mocked.getConnection.mockReturnValue(conn.promise)
    const user = userEvent.setup()
    render(<App />)
    await user.type(textarea(), 'SELECT 7')
    conn.resolve(demoConnection)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Example queries' })).toBeInTheDocument())
    expect(textarea().value).toBe('SELECT 7')
  })

  it('S14 disclosure says literals are masked, before and after the connection loads', async () => {
    const conn = deferred<api.Connection>()
    mocked.getConnection.mockReturnValue(conn.promise)
    render(<App />)
    expect(document.querySelector('[data-disclosure]')).toHaveTextContent(/masked/)
    conn.resolve(liveConnection)
    await waitFor(() => expect(document.querySelector('[data-mode]')).not.toBeNull())
    expect(document.querySelector('[data-disclosure]')).toHaveTextContent(/masked/)
  })

  it('S14 Run is disabled for an empty or whitespace query', async () => {
    const user = await ready()
    await user.clear(textarea())
    expect(run()).toBeDisabled()
    await user.type(textarea(), '   {Enter}  ')
    expect(run()).toBeDisabled()
    await user.click(run())
    await user.type(textarea(), 'SELECT 1')
    expect(run()).toBeEnabled()
    expect(mocked.explain).not.toHaveBeenCalled()
  })

  it('S14 C11 connection problems: every problem in an alert under Plan, the form opens by itself, nothing can run', async () => {
    mocked.getConnection.mockResolvedValue(refusedConnection)
    render(<App />)
    const alert = await within(panel()).findByRole('alert')
    for (const problem of refusedConnection.problems) expect(alert).toHaveTextContent(problem)
    expect(screen.getByRole('form', { name: 'Database connection' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'SQL query' })).toBeNull()
    const runButton = screen.queryByRole('button', { name: /^Run/ })
    if (runButton) expect(runButton).toBeDisabled()
    expect(mocked.explain).not.toHaveBeenCalled()
    expect(mocked.optimize).not.toHaveBeenCalled()
  })

  it('S14 fix tabs show a speed-up only for timed fixes, a mark instead for estimate and skipped', async () => {
    await runToEnd()
    expectSpeedup(fixTab(fastestIndex), optimizeResult.beforeMs / optimizeResult.fixes[fastestIndex].afterMs!)
    for (const i of [4, 5, 6]) {
      expect(speedups(fixTab(i))).toEqual([])
      const mark = fixTab(i).textContent!.replace(/^\s*Fix \d+/, '').replace(optimizeResult.fixes[i].title, '')
      expect(mark).toMatch(/\w/)
    }
  })

  async function startTest() {
    const user = await runToEnd()
    const pending = deferred<api.Explain>()
    mocked.explain.mockReturnValue(pending.promise)
    await user.click(fixTab(testableIndex))
    await user.click(within(panel()).getByRole('button', { name: /^Test/ }))
    return { user, pending }
  }

  it('B1 Test runs explain on that fix only, marks its tab busy and disables Run until it settles', async () => {
    const { pending } = await startTest()
    expect(mocked.explain).toHaveBeenCalledTimes(2)
    expect(mocked.explain).toHaveBeenLastCalledWith(optimizeResult.fixes[testableIndex].sql)
    expect(mocked.optimize).toHaveBeenCalledTimes(1)
    expect(fixTab(testableIndex)).toHaveAttribute('aria-busy', 'true')
    expect(tabs().filter((t) => t.getAttribute('aria-busy') === 'true')).toEqual([fixTab(testableIndex)])
    expect(run()).toBeDisabled()
    pending.resolve(testedExplain(explainResult.plan.rows))
    await waitFor(() => expect(run()).toBeEnabled())
    expect(fixTab(testableIndex)).not.toHaveAttribute('aria-busy', 'true')
  })

  it('B1 a tested rewrite with the same row count becomes timed everywhere and the fastest', async () => {
    const { user, pending } = await startTest()
    const tested = testedExplain(explainResult.plan.rows)
    pending.resolve(tested)
    const expected = optimizeResult.beforeMs / tested.executionMs
    await waitFor(() => expect(panel().querySelectorAll('[data-node-id]')).toHaveLength(1))
    expect(panel().querySelector('[data-skipped]')).toBeNull()
    expect(panel().querySelector('[data-warning]')).toBeNull()
    expect(within(panel()).queryByRole('button', { name: /^Test/ })).toBeNull()
    expect(panel().querySelector('[data-fastest]')).not.toBeNull()
    expectSpeedup(panel(), expected)
    expectSpeedup(fixTab(testableIndex), expected)
    await user.click(tab(/^Diagnosis/))
    const rows = [...panel().querySelectorAll('[data-fix-row]')]
    expectSpeedup(rows[testableIndex], expected)
    expect(rows[testableIndex].querySelector('[data-skipped]')).toBeNull()
    expect(rows.map((r) => r.querySelector('[data-fastest]') !== null)).toEqual(rows.map((_, i) => i === testableIndex))
    await user.click(fixTab(fastestIndex))
    expect(panel().querySelector('[data-fastest]')).toBeNull()
  })

  it('B1 a tested rewrite with a different row count is timed, warned and never the fastest', async () => {
    const { user, pending } = await startTest()
    pending.resolve(testedExplain(explainResult.plan.rows + 7))
    await waitFor(() => expect(panel().querySelector('[data-warning]')).not.toBeNull())
    expect(panel().querySelector('[data-skipped]')).toBeNull()
    expect(panel().querySelector('[data-fastest]')).toBeNull()
    await user.click(tab(/^Diagnosis/))
    const rows = [...panel().querySelectorAll('[data-fix-row]')]
    expect(rows[testableIndex].querySelector('[data-warning]')).not.toBeNull()
    expect(rows.map((r) => r.querySelector('[data-fastest]') !== null)).toEqual(rows.map((_, i) => i === fastestIndex))
  })

  it('B1 a failed test shows the error and removes the Test button', async () => {
    const { user, pending } = await startTest()
    pending.reject(new Error('permission denied for function pg_terminate_backend'))
    await waitFor(() => expect(panel().querySelector('[data-error]')).toHaveTextContent('permission denied for function pg_terminate_backend'))
    expect(within(panel()).queryByRole('button', { name: /^Test/ })).toBeNull()
    expect(panel().querySelector('[data-skipped]')).toBeNull()
    expect(run()).toBeEnabled()
    await user.click(tab(/^Diagnosis/))
    expect(panel().querySelectorAll('[data-fix-row]')[testableIndex].querySelector('[data-error]')).not.toBeNull()
  })

  it('B1 a new run discards tested results', async () => {
    const { user, pending } = await startTest()
    pending.resolve(testedExplain(explainResult.plan.rows))
    await waitFor(() => expect(run()).toBeEnabled())
    mocked.explain.mockResolvedValue(explainResult)
    await user.click(run())
    await waitFor(() => expectSelected(/^Diagnosis/))
    expect(panel().querySelectorAll('[data-fix-row]')[testableIndex].querySelector('[data-skipped]')).not.toBeNull()
    await user.click(fixTab(testableIndex))
    expect(within(panel()).getByRole('button', { name: /^Test/ })).toBeEnabled()
    expect(panel().querySelector('[data-node-id]')).toBeNull()
  })

  const connectionForm = () => screen.queryByRole('form', { name: 'Database connection' })
  const formField = (name: string) => within(connectionForm()!).getByLabelText<HTMLInputElement>(name)
  const CANARY = '  Cn4ry-FE-pw  '
  const CA_TEXT = '-----BEGIN CERTIFICATE-----\nMIIBcaFixtureCanary\n-----END CERTIFICATE-----\n'
  const TYPED = { host: 'db.internal.example', port: 6432, database: 'orders_prod', user: 'orders_ro', sslmode: 'verify-full' as const }

  async function openForm(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: /^Change connection/ }))
    expect(connectionForm()).not.toBeNull()
  }

  async function fillAndConnect(user: ReturnType<typeof userEvent.setup>, password = CANARY) {
    for (const [name, value] of [['Host', TYPED.host], ['Port', String(TYPED.port)], ['Database', TYPED.database],
      ['User', TYPED.user], ['Password', password]]) {
      await user.clear(formField(name))
      await user.type(formField(name), value)
    }
    await user.selectOptions(within(connectionForm()!).getByRole('combobox', { name: 'SSL mode' }), TYPED.sslmode)
    await user.click(within(connectionForm()!).getByRole('textbox', { name: 'CA certificate (optional)' }))
    await user.paste(CA_TEXT)
    await user.click(within(connectionForm()!).getByRole('checkbox', { name: /^Sandbox/ }))
    await user.click(within(connectionForm()!).getByRole('button', { name: /^Connect/ }))
  }

  it('C11 Change connection opens the form in place of the editor, Cancel brings the editor back', async () => {
    const user = await ready()
    expect(connectionForm()).toBeNull()
    await openForm(user)
    expect(screen.queryByRole('textbox', { name: 'SQL query' })).toBeNull()
    await user.click(within(connectionForm()!).getByRole('button', { name: 'Cancel' }))
    expect(connectionForm()).toBeNull()
    expect(textarea()).toBeInTheDocument()
    expect(mocked.connect).not.toHaveBeenCalled()
  })

  it('C11 a clean connect replaces the connection, reloads the schema and clears query, plan and fixes', async () => {
    const user = await runToEnd()
    const pending = deferred<api.Connection>()
    mocked.connect.mockReturnValue(pending.promise)
    await openForm(user)
    await fillAndConnect(user)
    expect(within(connectionForm()!).getByRole('button', { name: /^Connect/ })).toBeDisabled()
    expect(mocked.getSchema).toHaveBeenCalledTimes(1)
    pending.resolve(otherConnection)
    await waitFor(() => expect(connectionForm()).toBeNull())
    expect(mocked.getSchema).toHaveBeenCalledTimes(2)
    expect(textarea().value).toBe('')
    expect(document.querySelector('[data-mode]')).toHaveAttribute('data-mode', 'live')
    expect(document.body).toHaveTextContent(otherConnection.database)
    expect(document.body).toHaveTextContent(otherConnection.host)
    expect(tabs()).toHaveLength(2)
    await user.click(tab(/^Plan/))
    expect(panel().querySelector('[data-node-id]')).toBeNull()
    await user.click(tab(/^Diagnosis/))
    expect(panel().querySelector('[data-fix-row]')).toBeNull()
  })

  it('C11 a connect whose result has problems keeps the form open and lists them', async () => {
    const user = await ready()
    mocked.connect.mockResolvedValue(refusedConnection)
    await openForm(user)
    await fillAndConnect(user)
    await waitFor(() => expect(within(panel()).getByRole('alert')).toHaveTextContent(refusedConnection.problems[0]))
    for (const problem of refusedConnection.problems) expect(document.body).toHaveTextContent(problem)
    expect(connectionForm()).not.toBeNull()
    expect(screen.queryByRole('textbox', { name: 'SQL query' })).toBeNull()
    expect(mocked.getSchema).toHaveBeenCalledTimes(2)
  })

  it('C11 a rejected connect shows the error in the form and keeps the previous connection', async () => {
    const user = await ready()
    mocked.connect.mockRejectedValue(new Error('password authentication failed for user "orders_ro"'))
    await openForm(user)
    await fillAndConnect(user)
    expect(await within(connectionForm()!).findByRole('alert')).toHaveTextContent('password authentication failed')
    expect(document.querySelector('[data-mode]')).toHaveAttribute('data-mode', 'sandbox')
    expect(document.body).not.toHaveTextContent(otherConnection.host)
    expect(mocked.getSchema).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('sargable.connection')).toBeNull()
  })

  it('C11 Use the demo resets the connection and loads the first sample', async () => {
    mocked.getConnection.mockResolvedValue(otherConnection)
    mocked.resetConnection.mockResolvedValue(demoConnection)
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(document.querySelector('[data-mode]')).toHaveAttribute('data-mode', 'live'))
    expect(textarea().value).toBe('')
    await openForm(user)
    await user.click(within(connectionForm()!).getByRole('button', { name: /^Use the demo/ }))
    await waitFor(() => expect(connectionForm()).toBeNull())
    expect(mocked.resetConnection).toHaveBeenCalledTimes(1)
    expect(mocked.connect).not.toHaveBeenCalled()
    expect(mocked.getSchema).toHaveBeenCalledTimes(2)
    expect(textarea().value).toBe(samples[0].sql)
    expect(document.querySelector('[data-mode]')).toHaveAttribute('data-mode', 'sandbox')
    expect(screen.getByRole('combobox', { name: 'Example queries' })).toBeInTheDocument()
  })

  it('C12 the password is sent exactly as typed and stored nowhere; only the five plain fields are remembered', async () => {
    const user = await ready()
    mocked.connect.mockResolvedValue(otherConnection)
    await openForm(user)
    await fillAndConnect(user)
    await waitFor(() => expect(connectionForm()).toBeNull())
    expect(mocked.connect).toHaveBeenCalledTimes(1)
    expect(mocked.connect).toHaveBeenCalledWith({ ...TYPED, password: CANARY, sandbox: true, ca_cert: CA_TEXT })
    expect(JSON.parse(localStorage.getItem('sargable.connection')!)).toEqual(TYPED)
    const secret = CANARY.trim()
    const stored = [localStorage, sessionStorage].flatMap((s) =>
      Object.keys(s).flatMap((k) => [k, s.getItem(k) ?? '']))
    for (const place of [...stored, document.cookie, location.href, document.body.innerHTML]) {
      expect(place).not.toContain(secret)
      expect(place).not.toContain('Cn4ry')
    }
    for (const place of [...stored, document.cookie, location.href]) expect(place).not.toContain('MIIBcaFixtureCanary')

    cleanup()
    render(<App />)
    await waitFor(() => expect(textarea().value).toBe(samples[0].sql))
    await openForm(userEvent.setup())
    expect([formField('Host').value, formField('Port').value, formField('Database').value, formField('User').value])
      .toEqual([TYPED.host, String(TYPED.port), TYPED.database, TYPED.user])
    expect(within(connectionForm()!).getByRole<HTMLSelectElement>('combobox', { name: 'SSL mode' }).value).toBe(TYPED.sslmode)
    expect(formField('Password').value).toBe('')
    expect(within(connectionForm()!).getByRole<HTMLInputElement>('checkbox', { name: /^Sandbox/ }).checked).toBe(false)
  })

  it('C11 Change connection is disabled while a run or a rewrite test is in flight', async () => {
    const change = () => screen.getByRole('button', { name: /^Change connection/ })
    const ex = deferred<api.Explain>()
    const opt = deferred<api.Optimize>()
    mocked.explain.mockReturnValue(ex.promise)
    mocked.optimize.mockReturnValue(opt.promise)
    const user = await ready()
    expect(change()).toBeEnabled()
    await user.click(run())
    expect(change()).toBeDisabled()
    ex.resolve(explainResult)
    await waitFor(() => expect(mocked.optimize).toHaveBeenCalled())
    expect(change()).toBeDisabled()
    opt.resolve(optimizeResult)
    await waitFor(() => expectSelected(/^Diagnosis/))
    expect(change()).toBeEnabled()

    const pending = deferred<api.Explain>()
    mocked.explain.mockReturnValue(pending.promise)
    await user.click(fixTab(testableIndex))
    await user.click(within(panel()).getByRole('button', { name: /^Test/ }))
    expect(change()).toBeDisabled()
    pending.resolve(testedExplain(explainResult.plan.rows))
    await waitFor(() => expect(run()).toBeEnabled())
    expect(change()).toBeEnabled()
  })
})
