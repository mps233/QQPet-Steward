/**
 * 「新功能 NEW 标记」的注册表 + 已读状态。
 *
 * 每次发版把**用户看得见的新功能**登记到 `WHATS_NEW`（`since` = 第一次出现的版本，
 * 与 `desktop/macos/tauri.conf.json` 的 version 同一套号），设置页的一级菜单行 /
 * 二级分节标题、总览的任务列表就会自动亮出 NEW。用户点一下 = 已读，记在
 * localStorage（`qpet_whatsnew`），刷新/重开都不会再亮。
 *
 * 三个判据（纯函数，见 whats-new.test.mjs）：
 *   1. `since > 当前版本`：还没发到这个版本，不亮；
 *   2. `since <= lastSeen`：这一版已经读过了，不亮；
 *   3. 单独点掉过的条目也记进 `dismissed`（同一功能的几处标记一起消失）。
 * 当前版本的 NEW 全部点掉后，`lastSeen` 推进到当前版本并清空 dismissed，
 * localStorage 不会越攒越大。
 *
 * ⚠ 两条纪律：
 *   - **加新功能必须同时抬手写这里的 `since` 并升版本号**。同一个版本号里后加的
 *     条目，用户已经"读过这一版"后就再也不会亮。
 *   - 旧条目发过一两版就从数组里删掉：新装用户不该看到半年前的 NEW。
 *
 * ⚠ 标记位置必须与真实界面**逐字一致**：
 *   - `settingsGroup` = settings-schema.ts 的 GROUPS 键（一级菜单行）
 *   - `settingsSection` = 某个分组里分节标题（`<h3>` 文字），改标题要一起改
 *   - `task` = 任务键（总览任务列表那一行，见 api.ts 的 TASK_NAME）
 */

export interface WhatsNewEntry {
  /** 稳定 id（与版本组合成已读键，别为了文案改它） */
  id: string
  /** 第一次出现的版本，如 '0.8.14' */
  since: string
  /** 一句话说明（NEW 标记的 title / 无障碍标签用） */
  title: string
  /** 设置页一级菜单分组 id（GROUPS 的键） */
  settingsGroup?: string
  /** 设置页分组内的分节标题（与 settings-schema.ts 里的 title 逐字一致） */
  settingsSection?: string
  /** 总览任务列表的任务键（api.ts 的 TASK_NAME 键） */
  task?: string
}

/** 从这一版开始有 NEW 标记。比它更早的条目只对"从老版本升上来且没读过"的用户亮，
 *  当前这台机器第一次加载时 lastSeen 就取这个值。 */
export const BASELINE_VERSION = '0.8.13'

export const WHATS_NEW: readonly WhatsNewEntry[] = [
  {
    id: 'daily_gift',
    since: '0.8.14',
    title: '每日福利：衣橱「道具」页领免费礼包 + 看广告礼包',
    settingsGroup: 'social',
    settingsSection: '每日福利',
    task: 'daily_gift',
  },
]

export interface WhatsNewState {
  /** 已经读到的版本（默认 BASELINE_VERSION；点掉本版全部 NEW 后推进） */
  lastSeen: string
  /** 单独点掉的条目（`版本:id`） */
  dismissed: string[]
}

export const STORAGE_KEY = 'qpet_whatsnew'

export function defaultState(): WhatsNewState {
  return { lastSeen: BASELINE_VERSION, dismissed: [] }
}

/** 宽松语义化版本比较：'v0.10.0' > '0.9.9'，忽略 -beta/+build 后缀。
 *  非数字段按 0 处理（0.8.15-beta.1 == 0.8.15），够用了。 */
export function compareVersions(a: string, b: string): number {
  const pa = parseVersion(a)
  const pb = parseVersion(b)
  const n = Math.max(pa.length, pb.length)
  for (let i = 0; i < n; i += 1) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (d !== 0) return d < 0 ? -1 : 1
  }
  return 0
}

function parseVersion(value: string): number[] {
  const core = String(value || '').trim().replace(/^v/i, '').split(/[-+]/)[0]
  if (!core) return [0]
  return core.split('.').map((part) => {
    const m = /^\d+/.exec(part)
    return m ? Number(m[0]) : 0
  })
}

export function seenKey(entry: WhatsNewEntry): string {
  return `${entry.since}:${entry.id}`
}

/** 当前版本下还要亮 NEW 的条目（registry 顺序 = 显示顺序）。 */
export function visibleEntries(
  state: WhatsNewState,
  current: string,
  entries: readonly WhatsNewEntry[] = WHATS_NEW,
): WhatsNewEntry[] {
  if (!current) return []
  return entries.filter((entry) => {
    if (compareVersions(entry.since, current) > 0) return false // 还没发到
    if (compareVersions(entry.since, state.lastSeen) <= 0) return false // 已经读过
    return !state.dismissed.includes(seenKey(entry))
  })
}

/** 点掉一条之后的新状态：记已读；当前版本的 NEW 全部点掉就推进 lastSeen。 */
export function markSeen(
  state: WhatsNewState,
  entry: WhatsNewEntry,
  current: string,
  entries: readonly WhatsNewEntry[] = WHATS_NEW,
): WhatsNewState {
  const key = seenKey(entry)
  const dismissed = state.dismissed.includes(key)
    ? state.dismissed
    : [...state.dismissed, key]
  const next: WhatsNewState = { lastSeen: state.lastSeen, dismissed }
  if (
    current
    && compareVersions(current, next.lastSeen) > 0
    && visibleEntries(next, current, entries).length === 0
  ) {
    return { lastSeen: current, dismissed: [] }
  }
  return next
}

export interface WhatsNewStorage {
  read: () => string | null
  write: (value: string) => void
}

export interface WhatsNewStore {
  subscribe: (listener: () => void) => () => void
  /** 引用稳定（useSyncExternalStore 依赖这一点） */
  getSnapshot: () => WhatsNewState
  visible: (current: string) => WhatsNewEntry[]
  dismiss: (entry: WhatsNewEntry, current: string) => void
}

function parseState(raw: string | null): WhatsNewState {
  if (!raw) return defaultState()
  try {
    const obj = JSON.parse(raw) as Partial<WhatsNewState> | null
    const lastSeen = typeof obj?.lastSeen === 'string' && obj.lastSeen
      ? obj.lastSeen
      : BASELINE_VERSION
    const dismissed = Array.isArray(obj?.dismissed)
      ? obj.dismissed.filter((v): v is string => typeof v === 'string')
      : []
    return { lastSeen, dismissed }
  } catch {
    return defaultState() // 坏数据当没读过，最多多亮一次
  }
}

/** 可注入 storage 的工厂：浏览器用 localStorage，单测用假存储。 */
export function createWhatsNewStore(
  storage: WhatsNewStorage,
  entries: readonly WhatsNewEntry[] = WHATS_NEW,
): WhatsNewStore {
  let snapshot = parseState(storage.read())
  const listeners = new Set<() => void>()

  const emit = () => {
    for (const fn of listeners) fn()
  }

  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    getSnapshot: () => snapshot,
    visible: (current: string) => visibleEntries(snapshot, current, entries),
    dismiss: (entry: WhatsNewEntry, current: string) => {
      const next = markSeen(snapshot, entry, current, entries)
      if (
        next.lastSeen === snapshot.lastSeen
        && next.dismissed === snapshot.dismissed
      ) return
      snapshot = next
      try {
        storage.write(JSON.stringify(snapshot))
      } catch {
        // 隐私模式/存储被禁用：本次会话内标记照样消失，下次打开会再亮一遍
      }
      emit()
    },
  }
}

/** 浏览器默认存储。localStorage 读写在隐私模式下都可能抛异常，全部兜住。 */
const browserStorage: WhatsNewStorage = {
  read: () => {
    try {
      return globalThis.localStorage?.getItem(STORAGE_KEY) ?? null
    } catch {
      return null
    }
  },
  write: (value) => {
    try {
      globalThis.localStorage?.setItem(STORAGE_KEY, value)
    } catch {
      // 同上：存不进去不影响本次会话
    }
  },
}

export const whatsNewStore = createWhatsNewStore(browserStorage)
