export type PlanNode = {
  id: number
  type: string
  relation?: string
  index?: string
  detail?: string
  totalMs: number
  selfMs: number
  rows: number
  planRows: number
  rowsRemoved?: number
  loops: number
  children: PlanNode[]
}
export type Explain = { plan: PlanNode; bottleneckId: number; planningMs: number; executionMs: number; planCost: number }
export type Fix = {
  title: string
  kind: 'index' | 'rewrite'
  rationale: string
  ddl: string[]
  sql: string
  afterMs?: number
  plan?: PlanNode
  rowsMatch?: boolean
  error?: string
  estimate?: { beforeCost: number; afterCost: number; usesIndex: boolean }
  skipped?: string
  testable?: boolean
}
export type Optimize = { diagnosis: string; beforeMs: number; fixes: Fix[] }
export type Table = { name: string; rows: number; columns: { name: string; type: string }[]; indexes: string[] }

export type Connection = {
  host: string
  port: string
  database: string
  user: string
  serverVersion: string | null
  mode: 'sandbox' | 'live'
  demo: boolean
  hypopg: boolean
  problems: string[]
}

export type ConnectionDetails = {
  host: string
  port: number
  database: string
  user: string
  password: string
  sslmode: 'disable' | 'require' | 'verify-full'
  ca_cert: string
  sandbox: boolean
}

async function call<T>(path: string, method = 'GET', payload?: unknown): Promise<T> {
  const res = await fetch(
    `/api/${path}`,
    payload === undefined
      ? { method }
      : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) },
  )
  const body = await res.json()
  if (!res.ok) throw new Error(body.detail ?? res.statusText)
  return body
}

export const getConnection = () => call<Connection>('connection')
export const connect = (details: ConnectionDetails) => call<Connection>('connection', 'POST', details)
export const resetConnection = () => call<Connection>('connection', 'DELETE')
export const getSchema = () => call<Table[]>('schema')
export const explain = (sql: string) => call<Explain>('explain', 'POST', { sql })
export const optimize = (sql: string) => call<Optimize>('optimize', 'POST', { sql })

export const fmtMs = (ms: number) =>
  ms >= 100 ? Math.round(ms).toLocaleString('en-US') : ms >= 10 ? ms.toFixed(1) : ms.toFixed(2)
