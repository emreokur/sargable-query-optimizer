import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ConnectionForm from './ConnectionForm'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const form = () => screen.getByRole('form', { name: 'Database connection' })
const field = (name: string) => within(form()).getByLabelText<HTMLInputElement>(name)
const sslmode = () => within(form()).getByRole<HTMLSelectElement>('combobox', { name: 'SSL mode' })
const sandbox = () => within(form()).getByRole<HTMLInputElement>('checkbox', { name: /^Sandbox/ })
const caField = () => within(form()).queryByRole<HTMLTextAreaElement>('textbox', { name: 'CA certificate (optional)' })
const PEM = '-----BEGIN CERTIFICATE-----\n  MIIBcanaryCA0123\nline two  \n-----END CERTIFICATE-----\n'
const connectButton = () => within(form()).getByRole('button', { name: /^Connect/ })
const props = { busy: false, onSubmit: () => {}, onDemo: () => {} }

async function fill(user: ReturnType<typeof userEvent.setup>, values: Record<string, string>) {
  for (const [name, value] of Object.entries(values)) {
    await user.clear(field(name))
    if (value) await user.type(field(name), value)
  }
}

describe('ConnectionForm', () => {
  it('C10 fields and defaults; password is a non-autocompleted password input; sandbox warning shown', () => {
    render(<ConnectionForm {...props} />)
    expect(field('Host').value).toBe('')
    expect(field('Port').value).toBe('5432')
    expect(field('Database').value).toBe('')
    expect(field('User').value).toBe('')
    expect(field('Password').value).toBe('')
    expect(field('Password')).toHaveAttribute('type', 'password')
    expect(field('Password')).toHaveAttribute('autocomplete', 'off')
    expect(sslmode().value).toBe('verify-full')
    expect(caField()!.value).toBe('')
    expect([...sslmode().options].map((o) => o.value).sort()).toEqual(['disable', 'require', 'verify-full'])
    expect(sandbox().checked).toBe(false)
    const warning = form().querySelector('[data-sandbox-warning]')
    expect(warning).not.toBeNull()
    expect(warning!.textContent!.trim()).not.toBe('')
    expect(connectButton()).toBeDisabled()
    expect(within(form()).queryByRole('button', { name: 'Cancel' })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('C10 initial values override the defaults, except the password which always starts empty', () => {
    render(<ConnectionForm {...props} initial={{ host: 'db.internal', port: 6432, database: 'app', user: 'ro',
      sslmode: 'verify-full', password: 'never-prefilled' }} />)
    expect([field('Host').value, field('Port').value, field('Database').value, field('User').value])
      .toEqual(['db.internal', '6432', 'app', 'ro'])
    expect(sslmode().value).toBe('verify-full')
    expect(field('Password').value).toBe('')
    expect(document.body.innerHTML).not.toContain('never-prefilled')
    expect(connectButton()).toBeEnabled()
  })

  it('C10 Connect is disabled for blank required fields, a bad port, or while busy', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<ConnectionForm {...props} />)
    await fill(user, { Host: 'db', Database: 'app', User: 'ro' })
    expect(connectButton()).toBeEnabled()
    for (const [name, bad] of [['Host', '   '], ['Database', ' '], ['User', '  '], ['Port', '0'], ['Port', '65536'],
      ['Port', '54.5'], ['Port', '']]) {
      const good = field(name).value
      await fill(user, { [name]: bad })
      expect(connectButton(), `${name}=${bad}`).toBeDisabled()
      await fill(user, { [name]: good })
      expect(connectButton()).toBeEnabled()
    }
    await fill(user, { Port: '65535' })
    expect(connectButton()).toBeEnabled()
    rerender(<ConnectionForm {...props} busy />)
    expect(connectButton()).toBeDisabled()
  })

  it('C10 submit sends typed details once: port as a number, strings trimmed, password exactly as typed', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<ConnectionForm {...props} onSubmit={onSubmit} />)
    await fill(user, { Host: '  db.internal  ', Port: '6432', Database: ' app ', User: ' ro ', Password: '  pw with spaces  ' })
    await user.selectOptions(sslmode(), 'verify-full')
    await user.click(caField()!)
    await user.paste(PEM)
    await user.click(sandbox())
    await user.click(connectButton())
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith({ host: 'db.internal', port: 6432, database: 'app', user: 'ro',
      password: '  pw with spaces  ', sslmode: 'verify-full', sandbox: true, ca_cert: PEM })
  })

  it('C10 demo and cancel buttons, and the error alert', async () => {
    const user = userEvent.setup()
    const onDemo = vi.fn()
    const onCancel = vi.fn()
    render(<ConnectionForm {...props} onDemo={onDemo} onCancel={onCancel} error="password authentication failed" />)
    await user.click(within(form()).getByRole('button', { name: /^Use the demo/ }))
    expect(onDemo).toHaveBeenCalledTimes(1)
    await user.click(within(form()).getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('alert')).toHaveTextContent('password authentication failed')
  })

  it('C10 C12 the form never touches localStorage itself', async () => {
    const user = userEvent.setup()
    const get = vi.spyOn(Storage.prototype, 'getItem')
    const set = vi.spyOn(Storage.prototype, 'setItem')
    render(<ConnectionForm {...props} initial={{ host: 'db' }} />)
    await fill(user, { Database: 'app', User: 'ro', Password: 'secret' })
    await user.click(connectButton())
    expect(get).not.toHaveBeenCalled()
    expect(set).not.toHaveBeenCalled()
  })

  it('C10 T2 the CA certificate field exists only for verify-full, and other modes submit an empty ca_cert', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<ConnectionForm {...props} onSubmit={onSubmit} initial={{ host: 'db', database: 'app', user: 'ro' }} />)
    expect(caField()).not.toBeNull()
    for (const name of ['RDS', 'Azure', 'Cloud SQL']) expect(form()).toHaveTextContent(name)
    await user.click(caField()!)
    await user.paste(PEM)
    for (const mode of ['require', 'disable']) {
      await user.selectOptions(sslmode(), mode)
      expect(caField()).toBeNull()
      await user.click(connectButton())
      expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ sslmode: mode, ca_cert: '' }))
    }
    await user.selectOptions(sslmode(), 'verify-full')
    expect(caField()).not.toBeNull()
  })
})
