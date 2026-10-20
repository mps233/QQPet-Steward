/** 数值格式化 —— 沿用旧版同名工具的格式（与历史显示一致，别再改） */

export const pad2 = (n: number): string => String(n).padStart(2, '0')

/** 运行时长满 24 小时后显示天数，兼容 ps 的 "D-HH:MM:SS" 兜底格式。 */
export function formatUptime(uptime: string): string {
  const match = /^(?:(\d+)-)?(\d+):([0-5]\d):([0-5]\d)$/.exec(uptime)
  if (!match) return uptime
  const hours = Number(match[1] || 0) * 24 + Number(match[2])
  if (!Number.isSafeInteger(hours) || hours < 24) return uptime
  return `${Math.floor(hours / 24)} 天 ${pad2(hours % 24)}:${match[3]}:${match[4]}`
}

/** 后端运行时长 → 秒，包含 ps 的 "MM:SS" / "D-HH:MM:SS" 兜底格式。 */
export function parseUptimeSeconds(uptime: string): number | null {
  const match = /^(?:(\d+)-)?(\d+):([0-5]\d)(?::([0-5]\d))?$/.exec(uptime)
  if (!match || (match[1] && !match[4])) return null
  const hours = match[4] ? Number(match[2]) : 0
  const minutes = Number(match[4] ? match[3] : match[2])
  if (minutes > 59) return null
  const seconds = (Number(match[1] || 0) * 24 + hours) * 3600
    + minutes * 60 + Number(match[4] || match[3])
  return Number.isSafeInteger(seconds) ? seconds : null
}

/** 两次后端同步之间按实际经过的时间走表，不依赖定时器执行次数。 */
export function advanceUptime(uptime: string, elapsedMs: number): string {
  const base = parseUptimeSeconds(uptime)
  const elapsed = Math.max(0, Math.floor(elapsedMs / 1000))
  if (base === null || !Number.isFinite(elapsed) || elapsed === 0) return formatUptime(uptime)
  const seconds = base + elapsed
  if (!Number.isSafeInteger(seconds)) return formatUptime(uptime)
  return formatUptime(
    `${pad2(Math.floor(seconds / 3600))}:${pad2(Math.floor(seconds % 3600 / 60))}:${pad2(seconds % 60)}`,
  )
}

/** Unix 秒时间戳 → 剩余秒数；0 表示已到点，null 表示没有可用时间。 */
export function countdownSeconds(targetSeconds: number | null | undefined, nowMs: number): number | null {
  if (typeof targetSeconds !== 'number' || !Number.isFinite(targetSeconds)
    || targetSeconds <= 0 || !Number.isFinite(nowMs)) return null
  return Math.max(0, Math.ceil(targetSeconds - nowMs / 1000))
}

/** 秒 → "H:MM:SS" / "MM:SS" */
export function hms(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return (h ? `${h}:` : '') + pad2(m) + ':' + pad2(s % 60)
}

/** 秒 → 小时字符串，保留 1 位且去掉多余的 .0（3600 → "1"，0 → "0"） */
export const hrs = (sec: number | undefined): string =>
  ((sec || 0) / 3600).toFixed(1).replace(/\.0$/, '')

/** 大数缩写：113000 → "11.3w"，10000 → "1w"，不足一万保持原样 */
export const wan = (n: number): string =>
  n >= 10000 ? (n / 10000).toFixed(1).replace(/\.0$/, '') + 'w' : String(n)

/** 把值夹进 0~100 的百分比（进度条 / 状态环用） */
export const pct = (v: number, max: number): number =>
  max > 0 ? Math.min(100, Math.max(0, (v / max) * 100)) : 0

/** 状态值可能为 null / ''（未识别到），统一成 number | null */
export function petVal(st: Record<string, unknown>, k: string): number | null {
  const raw = st?.[k]
  if (raw === null || raw === undefined || raw === '') return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

/** HTML 转义（设置页里往 title 写文本时用） */
export const esc = (s: unknown): string =>
  String(s ?? '').replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string,
  )
