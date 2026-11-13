import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'qpet-whats-new-'))

try {
  execFileSync(process.execPath, [
    join(here, '../../node_modules/typescript/bin/tsc'),
    join(here, 'whats-new.ts'),
    '--module', 'esnext', '--target', 'es2020',
    '--moduleResolution', 'bundler', '--outDir', out,
  ], { stdio: 'pipe' })
  const {
    BASELINE_VERSION,
    WHATS_NEW,
    compareVersions,
    createWhatsNewStore,
    defaultState,
    markSeen,
    seenKey,
    visibleEntries,
  } = await import(pathToFileURL(join(out, 'whats-new.js')).href)

  // ---- 版本比较：数字段逐段比，不是字符串比 ----
  assert.equal(compareVersions('0.8.14', '0.8.13'), 1)
  assert.equal(compareVersions('0.8.13', '0.8.14'), -1)
  assert.equal(compareVersions('0.10.0', '0.9.9'), 1, '0.10 > 0.9，不能按字典序')
  assert.equal(compareVersions('v1.0.0', '1.0.0'), 0, '忽略 v 前缀')
  assert.equal(compareVersions('0.8.15-beta.1', '0.8.15'), 0, '预发布后缀按同一版处理')
  assert.equal(compareVersions('', ''), 0)

  // ---- 注册表与默认状态 ----
  const itemsEntry = WHATS_NEW.find((e) => e.id === 'items')
  assert.ok(itemsEntry, '学习/打工用道具必须在注册表里')
  assert.equal(itemsEntry.since, '0.8.16')
  assert.equal(itemsEntry.settingsGroup, 'limits')
  assert.equal(itemsEntry.settingsSection, '道具', '必须与 settings-schema.ts 的分节标题逐字一致')
  assert.equal(BASELINE_VERSION, '0.8.13', '基线 = 有 NEW 标记之前的最后一版')
  // 0.8.14 的 daily_gift 已按纪律下线（发过两版），注册表里不该再有它
  assert.equal(WHATS_NEW.find((e) => e.id === 'daily_gift'), undefined,
    '旧条目发过一两版要删掉，否则新装用户会看到半年前的 NEW')

  const fresh = defaultState()
  assert.deepEqual(fresh, { lastSeen: '0.8.13', dismissed: [] })

  // 当前版本 = 0.8.16（这条功能首次出现的版本）→ 亮
  assert.deepEqual(visibleEntries(fresh, '0.8.16').map((e) => e.id), ['items'])
  // 还在老版本（0.8.15 / 0.8.13）、或拿不到版本号 → 不亮
  assert.deepEqual(visibleEntries(fresh, '0.8.15'), [])
  assert.deepEqual(visibleEntries(fresh, '0.8.13'), [])
  assert.deepEqual(visibleEntries(fresh, ''), [])

  // ---- 点掉之后：这一版没有 NEW 了 → lastSeen 推进、dismissed 清空 ----
  const afterDismiss = markSeen(fresh, itemsEntry, '0.8.16')
  assert.deepEqual(afterDismiss, { lastSeen: '0.8.16', dismissed: [] })
  assert.deepEqual(visibleEntries(afterDismiss, '0.8.16'), [])

  // 升级到下一版时，登记在 0.8.17 的新条目会亮；0.8.16 的不会复活
  const v17 = { id: 'demo', since: '0.8.17', title: '演示功能' }
  assert.deepEqual(
    visibleEntries(afterDismiss, '0.8.17', [itemsEntry, v17]).map((e) => e.id),
    ['demo'],
  )

  // ---- 同一版本有多条：点掉一条，另一条还亮，lastSeen 不提前推进 ----
  const two = { id: 'two', since: '0.8.16', title: '另一条' }
  const entries2 = [itemsEntry, two]
  const half = markSeen(fresh, itemsEntry, '0.8.16', entries2)
  assert.equal(half.lastSeen, '0.8.13', '还有没读的条目时不能推进 lastSeen')
  assert.deepEqual(half.dismissed, [seenKey(itemsEntry)])
  assert.deepEqual(visibleEntries(half, '0.8.16', entries2).map((e) => e.id), ['two'])
  const all = markSeen(half, two, '0.8.16', entries2)
  assert.deepEqual(all, { lastSeen: '0.8.16', dismissed: [] })

  // 单独点掉的条目在升级后仍然保持已读（不会因为没推进 lastSeen 而复活）
  const upgraded = visibleEntries(half, '0.8.17', [itemsEntry, two])
  assert.deepEqual(upgraded.map((e) => e.id), ['two'], 'items 已经被点掉，不能复活')

  // ---- store：持久化 + 快照引用稳定（useSyncExternalStore 依赖它） ----
  const mem = new Map()
  const storage = {
    read: () => mem.get('k') ?? null,
    write: (v) => { mem.set('k', v) },
  }
  const store = createWhatsNewStore(storage, WHATS_NEW)
  const s0 = store.getSnapshot()
  assert.equal(store.getSnapshot(), s0, '未变化时快照必须是同一个引用')
  assert.deepEqual(store.visible('0.8.16').map((e) => e.id), ['items'])
  store.dismiss(itemsEntry, '0.8.16')
  const s1 = store.getSnapshot()
  assert.notEqual(s1, s0, '点掉后换新快照触发重渲染')
  assert.deepEqual(JSON.parse(mem.get('k')), { lastSeen: '0.8.16', dismissed: [] })
  assert.deepEqual(store.visible('0.8.16'), [])

  // 重新按存储内容构造：已读状态要能恢复
  const again = createWhatsNewStore(storage, WHATS_NEW)
  assert.deepEqual(again.visible('0.8.16'), [], '重启后不该再亮')

  // 坏数据当没读过（最多多亮一次），不能抛异常
  const broken = createWhatsNewStore({ read: () => '{not json', write: () => {} }, WHATS_NEW)
  assert.deepEqual(broken.getSnapshot(), defaultState())
  assert.deepEqual(broken.visible('0.8.16').map((e) => e.id), ['items'])

  // 存储写失败（隐私模式）不能把交互打挂
  const thrower = createWhatsNewStore({
    read: () => null,
    write: () => { throw new Error('quota') },
  }, WHATS_NEW)
  thrower.dismiss(itemsEntry, '0.8.16')
  assert.deepEqual(thrower.visible('0.8.14'), [], '写失败也照样在本次会话内生效')

  console.log('whats-new.test.mjs: ok')
} finally {
  rmSync(out, { recursive: true, force: true })
}
