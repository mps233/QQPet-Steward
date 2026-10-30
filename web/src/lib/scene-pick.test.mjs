import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'qpet-scene-pick-'))

try {
  execFileSync(process.execPath, [
    join(here, '../../node_modules/typescript/bin/tsc'),
    join(here, 'scene-pick.ts'),
    '--module', 'esnext', '--target', 'es2020',
    '--moduleResolution', 'bundler', '--outDir', out,
  ], { stdio: 'pipe' })
  const { RANDOM_KEY, normalizeManualScene, pickScene } = await import(
    pathToFileURL(join(out, 'scene-pick.js')).href
  )

  const pool = ['home-cabin', 'home-candy', 'main', 'feed', 'shower', 'record']

  // 抽签：只会在池子里，且 1000 次里每张都抽得到（池子小，漏一张的概率可忽略）
  const seen = new Set()
  for (let i = 0; i < 1000; i++) {
    const k = pickScene(pool)
    assert.ok(pool.includes(k), `unknown key: ${k}`)
    seen.add(k)
  }
  assert.equal(seen.size, pool.length, 'random pick should reach every key over 1000 draws')

  // 传 current 时必须排除它（连续两次同一张 = 用户看不出换了）
  for (let i = 0; i < 500; i++) {
    assert.notEqual(pickScene(pool, 'home-cabin'), 'home-cabin', 'must not repeat current')
  }

  // 单张池子不能抽空；空池子返回 '' 交给调用方当"无背景"
  assert.equal(pickScene(['main'], 'main'), 'main', 'single-item pool falls back to itself')
  assert.equal(pickScene([], 'main'), '', 'empty pool + current -> empty string')
  assert.equal(pickScene([]), '', 'empty pool -> empty string')

  // 旧值归一化：'auto'（旧的跟随任务）/ 'store'（已移除的场景）→ 随机；合法键原样保留
  assert.equal(RANDOM_KEY, 'random')
  assert.equal(normalizeManualScene(null, pool), 'random')
  assert.equal(normalizeManualScene('', pool), 'random')
  assert.equal(normalizeManualScene('auto', pool), 'random')
  assert.equal(normalizeManualScene('store', pool), 'random')
  assert.equal(normalizeManualScene('random', pool), 'random')
  for (const k of pool) assert.equal(normalizeManualScene(k, pool), k)

  console.log('Scene pick: pool coverage, no-repeat and legacy-value migration checks passed')
} finally {
  rmSync(out, { recursive: true, force: true })
}
