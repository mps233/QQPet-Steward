/** 房间背景「随机」模式的纯逻辑：抽签 + 旧 localStorage 值归一化。
 *
 *  单独一个文件是为了能在 Node 里单测（`scene-pick.test.mjs` 用 tsc 单文件编译）：
 *  `scene.ts` 依赖 `connection.ts` 的 `import.meta.env`，在 Node 里编译不过；
 *  而这两条规则（别连续抽到同一张、旧「自动」值要迁移成随机）恰恰最值得测。 */

/** `localStorage('qpet_scene')` 里表示「随机」的值。
 *  旧版的 'auto'（跟随任务）与已移除的 'store' 都会被 `normalizeManualScene`
 *  归一化成它，所以老用户的存储不会让页面白屏或退回某个不存在的场景。 */
export const RANDOM_KEY = 'random'

/** 把存储里的值归一化成「一个合法的模式键」：随机 / 背景库里的某张。 */
export function normalizeManualScene(v: string | null, known: readonly string[]): string {
  if (v === RANDOM_KEY) return RANDOM_KEY
  if (v && known.includes(v)) return v
  // 没存过 / 旧的 'auto' / 已移除的 'store' 等等，一律落到随机
  return RANDOM_KEY
}

/** 从背景池里抽一张；传 current 时排除它，避免连续两次同一张。
 *  池子为空时返回 ''（调用方按「没有背景」处理）。 */
export function pickScene(pool: readonly string[], current?: string): string {
  const candidates = current ? pool.filter((k) => k !== current) : pool
  const from = candidates.length ? candidates : pool
  if (!from.length) return ''
  // noUncheckedIndexedAccess：下标访问的类型是 string | undefined，上面已判空
  return from[Math.floor(Math.random() * from.length)] ?? ''
}
