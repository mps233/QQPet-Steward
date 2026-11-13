import { useSyncExternalStore } from 'react'
import {
  visibleEntries,
  whatsNewStore,
  type WhatsNewEntry,
} from './whats-new'

/** 读「新功能 NEW 标记」（注册表见 whats-new.ts）。
 *
 *  `version` 来自 `/api/data` 的 `app.version`（源码运行与手机端都一致）。
 *  store 是模块级单例（与 ToastHost 同一套 useSyncExternalStore 写法），
 *  所以在设置页点掉 NEW 时，总览任务列表上的同一个标记会同时消失。
 *
 *  版本号还没拿到（首轮轮询未回）时一律不亮，避免"先亮后灭"的闪烁。 */
export function useWhatsNew(version?: string) {
  const state = useSyncExternalStore(
    whatsNewStore.subscribe,
    whatsNewStore.getSnapshot,
    whatsNewStore.getSnapshot,
  )
  const current = version || ''
  const entries = visibleEntries(state, current)

  return {
    entries,
    /** 一级菜单分组里还没读的条目（菜单行上的 NEW） */
    inGroup: (group: string) => entries.filter((e) => e.settingsGroup === group),
    /** 分组内某个分节标题旁的条目（标题文字与 settings-schema.ts 逐字对应） */
    inSection: (group: string, section: string) => entries.filter(
      (e) => e.settingsGroup === group && e.settingsSection === section,
    ),
    /** 任务列表某一行对应的条目 */
    forTask: (task: string) => entries.find((e) => e.task === task),
    dismiss: (entry: WhatsNewEntry) => whatsNewStore.dismiss(entry, current),
  }
}
