import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'qpet-theme-'))

try {
  execFileSync(process.execPath, [
    join(here, '../../node_modules/typescript/bin/tsc'),
    join(here, 'theme.ts'),
    '--module', 'esnext', '--target', 'es2020',
    '--moduleResolution', 'bundler', '--outDir', out,
  ], { stdio: 'pipe' })
  const { nextThemePref, resolveTheme, isThemePref, THEME_CYCLE } =
    await import(pathToFileURL(join(out, 'theme.js')).href)

  // 三态循环：跟随系统 → 浅色 → 深色 → 跟随系统
  assert.deepEqual(THEME_CYCLE, ['system', 'light', 'dark'])
  assert.equal(nextThemePref('system'), 'light')
  assert.equal(nextThemePref('light'), 'dark')
  assert.equal(nextThemePref('dark'), 'system')
  // 未知值回落 system（旧版本 localStorage / 手改键名时不至于卡死）
  assert.equal(nextThemePref('nope'), 'system')

  // 解析：手动优先，system 才看系统
  assert.equal(resolveTheme('system', true), 'dark')
  assert.equal(resolveTheme('system', false), 'light')
  assert.equal(resolveTheme('light', true), 'light')
  assert.equal(resolveTheme('light', false), 'light')
  assert.equal(resolveTheme('dark', false), 'dark')
  assert.equal(resolveTheme('dark', true), 'dark')

  assert.equal(isThemePref('system'), true)
  assert.equal(isThemePref('light'), true)
  assert.equal(isThemePref('dark'), true)
  assert.equal(isThemePref('auto'), false)
  assert.equal(isThemePref(null), false)

  console.log('theme.test.mjs OK')
} finally {
  rmSync(out, { recursive: true, force: true })
}
