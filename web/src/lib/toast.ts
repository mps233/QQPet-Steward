/**
 * 全局轻提示（toast）store。
 *
 * 为什么不是每个页面各放一个 <Toast/>：
 *   气泡的「当前显示什么 / 什么时候消失 / DOM 节点」原先在总览、职业树、App 里
 *   各有一份，样式也各写各的。这里把它们收敛成一个模块级 store，页面只调用
 *   `showToast()`，渲染交给 main.tsx 里唯一的 `<ToastHost/>`。
 *
 * 三个位置各留一条（后到覆盖前到）：
 *   center  总览/职业树的中部提示（换背景、切主题）
 *   career  职业树（官方 51.1% 位置，保持原来的观感）
 *   bottom  全局操作提示（调度器启停、设置保存、任务排序）
 * 这样「换背景」和「保存」这类不同位置的提示可以同时存在，又不会堆成一串。
 *
 * 计时器通过 `createToastStore` 注入：浏览器用全局 setTimeout，单测用假计时器
 * （见 toast.test.mjs）。默认 `duration = 1400ms`；传 0 = 常驻，直到被同位置
 * 的下一条覆盖或 `clearToast()`。
 */

export type ToastPlacement = 'center' | 'career' | 'bottom'
export type ToastTone = 'default' | 'error'
/** 图标语义：由 ToastHost 映射成内联 SVG（主题图标与 ctrl/theme-*.svg 同形）。 */
export type ToastIcon =
  | 'paw'
  | 'success'
  | 'error'
  | 'theme-system'
  | 'theme-light'
  | 'theme-dark'

export interface ToastOptions {
  placement?: ToastPlacement
  /** 毫秒；0 = 常驻，直到被同位置的下一条覆盖或 clearToast() */
  duration?: number
  tone?: ToastTone
  icon?: ToastIcon
}

export interface ToastItem {
  id: number
  text: string
  tone: ToastTone
  icon?: ToastIcon
  placement: ToastPlacement
}

export type ToastSnapshot = Record<ToastPlacement, ToastItem | null>

const PLACEMENTS: readonly ToastPlacement[] = ['center', 'career', 'bottom']
const EMPTY: ToastSnapshot = { center: null, career: null, bottom: null }

type TimerHandle = unknown
interface TimerDeps {
  setTimer?: (fn: () => void, ms: number) => TimerHandle
  clearTimer?: (handle: TimerHandle) => void
}

export interface ToastStore {
  show: (text: string, options?: ToastOptions) => number
  clear: (placement?: ToastPlacement) => void
  subscribe: (listener: () => void) => () => void
  getSnapshot: () => ToastSnapshot
}

/** 可注入计时器的工厂：测试里用假计时器，浏览器里用全局 setTimeout。 */
export function createToastStore(deps: TimerDeps = {}): ToastStore {
  const setTimer = deps.setTimer
    ?? ((fn: () => void, ms: number) => globalThis.setTimeout(fn, ms))
  const clearTimer = deps.clearTimer
    ?? ((handle: TimerHandle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>))

  let snapshot: ToastSnapshot = EMPTY
  let seq = 0
  const timers: Partial<Record<ToastPlacement, TimerHandle>> = {}
  const listeners = new Set<() => void>()

  const emit = () => {
    for (const fn of listeners) fn()
  }

  const stopTimer = (placement: ToastPlacement) => {
    if (timers[placement] !== undefined) {
      clearTimer(timers[placement])
      delete timers[placement]
    }
  }

  const show = (text: string, options: ToastOptions = {}): number => {
    const placement = options.placement ?? 'center'
    const duration = options.duration ?? 1400
    const item: ToastItem = {
      id: ++seq,
      text,
      tone: options.tone ?? 'default',
      icon: options.icon,
      placement,
    }
    stopTimer(placement)
    snapshot = { ...snapshot, [placement]: item }
    emit()
    if (duration > 0) {
      timers[placement] = setTimer(() => {
        delete timers[placement]
        // 只清自己：计时器触发前，同位置可能已经被新提示覆盖
        if (snapshot[placement]?.id === item.id) {
          snapshot = { ...snapshot, [placement]: null }
          emit()
        }
      }, duration)
    }
    return item.id
  }

  const clear = (placement?: ToastPlacement) => {
    if (placement) {
      stopTimer(placement)
      if (snapshot[placement]) {
        snapshot = { ...snapshot, [placement]: null }
        emit()
      }
      return
    }
    for (const p of PLACEMENTS) stopTimer(p)
    if (snapshot !== EMPTY) {
      snapshot = EMPTY
      emit()
    }
  }

  const subscribe = (listener: () => void) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }

  const getSnapshot = () => snapshot

  return { show, clear, subscribe, getSnapshot }
}

const defaultStore = createToastStore()

export const showToast = defaultStore.show
export const clearToast = defaultStore.clear
export const subscribeToast = defaultStore.subscribe
export const getToastSnapshot = defaultStore.getSnapshot
