// @vitest-environment node
/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const src = new URL('./', import.meta.url)
const EM_DASH = String.fromCharCode(0x2014)

describe('source files', () => {
  it('F12 contain no em dash outside test files', () => {
    const files = readdirSync(src, { recursive: true, encoding: 'utf8' })
      .filter((f) => /\.(tsx?|css|svg|html|json)$/.test(f) && !/\.test\.tsx?$/.test(f))
    expect(files).toEqual(expect.arrayContaining(['App.tsx', 'PlanTree.tsx', 'FixPanel.tsx', 'Diagnosis.tsx', 'samples.ts', 'api.ts']))
    expect(files).not.toContain('Fixes.tsx')
    const paths = [...files.map((f) => new URL(f, src)), new URL('../index.html', src)]
    expect(paths.filter((p) => readFileSync(p, 'utf8').includes(EM_DASH)).map(String)).toEqual([])
  })
})
