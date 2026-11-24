import { expect } from 'vitest'
import type { Connection, Explain, Optimize, PlanNode, Table } from './api'

const leaf = (id: number, type: string, ms: number, relation?: string): PlanNode => ({
  id, type, relation, totalMs: ms, selfMs: ms, rows: 100, planRows: 90, loops: 1, children: [],
})

export const plan: PlanNode = {
  id: 0, type: 'Limit', totalMs: 80, selfMs: 2, rows: 10, planRows: 10, loops: 1,
  children: [{
    id: 1, type: 'Sort', detail: 'v.visited_at DESC', totalMs: 78, selfMs: 8, rows: 10, planRows: 10, loops: 1,
    children: [{
      id: 2, type: 'Hash Join', detail: '(v.animal_id = a.id)', totalMs: 70, selfMs: 10, rows: 500, planRows: 400, loops: 1,
      children: [
        { ...leaf(3, 'Seq Scan', 40, 'visits'), detail: "(status = 'open'::text)", rowsRemoved: 1400000 },
        { id: 4, type: 'Hash', totalMs: 20, selfMs: 0, rows: 300, planRows: 300, loops: 1, children: [leaf(5, 'Seq Scan', 20, 'animals')] },
      ],
    }],
  }],
}

export const explainResult: Explain = { plan, bottleneckId: 3, planningMs: 0.4, executionMs: 80.3, planCost: 35210 }

// fastest timed fix is index 2: index 1 is quicker but changes the row count, index 0 is slower,
// and the estimate (index 4) and skipped (index 5) fixes must never count as fastest
export const optimizeResult: Optimize = {
  diagnosis: 'The scan on visits reads every row to keep a few open ones.',
  beforeMs: 1812.4,
  fixes: [
    {
      title: 'Index visits by status', kind: 'index', rationale: 'Lets the planner read only open visits.',
      ddl: ['CREATE INDEX ON visits (status, visited_at)'], sql: 'SELECT * FROM visits WHERE status = $1',
      afterMs: 250, plan: leaf(0, 'Index Scan', 250, 'visits'), rowsMatch: true,
    },
    {
      title: 'Rewrite as NOT EXISTS', kind: 'rewrite', rationale: 'Anti join instead of a hashed subplan.',
      ddl: ['CREATE INDEX ON treatments (visit_id)', 'CREATE INDEX ON visits (animal_id)'],
      sql: 'SELECT a.id FROM animals a WHERE NOT EXISTS (SELECT FROM visits v WHERE v.animal_id = a.id)',
      afterMs: 0.9, plan: leaf(0, 'Hash Anti Join', 0.9), rowsMatch: false,
    },
    {
      title: 'Composite index on animal and date', kind: 'index', rationale: 'Covers the join and the sort.',
      ddl: ['CREATE INDEX ON visits (animal_id, visited_at)'], sql: 'SELECT * FROM visits WHERE status = $1',
      afterMs: 40, rowsMatch: true,
      plan: { id: 0, type: 'Nested Loop', totalMs: 40, selfMs: 5, rows: 100, planRows: 90, loops: 1,
        children: [leaf(1, 'Bitmap Heap Scan', 30, 'visits'), leaf(2, 'Index Scan', 5, 'animals')] },
    },
    {
      title: 'Partial index on open visits', kind: 'index', rationale: 'Smaller index for the hot predicate.',
      ddl: ['CREATE INDEX ON vists (visited_at)'], sql: 'SELECT * FROM visits WHERE status = $1',
      error: 'relation "vists" does not exist',
    },
    {
      title: 'Hypothetical index on status', kind: 'index', rationale: 'The planner expects a cheap index scan.',
      ddl: ['CREATE INDEX ON visits (status)'], sql: 'SELECT * FROM visits WHERE status = $1',
      estimate: { beforeCost: 35210, afterCost: 12, usesIndex: true },
    },
    {
      title: 'Index on owner', kind: 'index', rationale: 'Supports the owner join.',
      ddl: ['CREATE INDEX ON animals (owner_id)'], sql: 'SELECT * FROM visits WHERE status = $1',
      skipped: 'Index fixes are not built on a live database. Install HypoPG for an estimate, or use a sandbox copy for a measurement.',
    },
    {
      title: 'Index the planner ignores', kind: 'index', rationale: 'Low selectivity column.',
      ddl: ['CREATE INDEX ON visits (reason)'], sql: 'SELECT * FROM visits WHERE status = $1',
      estimate: { beforeCost: 35210, afterCost: 35210, usesIndex: false },
    },
    {
      title: 'Rewrite with EXISTS', kind: 'rewrite', rationale: 'Stops at the first matching visit.',
      ddl: [], sql: 'SELECT a.id FROM animals a WHERE EXISTS (SELECT FROM visits v WHERE v.animal_id = a.id)',
      skipped: 'Rewrites from Claude are not run automatically on a live database; review it, then test it.',
      testable: true,
    },
  ],
}
export const testableIndex = 7

// The operator's test run of the testable rewrite: 5 ms beats every timed fix; rows decides rowsMatch.
export const testedExplain = (rows: number): Explain => ({
  plan: { ...leaf(0, 'Hash Semi Join', 5, 'animals'), rows }, bottleneckId: 0, planningMs: 0.2, executionMs: 5, planCost: 900,
})
export const fastestIndex = 2

export const EM_DASH = String.fromCharCode(0x2014)

const SPEEDUP = /(?<![\d.,])(\d[\d,]*(?:\.\d+)?)\s?[x\u00d7]/g

// Test helper: every "<number>x" in the element or any descendant, so numbers in sibling elements cannot merge.
export const speedups = (el: Element) =>
  [el, ...el.querySelectorAll('*')].flatMap((e) => [...(e.textContent ?? '').matchAll(SPEEDUP)].map((m) => m[1]))

// Test helper: asserts one speed-up within 5% of expected, formatted with one decimal below 10, integer from 10 up.
export const expectSpeedup = (el: Element, expected: number) => {
  const match = speedups(el).find((s) => Math.abs(parseFloat(s.replace(/,/g, '')) / expected - 1) <= 0.05)
  expect(match, `speed-up near ${expected}`).toBeDefined()
  expect(match).toMatch(expected < 10 ? /^\d\.\d$/ : /^[\d,]+$/)
}

// Test helper: an integer shown on its own, not as part of a longer number.
export const hasNumber = (el: Element, n: string) => new RegExp(`(?<!\\d|\\d[.,])${n}(?!\\d|[.,]\\d)`).test(el.textContent ?? '')

const connection = (c: Partial<Connection>): Connection => ({
  host: 'db-fixture-host', port: '5432', database: 'fixture_db', user: 'sargable_ro', serverVersion: '17.6',
  mode: 'live', demo: false, hypopg: true, problems: [], ...c,
})
export const demoConnection = connection({ host: 'localhost', mode: 'sandbox', demo: true, user: 'postgres' })
export const liveConnection = connection({})
export const otherConnection = connection({ host: 'db.internal.example', database: 'orders_prod', user: 'orders_ro' })
export const refusedConnection = connection({
  problems: ['The role is a superuser.', 'The role can write to at least one table.'],
})

export const tables: Table[] = [
  { name: 'fixture_table', rows: 1234, columns: [{ name: 'id', type: 'integer' }], indexes: ['fixture_table_pkey'] },
]
