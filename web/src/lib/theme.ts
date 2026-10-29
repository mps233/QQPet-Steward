/**
 * 主题偏好：跟随系统 / 浅色 / 深色（三态循环）。
 *
 * 背景：全站深色样式原先挂在 `@media (prefers-color-scheme: dark)` 上，只能跟系统走、
 * 没法手动切。现在把「解析后的主题」写到 `<html data-theme="light|dark">`，CSS 只认
 * 这个属性；跟随系统时由 JS 监听 `matchMedia` 后**同样写属性** —— 手动与系统两条路
 * 合一，不会出现"媒体查询和手动开关互相打架"。
 *
 * 存储：`localStorage('qpet_theme')`。`system` 直接删键（等于默认值，避免旧值残留，
 * 与 lib/scene.ts 的 `qpet_scene` 同一套做法）。
 *
 * ⚠️ index.html 的 `<head>` 里有一段首帧内联脚本，键名与解析规则必须和这里保持一致
 * （否则手动选深色 + 系统浅色时会先闪一帧浅色）。改这里记得同步那边。
 */

export type ThemePref = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_KEY = 'qpet_theme'

/** 点击轮转顺序：跟随系统 → 浅色 → 深色 → 跟随系统 */
export const THEME_CYCLE: readonly ThemePref[] = ['system', 'light', 'dark']

export const THEME_LABEL: Record<ThemePref, string> = {
  system: '跟随系统',
  light: '浅色',
  dark: '深色',
}

/** 圆钮图标：三种偏好各一张（放在 ctrl/ 下，与 play/stop/refresh 同一画风）。 */
export const THEME_ICON: Record<ThemePref, string> = {
  system: '/qp-icons/ctrl/theme-system.svg',
  light: '/qp-icons/ctrl/theme-light.svg',
  dark: '/qp-icons/ctrl/theme-dark.svg',
}

export function isThemePref(v: unknown): v is ThemePref {
  return v === 'system' || v === 'light' || v === 'dark'
}

export function nextThemePref(pref: ThemePref): ThemePref {
  const i = THEME_CYCLE.indexOf(pref)
  return THEME_CYCLE[(i + 1) % THEME_CYCLE.length] ?? 'system'
}

/** 解析当前实际该用哪套主题：手动的 light/dark 优先，只有 system 才看系统。 */
export function resolveTheme(pref: ThemePref, systemDark: boolean): ResolvedTheme {
  if (pref === 'dark') return 'dark'
  if (pref === 'light') return 'light'
  return systemDark ? 'dark' : 'light'
}

export function readThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_KEY)
    if (isThemePref(v)) return v
  } catch {
    /* 隐私模式 / 禁用存储：当默认「跟随系统」 */
  }
  return 'system'
}

export function writeThemePref(pref: ThemePref): void {
  try {
    if (pref === 'system') localStorage.removeItem(THEME_KEY)
    else localStorage.setItem(THEME_KEY, pref)
  } catch {
    /* 同上 */
  }
}

export function systemPrefersDark(): boolean {
  return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
}

/** 写到根元素；CSS 里所有深色规则都挂在 `[data-theme="dark"]` 上。 */
export function applyTheme(resolved: ResolvedTheme): void {
  document.documentElement.setAttribute('data-theme', resolved)
}
