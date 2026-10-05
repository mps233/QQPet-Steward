/**
 * 乐观更新的回滚逻辑（从 TaskList 抽出，便于独立验证）。
 *
 * 背景（docs/review/05-前端设计系统.md H 节）：原先只在"服务端状态与本地一致"时
 * 清除乐观覆盖 —— 保存失败时服务端永远不变，于是覆盖**永不消解**，勾选状态与真实
 * 配置长期不符，直到整页刷新才暴露。
 *
 * 这里把"何时回滚"的规则抽成纯函数，不依赖 React 与 DOM，
 * 既好测（见 web/src/lib/optimistic.test.mjs 的思路），也避免以后再退化成
 * "只清不减"。
 */

export type OptimMap = Record<string, boolean>

/** 应用一次乐观翻转（返回新对象，不改原值）。 */
export function applyOptim(prev: OptimMap, k: string, on: boolean): OptimMap {
  return { ...prev, [k]: on }
}

/** 撤销某键的乐观覆盖。 */
export function rollbackOptim(prev: OptimMap, k: string): OptimMap {
  if (!(k in prev)) return prev          // 没覆盖时保持引用不变，避免无谓重渲染
  const next = { ...prev }
  delete next[k]
  return next
}

/** 读取某键的有效值：有乐观覆盖用覆盖值，否则用服务端值。 */
export function effectiveValue(
  optim: OptimMap,
  k: string,
  serverOn: (k: string) => boolean,
): boolean {
  return k in optim ? optim[k]! : serverOn(k)
}

/**
 * 服务端数据刷新后，丢弃"已与之一致"的乐观覆盖。
 *
 * 注意：**只丢弃一致的**，不一致的保留 —— 后者代表"请求还在路上"，
 * 提前丢掉会让勾选在请求完成前弹回旧值（用户会看到跳动）。
 */
export function reconcile(
  prev: OptimMap,
  serverOn: (k: string) => boolean,
): OptimMap {
  let changed = false
  const next: OptimMap = {}
  for (const k of Object.keys(prev)) {
    if (serverOn(k) === prev[k]) changed = true
    else next[k] = prev[k]!
  }
  return changed ? next : prev
}
