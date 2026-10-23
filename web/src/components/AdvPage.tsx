import { useCallback, useRef, useState, type ReactNode } from 'react'
import { fetchAdventure, type AdvData } from '../api'
import { usePoll } from '../lib/usePoll'

/** 图表几何：viewBox 340×126（宽度仍是 340 —— 指针取点按它换算，不能动） */
const W = 340
const H = 126
const PAD = 10

type ChartKey = 'cum' | 'pts' | 'stats'

/** 收益图绘图区：左侧留纵轴刻度、底部留时间标签 */
const PL = 28
const PR = W - 8
const PT = 10
const PB = 18

/** 横坐标：按索引 1..tx 均匀铺开（状态轨迹用整宽） */
const sx = (i: number, tx: number): number =>
  PAD + ((Math.max(1, i) - 1) / Math.max(1, tx - 1)) * (W - 2 * PAD)
/** 收益图横坐标（与左侧刻度栏对齐） */
const mx = (i: number, tx: number): number =>
  PL + ((Math.max(1, i) - 1) / Math.max(1, tx - 1)) * (PR - PL)
/** 累计收益：线性纵轴，0 落在基线上（旧版把 0 放中线，图下半截全是空的） */
const syLinear = (v: number, lo: number, hi: number): number => {
  const span = hi - lo || 1
  return H - PB - ((v - lo) / span) * (H - PT - PB)
}
/** 单次收益：开方纵轴——64% 的场次是 0、收益又集中在 20~50，
 *  线性刻度会把所有点压成贴着零线的一条毛毛虫（旧版就是这样），开方后各档位才分得开 */
const syScatter = (v: number, lo: number, hi: number): number => {
  const plotH = H - PT - PB
  const y0 = H - PB - (lo < 0 ? plotH * 0.28 : 0)
  if (v >= 0) return y0 - Math.sqrt(Math.min(1, hi > 0 ? v / hi : 0)) * (y0 - PT)
  return y0 + Math.sqrt(Math.min(1, -v / Math.max(1, -lo))) * (H - PB - y0)
}
/** 状态类纵坐标（0~100 铺满） */
const syState = (v: number): number =>
  H - PAD - (Math.max(0, Math.min(100, v)) / 100) * (H - 2 * PAD)

/** 取「好读」的纵轴上限：1/1.2/1.5/2/2.5/3/4/5/6/8 × 10^n */
const NICE_STEPS = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8]
const niceMax = (v: number): number => {
  if (!(v > 0)) return 10
  const e = Math.pow(10, Math.floor(Math.log10(v)))
  const f = v / e
  const step = NICE_STEPS.find((n) => f <= n + 1e-9) ?? 10
  return Math.max(10, step * e)
}
const fmtTick = (v: number): string => {
  const a = Math.abs(v)
  if (a >= 1000) return `${Math.round((v / 1000) * 10) / 10}k`
  return String(Math.round(v * 10) / 10)
}
/** 散点图的刻度只取「收益档」里的整数（20/25/50/100/200…），免得出现 12.5 这种怪数 */
const SCATTER_TICKS = [10, 20, 25, 50, 100, 200, 300, 500, 1000, 2000, 5000]
const scatterTicks = (top: number): number[] => {
  const ok = SCATTER_TICKS.filter((v) => v <= top * 1.02)
  if (!ok.length) return [top]
  // 只要 3 档：50/100/200 这类好读的档位，太密会在 20~25 的密集带上挤成一团
  const picked = ok.length <= 3 ? ok : [ok[ok.length - 3]!, ok[ok.length - 2]!, ok[ok.length - 1]!]
  return picked
}
/** 相邻档位在开方刻度上可能只差几个像素（20 与 25）：按实际纵坐标间距再筛一遍 */
const spacedTicks = (ticks: number[], y: (v: number) => number, minGap = 9): number[] => {
  const kept: number[] = []
  for (let i = ticks.length - 1; i >= 0; i--) {
    const v = ticks[i]!
    const yy = y(v)
    if (!kept.length || Math.abs(y(kept[kept.length - 1]!) - yy) >= minGap) kept.push(v)
  }
  return kept.reverse()
}
/** 单调三次插值（Fritsch–Carlson）：曲线平滑但不会在累计值上「越过去再回来」 */
const monoPath = (points: [number, number][]): string => {
  if (!points.length) return ''
  const first = points[0]!
  if (points.length === 1) return `M ${first[0].toFixed(1)} ${first[1].toFixed(1)}`
  const n = points.length
  const dx: number[] = []
  const dy: number[] = []
  const slope: number[] = []
  for (let i = 0; i < n - 1; i++) {
    const a = points[i]!
    const b = points[i + 1]!
    dx.push(Math.max(1e-6, b[0] - a[0]))
    dy.push(b[1] - a[1])
    slope.push(dy[i]! / dx[i]!)
  }
  const m: number[] = new Array(n)
  m[0] = slope[0]!
  m[n - 1] = slope[n - 2]!
  for (let i = 1; i < n - 1; i++) {
    const s0 = slope[i - 1]!
    const s1 = slope[i]!
    if (s0 * s1 <= 0) m[i] = 0
    else m[i] = (3 * (dx[i - 1]! + dx[i]!)) / ((2 * dx[i]! + dx[i - 1]!) / s0 + (dx[i]! + 2 * dx[i - 1]!) / s1)
  }
  let d = `M ${first[0].toFixed(1)} ${first[1].toFixed(1)}`
  for (let i = 0; i < n - 1; i++) {
    const a = points[i]!
    const b = points[i + 1]!
    const h = dx[i]!
    d += ` C ${(a[0] + h / 3).toFixed(1)} ${(a[1] + (m[i]! * h) / 3).toFixed(1)},` +
      ` ${(b[0] - h / 3).toFixed(1)} ${(b[1] - (m[i + 1]! * h) / 3).toFixed(1)},` +
      ` ${b[0].toFixed(1)} ${b[1].toFixed(1)}`
  }
  return d
}
/** 按 x 像素列分箱，只保留每列的最后一个点（累计值单调，末点即该列真值） */
const binLast = (points: [number, number][]): [number, number][] => {
  if (points.length <= 2) return points
  const out: [number, number][] = []
  let lastX = -Infinity
  for (const p of points) {
    if (p[0] - lastX >= 1.6) {
      out.push(p)
      lastX = p[0]
    } else {
      out[out.length - 1] = p
    }
  }
  // 末点必须是真实终值；若它离上一列太近，就替换掉上一列，别留一截 0 像素宽的竖线
  const tail = points[points.length - 1]!
  if (out[out.length - 1] !== tail) {
    if (out.length > 1 && tail[0] - out[out.length - 2]![0] < 1.6) out[out.length - 1] = tail
    else out.push(tail)
  }
  return out
}
/** 首尾时间标签：同一天显示 HH:MM，跨天显示 MM-DD（recent 里是 "MM-DD HH:MM"） */
const stampPair = (a: string | undefined, b: string | undefined): [string, string] => {
  const x = a || ''
  const y = b || ''
  const same = !!x && !!y && x.slice(0, 5) === y.slice(0, 5)
  const cut = (s: string) => (same && s.length >= 11 ? s.slice(6, 11) : s.slice(0, 5))
  return [x ? cut(x) : '起', y ? cut(y) : '今']
}

const fmtNet = (v: number): string => (v > 0 ? `+${v}` : String(v))
const fmtAvg = (v: number): string => {
  const r = Math.round(v * 10) / 10
  return r > 0 ? `+${r}` : String(r)
}

const GAIN_ICON: Record<string, string> = {
  体力: '/qp-icons/official/status_energy.png',
  清洁: '/qp-icons/official/status_clean.png',
  心情: '/qp-icons/official/status_mood.png',
}
const GAIN_CLASS: Record<string, string> = { 体力: 'energy', 清洁: 'clean', 心情: 'mood' }

type GainTag = { name: string; val: string }

/** `心情+5 体力+2` → 标签；认不出的整段原样保留，不丢数据 */
const parseGains = (s?: string): GainTag[] => {
  if (!s) return []
  return s
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((t) => {
      const m = t.match(/^(心情|体力|清洁)\+(\d+)$/)
      return m ? { name: m[1]!, val: m[2]! } : { name: '', val: t }
    })
}

export function AdvPage() {
  const [date, setDate] = useState('')
  const [sel, setSel] = useState<{ chart: ChartKey; k: number } | null>(null)
  const [showAll, setShowAll] = useState(true)
  const downRef = useRef<ChartKey | null>(null)

  // 20 秒轮询；切换统计范围（date）时立刻重拉
  const { data: d, error: err } = usePoll(() => fetchAdventure(date || undefined), 20000, [date])

  /** 点/拖动图表 → 找最近的索引并把读数写进提示行 */
  const pick = useCallback(
    (chart: ChartKey, e: React.PointerEvent<SVGSVGElement>, data: AdvData) => {
      const rect = e.currentTarget.getBoundingClientRect()
      const x = (e.clientX - rect.left) * (W / Math.max(1, rect.width))
      const arr = chart === 'stats' ? (data.stats || []).map((r) => r[0]) : (data.pts || []).map((p) => p[0])
      // 状态轨迹仍用整宽坐标系；收益两张图的绘图区左侧有刻度栏，取点要按 mx() 换算
      const pos = chart === 'stats' ? sx : mx
      let best: number | null = null
      let bd = Infinity
      for (const i of arr) {
        const dd = Math.abs(pos(i, data.n || 100) - x)
        if (dd < bd) {
          bd = dd
          best = i
        }
      }
      if (best != null) setSel({ chart, k: best })
    },
    [],
  )

  const bind = (chart: ChartKey, data: AdvData) => ({
    onPointerDown: (e: React.PointerEvent<SVGSVGElement>) => {
      downRef.current = chart
      pick(chart, e, data)
    },
    onPointerMove: (e: React.PointerEvent<SVGSVGElement>) => {
      if (downRef.current === chart) pick(chart, e, data)
    },
    onPointerUp: () => {
      downRef.current = null
    },
    onPointerCancel: () => {
      downRef.current = null
    },
    onPointerLeave: () => {
      downRef.current = null
    },
  })

  if (err) {
    return (
      <div className="adv-state">
        <img src="/qp-icons/official/cap_compass.png" alt="" />
        <p>读取冒险记录失败</p>
        <span>{err}</span>
      </div>
    )
  }
  if (!d) {
    return (
      <div className="adv-state">
        <img src="/qp-icons/official/cap_compass.png" alt="" />
        <p>正在翻开冒险手账…</p>
      </div>
    )
  }
  if (!d.ok) {
    return (
      <div className="adv-state">
        <img src="/qp-icons/official/cap_compass.png" alt="" />
        <p>还没有冒险记录</p>
        <span>出门冒险一次，第一条足迹就会记在这里</span>
      </div>
    )
  }

  const tx = d.n || 100
  const cum = d.cum || []
  const pts = d.pts || []
  const stats = d.stats || []
  const recent = d.recent || []
  const hasStats = stats.length > 0
  const gains = d.gains || []

  // 记录索引 → 时间/增益（提示行用）
  const tm: Record<number, string> = {}
  const gn: Record<number, string> = {}
  for (const r of recent) {
    tm[r[0]] = r[1] || ''
    gn[r[0]] = r[3] || ''
  }
  const cumMap: Record<number, number> = {}
  for (const p of cum) cumMap[p[0]] = p[1]
  const dlMap: Record<number, number> = {}
  for (const p of pts) dlMap[p[0]] = p[1]

  // ---- 提示行：点图表后的读数 ----
  let tip: ReactNode = null
  if (sel) {
    const k = sel.k
    if (sel.chart === 'stats') {
      const r = stats.filter((x) => x[0] === k).sort((a, b) => b[4] - a[4])[0]
      if (r) {
        tip = (
          <span className="adv-read">
            <b className="adv-read-no">#{k}</b>
            {tm[k] ? <span className="adv-read-time">{tm[k]}</span> : null}
            <span className="adv-read-kv energy">
              体力 <b>{r[1] ?? '—'}</b>
            </span>
            <span className="adv-read-kv clean">
              清洁 <b>{r[2] ?? '—'}</b>
            </span>
            <span className="adv-read-kv mood">
              心情 <b>{r[3] ?? '—'}</b>
            </span>
            {r[4] === 1 ? <span className="adv-read-care">护理后</span> : null}
          </span>
        )
      }
    } else {
      const v = dlMap[k]
      const tags = parseGains(gn[k])
      tip = (
        <span className="adv-read">
          <b className="adv-read-no">#{k}</b>
          {tm[k] ? <span className="adv-read-time">{tm[k]}</span> : null}
          <span className="adv-read-kv">
            累计 <b>{fmtNet(cumMap[k] ?? 0)}</b>
          </span>
          <span className="adv-read-kv">
            本趟 <b className={v != null && v > 0 ? 'pos' : v != null && v < 0 ? 'neg' : ''}>{v == null ? '?' : fmtNet(v)}</b>
          </span>
          {tags.length ? (
            <span className="adv-read-tags">
              {tags.map((t, i) => (
                <span className={`adv-tag ${GAIN_CLASS[t.name] || ''}`} key={`${t.name}-${i}`}>
                  {t.name ? <i /> : null}
                  {t.name}
                  {t.name ? <b>+{t.val}</b> : t.val}
                </span>
              ))}
            </span>
          ) : null}
        </span>
      )
    }
  }

  // ---- 明细列表 ----
  let rows = [...recent].reverse().slice(0, 400)
  if (!showAll) rows = rows.filter((r) => r[2] !== 0 || r[4])

  const meta =
    d.date === 'all'
      ? `共 ${d.n} 把 · 今日 ${d.today_n || 0} 把（${fmtNet(d.today_net || 0)}） · 更新 ${d.updated || ''}`
      : d.date === d.today
        ? `共 ${d.n} 把 · 更新 ${d.updated || ''}`
        : `${(d.date || '').slice(5).replace('-', '月')}日 · 共 ${d.n} 把 · 更新 ${d.updated || ''}`

  const winPct = d.n > 0 ? Math.round((d.win / d.n) * 100) : 0

  /** 横轴两端标签：单日看闭区间时间（08:30 → 21:23）；
   *  「全部」时 recent 只装了最后 800 条、覆盖不到整段，退回用日期区间（10-18 → 10-22） */
  const xAxis: [string, string] = (() => {
    const fullView = recent.length >= d.n
    if (fullView && tm[1]) return stampPair(tm[1], tm[tx])
    if (d.date === 'all') {
      const ds = d.dates || []
      const l = ds.length ? ds[ds.length - 1]!.slice(5) : '起'
      const rgt = ds.length ? ds[0]!.slice(5) : '今'
      return [l, rgt]
    }
    return [(d.date || '').slice(5), d.updated || '今']
  })()

  return (
    <div className="adv-page">
      <header className="adv-hero">
        <div className="adv-brand">
          <img className="adv-logo" src="/qp-icons/official/cap_compass.png" alt="" />
          <div className="adv-brand-main">
            <h2 className="adv-title">冒险手账</h2>
            <p className="adv-meta" id="advMeta">
              {meta}
            </p>
          </div>
          <label className="adv-date" id="advDateRow">
            <span className="adv-date-k">统计范围</span>
            <select
              value={date || d.date}
              aria-label="统计范围"
              onChange={(e) => {
                setSel(null)
                setDate(e.target.value)
              }}
            >
              {(d.dates || []).map((dt) => (
                <option key={dt} value={dt}>
                  {dt === d.today ? '今天' : dt === d.yesterday ? '昨天' : dt.slice(5).replace('-', '/')}
                </option>
              ))}
              <option value="all">全部</option>
            </select>
          </label>
        </div>

        <div className="adv-sum">
          <div className="adv-net-row">
            <img className="adv-coin" src="/qp-icons/official/cap_coin.png" alt="" />
            <span className={`adv-net ${d.net > 0 ? 'pos' : d.net < 0 ? 'neg' : 'zero'}`} id="advNet">
              {fmtNet(d.net)}
            </span>
            <span className="adv-unit">金币</span>
            <span className="adv-avg">
              平均 <b>{fmtAvg(d.avg)}</b>/把
            </span>
          </div>
          <div className="adv-net-hint" id="advNetHint">
            金币收益合计（结算页口径）
          </div>

          <div className="adv-ratio">
            <div className="adv-ratio-bar">
              <i style={{ width: `${winPct}%` }} />
            </div>
            <div className="adv-ratio-legend" id="advSub">
              <span className="win">
                <i />
                有收益 {d.win} 把
              </span>
              <span className="zero">
                <i />
                零收益 {d.zero} 把
              </span>
              <span className="rate">命中 {winPct}%</span>
            </div>
          </div>

          {gains.length ? (
            <div className="adv-gains" id="advChips">
              {gains.map((g) => {
                const cls = GAIN_CLASS[g[0]] || ''
                const ico = GAIN_ICON[g[0]]
                return (
                  <span className={`adv-gain ${cls}`} key={g[0]}>
                    {ico ? <img src={ico} alt="" /> : null}
                    <span>{g[0]}</span>
                    <b>+{g[2]}</b>
                    <em>×{g[1]}</em>
                  </span>
                )
              })}
            </div>
          ) : (
            <div className="adv-gains is-empty" id="advChips">
              本段没有属性增益
            </div>
          )}
        </div>
      </header>

      {/* 收益走势：累计曲线 + 单次散点，读数条常驻在卡顶 */}
      <div className="adv-sec">
        <div className="adv-sec-h">
          <h3 className="adv-sec-t">收益走势</h3>
          <span className="adv-sec-note">金币 · 结算页口径</span>
        </div>
        <div className="adv-card adv-charts">
          <div className={'adv-tip' + (sel ? ' is-live' : '')} id="advTip" aria-live="polite">
            {tip ?? (
              <span className="adv-tip-hint">
                <i className="adv-tip-touch" aria-hidden="true" />
                点按或拖动图表，查看当次读数
              </span>
            )}
          </div>

          <div className="adv-chart-block">
            <div className="adv-chart-cap">
              <span className="adv-chart-k">
                <i className="adv-swatch cum" aria-hidden="true" />
                累计收益
              </span>
              <b className={d.net > 0 ? 'pos' : ''}>{fmtNet(d.net)}</b>
            </div>
            <svg className="adv-chart" viewBox={`0 0 ${W} ${H}`} aria-label="累计收益曲线" {...bind('cum', d)}>
              <defs>
                <linearGradient id="advCumFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#F2BE4E" stopOpacity=".34" />
                  <stop offset="62%" stopColor="#F2BE4E" stopOpacity=".1" />
                  <stop offset="100%" stopColor="#F2BE4E" stopOpacity="0" />
                </linearGradient>
                <linearGradient id="advCumLine" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#F0BE52" />
                  <stop offset="100%" stopColor="#D88606" />
                </linearGradient>
              </defs>
              {(() => {
                const ys = cum.map((p) => p[1])
                const top = niceMax(Math.max(10, ...ys))
                const lo = Math.min(0, ...ys) < 0 ? -niceMax(-Math.min(0, ...ys)) : 0
                const y0 = syLinear(0, lo, top)
                // 497 个点挤进 300px 会走成锯齿台阶：按像素列取「该列最后一个累计值」，
                // 末端仍是真实终值，曲线顺滑且不改变读数
                const ptsC = binLast(cum.map((p) => [mx(p[0], tx), syLinear(p[1], lo, top)] as [number, number]))
                const first = ptsC[0]
                const last = ptsC[ptsC.length - 1]
                const [t0, t1] = xAxis
                return (
                  <>
                    {spacedTicks(lo < 0 ? [lo, 0, top / 2, top] : [top / 2, top], (v) => syLinear(v, lo, top)).map((v) => (
                      <g key={v}>
                        <line className="adv-grid" x1={PL} x2={PR} y1={syLinear(v, lo, top)} y2={syLinear(v, lo, top)} />
                        <text className="adv-axis" x={PL - 6} y={syLinear(v, lo, top) + 3} textAnchor="end">
                          {fmtTick(v)}
                        </text>
                      </g>
                    ))}
                    <line className="adv-base" x1={PL} x2={PR} y1={y0} y2={y0} />
                    {lo >= 0 ? (
                      <text className="adv-axis" x={PL - 6} y={y0 + 3} textAnchor="end">
                        0
                      </text>
                    ) : null}
                    {first && last && ptsC.length > 1 ? (
                      <path
                        d={`${monoPath(ptsC)} L ${last[0].toFixed(1)} ${y0.toFixed(1)} L ${first[0].toFixed(1)} ${y0.toFixed(1)} Z`}
                        fill="url(#advCumFill)"
                      />
                    ) : null}
                    {ptsC.length > 1 ? (
                      <path
                        d={monoPath(ptsC)}
                        fill="none"
                        stroke="url(#advCumLine)"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    ) : last ? (
                      /* 只有一条记录时画一条水平线，否则整个图只剩一个孤零零的点 */
                      <line
                        x1={PL}
                        x2={PR}
                        y1={last[1]}
                        y2={last[1]}
                        stroke="url(#advCumLine)"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                      />
                    ) : null}
                    {last ? (
                      <>
                        <circle cx={last[0]} cy={last[1]} r="7" fill="#E7A21C" opacity=".14" />
                        <circle cx={last[0]} cy={last[1]} r="2.9" fill="#E7A21C" stroke="#FFF9EC" strokeWidth="1.6" />
                      </>
                    ) : null}
                    {sel?.chart === 'cum' && sel.k in cumMap ? (
                      <>
                        <line className="adv-cross" x1={mx(sel.k, tx)} x2={mx(sel.k, tx)} y1={PT - 2} y2={y0 + 2} />
                        <circle
                          className="adv-focus"
                          cx={mx(sel.k, tx)}
                          cy={syLinear(cumMap[sel.k] ?? 0, lo, top)}
                          r="4.2"
                        />
                      </>
                    ) : null}
                    <text className="adv-axis" x={PL} y={H - 4} textAnchor="start">
                      {t0}
                    </text>
                    {tx > 1 ? (
                      <text className="adv-axis" x={PR} y={H - 4} textAnchor="end">
                        {t1}
                      </text>
                    ) : null}
                  </>
                )
              })()}
            </svg>
          </div>

          <div className="adv-chart-block">
            <div className="adv-chart-cap">
              <span className="adv-chart-k">
                <i className="adv-swatch pts" aria-hidden="true" />
                单次收益
              </span>
              <b>平均 {fmtAvg(d.avg)}</b>
            </div>
            <svg className="adv-chart" viewBox={`0 0 ${W} ${H}`} aria-label="单次收益散点" {...bind('pts', d)}>
              {(() => {
                const ys = pts.map((p) => p[1])
                const top = niceMax(Math.max(10, ...ys))
                const lo = Math.min(0, ...ys) < 0 ? -niceMax(-Math.min(0, ...ys)) : 0
                const y0 = syScatter(0, lo, top)
                const avgY = syScatter(d.avg, lo, top)
                const zeros = pts.filter((p) => p[1] === 0)
                const wins = pts.filter((p) => p[1] !== 0)
                const [t0, t1] = xAxis
                return (
                  <>
                    {/* 开方刻度：只标收益档里的整数档位（20/50/100/200…），线性刻度会挤在底部 */}
                    {spacedTicks(lo < 0 ? [lo, 0, ...scatterTicks(top)] : scatterTicks(top), (v) => syScatter(v, lo, top)).map((v) => (
                      <g key={v}>
                        <line className="adv-grid" x1={PL} x2={PR} y1={syScatter(v, lo, top)} y2={syScatter(v, lo, top)} />
                        <text className="adv-axis" x={PL - 6} y={syScatter(v, lo, top) + 3} textAnchor="end">
                          {fmtTick(v)}
                        </text>
                      </g>
                    ))}
                    <line className="adv-base" x1={PL} x2={PR} y1={y0} y2={y0} />
                    {lo >= 0 ? (
                      <text className="adv-axis" x={PL - 6} y={y0 + 3} textAnchor="end">
                        0
                      </text>
                    ) : null}
                    {/* 零收益：贴着基线的一排细刻度（318 个重叠圆点只会糊成一条毛毛虫） */}
                    {zeros.map((p) => (
                      <rect
                        key={`z${p[0]}`}
                        className="adv-zero-tick"
                        x={mx(p[0], tx) - 0.5}
                        y={y0 - 0.8}
                        width="1"
                        height="1.6"
                        rx=".5"
                      />
                    ))}
                    {wins.map((p) => {
                      const x = mx(p[0], tx)
                      const y = syScatter(p[1], lo, top)
                      const cls = p[1] < 0 ? 'neg' : p[1] >= 100 ? 'big' : p[1] >= 50 ? 'mid' : 'small'
                      return (
                        <g key={p[0]} className={`adv-dot ${cls}`}>
                          {/* 只有 50+ 的「大奖」落杆：20/25 两档是常态，几百根杆子会糊成草皮 */}
                          {cls !== 'small' ? <line className="adv-stem" x1={x} x2={x} y1={y} y2={y0} /> : null}
                          {cls === 'big' ? <circle className="adv-halo" cx={x} cy={y} r="4.2" /> : null}
                          <circle cx={x} cy={y} r={cls === 'big' ? 2.8 : cls === 'mid' ? 2.1 : 1.6} />
                        </g>
                      )
                    })}
                    <line className="adv-avg" x1={PL} x2={PR} y1={avgY} y2={avgY} />
                    <g className="adv-avg-chip" transform={`translate(${(PR - 27).toFixed(1)}, ${(avgY - 6.5).toFixed(1)})`}>
                      <rect width="27" height="9.5" rx="4.75" />
                      <text x="13.5" y="6.8" textAnchor="middle">
                        均 {fmtAvg(d.avg)}
                      </text>
                    </g>
                    {sel?.chart === 'pts' && sel.k in dlMap ? (
                      <>
                        <line className="adv-cross" x1={mx(sel.k, tx)} x2={mx(sel.k, tx)} y1={PT - 2} y2={y0 + 2} />
                        <circle
                          className="adv-focus pts"
                          cx={mx(sel.k, tx)}
                          cy={syScatter(dlMap[sel.k] ?? 0, lo, top)}
                          r="4.2"
                        />
                      </>
                    ) : null}
                    <text className="adv-axis" x={PL} y={H - 4} textAnchor="start">
                      {t0}
                    </text>
                    {tx > 1 ? (
                      <text className="adv-axis" x={PR} y={H - 4} textAnchor="end">
                        {t1}
                      </text>
                    ) : null}
                  </>
                )
              })()}
            </svg>
          </div>
        </div>
      </div>

      {/* 状态轨迹：后端目前不下发 stats，有数据才出 */}
      {hasStats ? (
        <div className="adv-sec">
          <div className="adv-sec-h">
            <h3 className="adv-sec-t" id="capStats">
              状态轨迹
            </h3>
            <span className="adv-sec-note">
              红虚线 = 阈值 <span id="capThr">{d.care_energy == null ? '--' : String(d.care_energy)}</span>
              {d.care_clean != null && d.care_clean !== d.care_energy ? `/${d.care_clean}` : ''}
            </span>
          </div>
          <div className="adv-card adv-stats">
            <div className="adv-legend">
              <span>
                <img src="/qp-icons/official/status_energy.png" alt="" />
                体力
              </span>
              <span>
                <img src="/qp-icons/official/status_clean.png" alt="" />
                清洁
              </span>
              <span>
                <img src="/qp-icons/official/status_mood.png" alt="" />
                心情
              </span>
            </div>
            <svg className="adv-chart" viewBox={`0 0 ${W} ${H}`} aria-label="状态轨迹" {...bind('stats', d)}>
              {[25, 50, 75].map((v) => (
                <line key={v} className="adv-grid" x1={PAD} x2={W - PAD} y1={syState(v)} y2={syState(v)} />
              ))}
              <line
                x1={PAD}
                y1={syState(d.care_energy ?? 60).toFixed(1)}
                x2={W - PAD}
                y2={syState(d.care_energy ?? 60).toFixed(1)}
                stroke="#E05A4A"
                strokeWidth="1"
                strokeDasharray="5 4"
                opacity=".65"
              />
              {(
                [
                  [1, '#07AAFC'],
                  [2, '#66C904'],
                  [3, '#E06549'],
                ] as const
              ).map(([idx, col]) => {
                const rows2 = stats.filter((r) => r[idx] != null)
                if (rows2.length < 2) return null
                const last = rows2[rows2.length - 1]!
                return (
                  <g key={idx}>
                    <polyline
                      points={rows2
                        .map((r) => `${sx(r[0], tx).toFixed(1)},${syState(r[idx] ?? 0).toFixed(1)}`)
                        .join(' ')}
                      fill="none"
                      stroke={col}
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    <circle cx={sx(last[0], tx).toFixed(1)} cy={syState(last[idx] ?? 0).toFixed(1)} r="2.6" fill={col} />
                  </g>
                )
              })}
              {stats
                .filter((r) => r[4] === 1)
                .map((r) => (
                  <line
                    key={`care-${r[0]}`}
                    x1={sx(r[0], tx).toFixed(1)}
                    y1={PAD}
                    x2={sx(r[0], tx).toFixed(1)}
                    y2={H - PAD}
                    stroke="#E05A4A"
                    strokeWidth="1"
                    strokeDasharray="2 3"
                    opacity=".5"
                  />
                ))}
            </svg>
          </div>
        </div>
      ) : null}

      {/* 冒险足迹：逐次明细 */}
      <div className="adv-sec">
        <div className="adv-sec-h">
          <h3 className="adv-sec-t">冒险足迹</h3>
          <span className="adv-sec-note">{rows.length} 条 · 新 → 旧</span>
          <div className="adv-seg" role="group" aria-label="明细筛选">
            <button type="button" className={showAll ? 'on' : undefined} aria-pressed={showAll} onClick={() => setShowAll(true)}>
              全部
            </button>
            <button
              type="button"
              className={!showAll ? 'on' : undefined}
              aria-pressed={!showAll}
              onClick={() => setShowAll(false)}
            >
              仅变化
            </button>
          </div>
        </div>
        <div className="adv-card adv-list" id="advList">
          {rows.length ? (
            rows.map((r) => {
              const v = r[2]
              const cls = v != null && v > 0 ? 'pos' : v != null && v < 0 ? 'neg' : 'zero'
              const vt = v == null ? '?' : v > 0 ? `+${v}` : String(v)
              const tags = parseGains(r[3])
              return (
                <div className="adv-row" key={`${r[0]}-${r[1]}`}>
                  <span className="adv-row-no">#{r[0]}</span>
                  <div className="adv-row-main">
                    <span className="adv-row-time">{r[1] || ''}</span>
                    {tags.length || r[4] ? (
                      <span className="adv-row-tags">
                        {tags.map((t, i) => (
                          <span className={`adv-tag ${GAIN_CLASS[t.name] || ''}`} key={`${t.name}-${i}`}>
                            {t.name ? <i /> : null}
                            {t.name}
                            {t.name ? <b>+{t.val}</b> : t.val}
                          </span>
                        ))}
                        {r[4] ? <span className="adv-tag fee">扣费{r[4]}</span> : null}
                      </span>
                    ) : null}
                  </div>
                  <span className={`adv-row-val ${cls}`}>
                    {v != null && v > 0 ? <img src="/qp-icons/official/cap_coin.png" alt="" /> : null}
                    {vt}
                  </span>
                </div>
              )
            })
          ) : (
            <div className="adv-list-empty">
              <img src="/qp-icons/official/cap_compass.png" alt="" />
              这一段还没有可显示的记录
            </div>
          )}
          {recent.length > 400 ? <div className="adv-list-foot">仅显示最近 400 条</div> : null}
        </div>
      </div>
    </div>
  )
}
