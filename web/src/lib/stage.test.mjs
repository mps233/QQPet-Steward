import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'qpet-stage-'))

try {
  execFileSync(process.execPath, [
    join(here, '../../node_modules/typescript/bin/tsc'),
    join(here, 'stage.ts'),
    '--module', 'esnext', '--target', 'es2020',
    '--moduleResolution', 'bundler', '--outDir', out,
  ], { stdio: 'pipe' })
  const { computeUnit } = await import(pathToFileURL(join(out, 'stage.js')).href)

  for (const width of [320, 360, 390, 430, 520, 521, 639, 640, 768, 844, 1024, 1440]) {
    assert.equal(computeUnit(width), 1, `stage unit changes at ${width}px`)
  }
  for (const override of [null, '', ' ', '0', '-1', 'NaN', 'Infinity', 'invalid']) {
    assert.equal(computeUnit(1440, override), 1, `invalid override: ${override}`)
  }
  for (const override of ['0.9', '1', '1.0833', '2']) {
    assert.equal(computeUnit(390, override), Number(override))
  }
  console.log('Stage unit: 24 responsive/override checks passed')
} finally {
  rmSync(out, { recursive: true, force: true })
}
