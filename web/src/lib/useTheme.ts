import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  applyTheme,
  nextThemePref,
  readThemePref,
  resolveTheme,
  systemPrefersDark,
  writeThemePref,
  type ResolvedTheme,
  type ThemePref,
} from './theme'

export interface ThemeState {
  pref: ThemePref
  resolved: ResolvedTheme
  dark: boolean
  /** 点一下圆钮：跟随系统 → 浅色 → 深色 → 跟随系统。
   *  返回切换后的偏好，供调用方显示一句轻提示。 */
  cycle: () => ThemePref
}

/** 主题状态（全应用唯一实例，在 App 里调用一次，向下传）。
 *
 *  为什么不各组件自己调：`useScene` 要按解析后的明暗换房间背景图，
 *  圆钮要读写同一份偏好 —— 两个实例各存一份 state 会不同步。
 *
 *  ⚠ `applyTheme` 用 `useLayoutEffect`：它必须赶在 App 里同步 theme-color 的
 *  layout effect 之前把 `data-theme` 写上去（hook 调用顺序更早 → effect 更早执行），
 *  否则读到的 `--qp-statusbar` 还是上一套主题的值。 */
export function useTheme(): ThemeState {
  const [pref, setPref] = useState<ThemePref>(readThemePref)
  const [systemDark, setSystemDark] = useState<boolean>(systemPrefersDark)

  // 跟随系统：系统切换时重算解析结果（手动 light/dark 不受影响）
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setSystemDark(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const resolved = resolveTheme(pref, systemDark)

  useLayoutEffect(() => {
    applyTheme(resolved)
  }, [resolved])

  // 用 ref 记住当前值：连点两下时 state 还没重渲染，闭包里的 pref 会是旧的，
  // 两次都会算出同一个 next（表现为"点快了像没反应"）。
  const prefRef = useRef(pref)
  prefRef.current = pref
  const cycle = useCallback((): ThemePref => {
    const next = nextThemePref(prefRef.current)
    prefRef.current = next
    setPref(next)
    writeThemePref(next)
    return next
  }, [])

  return { pref, resolved, dark: resolved === 'dark', cycle }
}
