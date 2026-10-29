import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'qpet-toast-'))

try {
  execFileSync(process.execPath, [
    join(here, '../../node_modules/typescript/bin/tsc'),
    join(here, 'toast.ts'),
    '--module', 'esnext', '--target', 'es2020',
    '--moduleResolution', 'bundler', '--outDir', out,
  ], { stdio: 'pipe' })
  const { createToastStore } = await import(pathToFileURL(join(out, 'toast.js')).href)

  // 假计时器：记录每次 set / clear，测试里手动触发
  const timers = []
  const setTimer = (fn, ms) => {
    const t = { fn, ms, cleared: false }
    timers.push(t)
    return t
  }
  const clearTimer = (t) => { if (t) t.cleared = true }

  const store = createToastStore({ setTimer, clearTimer })
  let notified = 0
  const unsubscribe = store.subscribe(() => { notified += 1 })

  // 初始为空，且快照引用稳定（useSyncExternalStore 依赖这一点）
  assert.deepEqual(store.getSnapshot(), { center: null, career: null, bottom: null })
  assert.equal(store.getSnapshot(), store.getSnapshot())

  // 基本显示 + 默认 1400ms
  const id1 = store.show('主题：浅色', { icon: 'theme-light' })
  assert.equal(notified, 1)
  assert.equal(store.getSnapshot().center?.id, id1)
  assert.equal(store.getSnapshot().center?.text, '主题：浅色')
  assert.equal(store.getSnapshot().center?.tone, 'default')
  assert.equal(store.getSnapshot().center?.icon, 'theme-light')
  assert.equal(timers.length, 1)
  assert.equal(timers[0].ms, 1400)
  assert.equal(store.getSnapshot().bottom, null)

  // 不同位置互不影响
  const id2 = store.show('已保存', { placement: 'bottom', icon: 'success', duration: 1800 })
  assert.equal(store.getSnapshot().center?.id, id1)
  assert.equal(store.getSnapshot().bottom?.id, id2)
  assert.equal(timers[1].ms, 1800)

  // 同位置后到覆盖前到：旧计时器被清，旧回调不会清掉新提示
  const oldTimer = timers[0]
  const id3 = store.show('主题：深色', { icon: 'theme-dark' })
  assert.equal(oldTimer.cleared, true)
  assert.equal(store.getSnapshot().center?.id, id3)
  oldTimer.fn()
  assert.equal(store.getSnapshot().center?.id, id3, '旧计时器不能清掉新提示')

  // 新提示的计时器到点后清空自己
  timers[timers.length - 1].fn()
  assert.equal(store.getSnapshot().center, null)
  assert.equal(store.getSnapshot().bottom?.id, id2, 'bottom 不受影响')

  // duration: 0 = 常驻
  const before = timers.length
  store.show('保存失败：后端拒绝', {
    placement: 'bottom', tone: 'error', icon: 'error', duration: 0,
  })
  assert.equal(timers.length, before, '常驻提示不应该创建计时器')
  assert.equal(store.getSnapshot().bottom?.tone, 'error')
  assert.equal(store.getSnapshot().bottom?.icon, 'error')

  // clear(placement) 只清指定位置
  store.clear('bottom')
  assert.equal(store.getSnapshot().bottom, null)
  assert.equal(store.getSnapshot().center, null)

  // clear() 清空全部；通知函数随订阅/退订正确增减
  store.show('a', { placement: 'center' })
  store.show('b', { placement: 'bottom' })
  store.clear()
  assert.deepEqual(store.getSnapshot(), { center: null, career: null, bottom: null })

  const n = notified
  unsubscribe()
  store.show('c')
  assert.equal(notified, n, '退订后不应再收到通知')

  console.log('toast.test.mjs OK')
} finally {
  rmSync(out, { recursive: true, force: true })
}
