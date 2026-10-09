/**
 * 舞台单位基准：把"官方 dp"映射到浏览器里的 CSS 像素。
 *
 * ## 为什么分母不是写死的 360
 *
 * 目标：网页里的按钮要与**同一台手机上官方 App 的按钮**看起来一样大。
 *
 * 官方规格全是 dp（`UI_TREE_OFFICIAL.md`）：圆钮 42dp、胶囊高 28dp、圆钮步进 60dp。
 * 同一台设备上，42dp 占屏宽的比例 = `42 / W_dp`（W_dp = 设备逻辑宽，单位 dp）。
 * 网页里按钮占屏宽 = `42u / V`（V = 视口宽，单位 CSS px）。
 *
 * 令两者相等：`u = V / W_dp`
 *
 * 而 Android 上（默认缩放 100%）：`devicePixelRatio = density / 160`，于是
 *
 *     W_dp = 物理宽 / (density/160) = 物理宽 / devicePixelRatio = V
 *
 * **即 `u ≈ 1 CSS px`** —— 手机浏览器里"视口宽(CSS px)"本来就约等于"逻辑宽(dp)"，
 * 所以 `1u = 1css px = 1dp`，直接用 CSS px 就是 dp。
 *
 * ## 历史（为什么原来是 360）
 *
 * 原实现写死 `--u = 视口宽 / 360`。360 恰好是**开发那台真机**
 * （一加 9 Pro：1080px / density 480 → 逻辑宽 360dp）的逻辑宽，
 * 所以在那一台设备上刚好对上；换任何其它逻辑宽的设备都会偏 ——
 * 例如 390px 视口的机器上按钮占 11.7%（官方该是 10.8%），平板/宽屏上更离谱。
 *
 * ## 现在怎么算
 *
 * - 默认 `u = 1px`，手机、平板和桌面保持同一套控件尺寸。
 * - 宽度变化由 grid 与版心限宽处理，不按整个视口放大控件：
 *   总览版心只有 430px，按视口放大反而会把中间任务列挤到 0px。
 *
 * 调试覆盖：URL 带 `?u=<数值>` 时直接用该值
 * （例如 `?u=1.0833` 可复现旧的"视口宽/360"行为，便于对比）。
 *
 * CSS 侧 `--u: var(--vu, 1px)` 保持首帧与这里一致。
 */

/** 导出供测试用。 */
export function computeUnit(_vw: number, override?: string | null): number {
  if (override) {
    const n = Number(override)
    if (Number.isFinite(n) && n > 0) return n
  }
  return 1
}

export function installStageUnit(): void {
  const setU = () => {
    const vw = document.documentElement.clientWidth || window.innerWidth || 360
    const override = new URLSearchParams(window.location.search).get('u')
    document.documentElement.style.setProperty('--vu', `${computeUnit(vw, override)}px`)
  }
  setU()
  window.addEventListener('resize', setU)
  window.addEventListener('orientationchange', setU)
}
