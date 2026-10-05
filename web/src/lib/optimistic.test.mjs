/**
 * lib/optimistic.ts 的行为验证。
 *
 *   cd web && node src/lib/optimistic.test.mjs
 *
 * 做法：先用项目自带的 tsc 把 .ts 编译成 .mjs（**不靠正则转译** —— 那东西对多行
 * 类型标注会漏），再 import 编译产物。编译到临时目录，不污染 src。
 *
 * 守的是 05-H 的那个坑：乐观更新的覆盖**不能永不消解** ——
 * 原实现只在"服务端状态与本地一致"时清除，保存失败时服务端永远不变，
 * 于是勾选状态与真实配置长期不符，直到整页刷新才暴露。
 */
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = join(here, '..', '..')
const out = mkdtempSync(join(tmpdir(), 'optim-'))

try {
  execFileSync(
    process.execPath,
    [join(webRoot, 'node_modules/typescript/bin/tsc'),
     join(here, 'optimistic.ts'),
     '--module', 'esnext', '--target', 'es2020',
     '--moduleResolution', 'bundler', '--outDir', out],
    { stdio: 'pipe' },
  )
  const { applyOptim, rollbackOptim, effectiveValue, reconcile } =
    await import(pathToFileURL(join(out, 'optimistic.js')).href)

  let n = 0
  const t = (name, fn) => { fn(); n++; console.log('  ✓', name) }

  console.log('乐观更新：')

  t('点击 → 乐观翻转立即生效', () => {
    const server = { care: true }
    const optim = applyOptim({}, 'care', false)
    assert.equal(effectiveValue(optim, 'care', (k) => server[k]), false)
  })

  t('保存失败 → 回滚到服务端真值', () => {
    const server = { care: true }
    let optim = applyOptim({}, 'care', false)
    optim = rollbackOptim(optim, 'care')
    assert.equal(effectiveValue(optim, 'care', (k) => server[k]), true)
    assert.ok(!('care' in optim), '覆盖应已清空')
  })

  t('反向同理：服务端 false、点击开启、失败回滚 → false', () => {
    const server = { care: false }
    let optim = applyOptim({}, 'care', true)
    optim = rollbackOptim(optim, 'care')
    assert.equal(effectiveValue(optim, 'care', (k) => server[k]), false)
  })

  t('无覆盖时回滚返回同一引用（避免无谓重渲染）', () => {
    const same = {}
    assert.equal(rollbackOptim(same, 'x'), same)
  })

  t('reconcile：与服务器一致时丢弃覆盖', () => {
    assert.deepEqual(reconcile({ a: true }, () => true), {})
  })

  t('reconcile：不一致时保留（请求还在路上，别提前弹回）', () => {
    assert.deepEqual(reconcile({ a: false }, () => true), { a: false })
  })

  t('关键回归：失败后覆盖不会永挂（旧 bug 的守门）', () => {
    const server = { a: false }
    let optim = applyOptim({}, 'a', true)   // 用户点了开启
    optim = rollbackOptim(optim, 'a')       // 保存失败
    assert.ok(!('a' in optim))
    assert.equal(effectiveValue(optim, 'a', (k) => server[k]), false)
  })

  console.log(`\n✅ ${n} 项全部通过`)
} finally {
  rmSync(out, { recursive: true, force: true })
}
