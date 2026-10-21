import { useCallback, useRef, useState, type ReactNode } from 'react'
import { fetchAdventure, type AdvData } from '../api'
import { usePoll } from '../lib/usePoll'

/** 图表几何：viewBox 340×104（宽度仍是 340 —— 指针取点按它换算，不能动） */
const W = 340
const H = 104
const PAD = 10
const MID = H / 2
const AMP = MID - PAD

type ChartKey = 'cum' | 'pts' | 'stats'

/** 横坐标：按索引 1..tx 均匀铺开 */
const sx = (i: number, tx: number): number =>
  PAD + ((Math.max(1, i) - 1) / Math.max(1, tx - 1)) * (W - 2 * PAD)
/** 收益类纵坐标（围绕中线上下铺开） */
const syMoney = (v: number, mx: number): number => MID - (v / mx) * AMP
/** 状态类纵坐标（0~100 铺满） */
const syState = (v: number): number =>
  H - PAD - (Math.max(0, Math.min(100, v)) / 100) * (H - 2 * PAD)

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
      let best: number | null = null
      let bd = Infinity
      for (const i of arr) {
        const dd = Math.abs(sx(i, data.n || 100) - x)
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
        <img src="/qp-icons/logo_adventure-64@2x.png" alt="" />
        <p>读取冒险记录失败</p>
        <span>{err}</span>
      </div>
    )
  }
  if (!d) {
    return (
      <div className="adv-state">
        <img src="/qp-icons/logo_adventure-64@2x.png" alt="" />
        <p>正在翻开冒险手账…</p>
      </div>
    )
  }
  if (!d.ok) {
    return (
      <div className="adv-state">
        <img src="/qp-icons/logo_adventure-64@2x.png" alt="" />
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

  return (
    <div className="adv-page">
      <header className="adv-hero">
        <div className="adv-brand">
          <img className="adv-logo" src="/qp-icons/logo_adventure-64@2x.png" alt="" />
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
            {tip ?? <span className="adv-tip-hint">点按或拖动下方图表，查看当次读数</span>}
          </div>

          <div className="adv-chart-block">
            <div className="adv-chart-cap">
              <span>累计收益</span>
              <b className={d.net > 0 ? 'pos' : ''}>{fmtNet(d.net)}</b>
            </div>
            <svg className="adv-chart" viewBox={`0 0 ${W} ${H}`} aria-label="累计收益曲线" {...bind('cum', d)}>
              <defs>
                <linearGradient id="advCumFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#F2C258" stopOpacity=".42" />
                  <stop offset="100%" stopColor="#F2C258" stopOpacity="0" />
                </linearGradient>
              </defs>
              {[0.2, 0.4, 0.6, 0.8].map((f) => (
                <line
                  key={f}
                  x1={PAD}
                  x2={W - PAD}
                  y1={H * f}
                  y2={H * f}
                  stroke="rgba(196,166,116,.18)"
                  strokeWidth="1"
                />
              ))}
              <line
                x1={PAD}
                x2={W - PAD}
                y1={MID}
                y2={MID}
                stroke="#DCC7A5"
                strokeWidth="1"
                strokeDasharray="4 5"
              />
              {(() => {
                const ys = cum.map((p) => p[1])
                const mx = Math.max(10, ...ys.map((v) => Math.abs(v))) * 1.15
                const ptsStr = cum
                  .map((p) => `${sx(p[0], tx).toFixed(1)},${syMoney(p[1], mx).toFixed(1)}`)
                  .join(' ')
                const first = cum[0]
                const last = cum[cum.length - 1]
                return (
                  <>
                    {first && last && cum.length > 1 ? (
                      <polygon
                        points={`${sx(first[0], tx).toFixed(1)},${MID} ${ptsStr} ${sx(last[0], tx).toFixed(1)},${MID}`}
                        fill="url(#advCumFill)"
                      />
                    ) : null}
                    {cum.length > 1 ? (
                      <polyline
                        points={ptsStr}
                        fill="none"
                        stroke="#E7A21C"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    ) : null}
                    {last ? (
                      <circle
                        cx={sx(last[0], tx).toFixed(1)}
                        cy={syMoney(last[1], mx).toFixed(1)}
                        r="3.2"
                        fill="#E7A21C"
                        stroke="#FFF8E8"
                        strokeWidth="1.6"
                      />
                    ) : null}
                    {sel?.chart === 'cum' && sel.k in cumMap ? (
                      <>
                        <line
                          x1={sx(sel.k, tx).toFixed(1)}
                          y1={PAD - 4}
                          x2={sx(sel.k, tx).toFixed(1)}
                          y2={H - PAD + 4}
                          stroke="#B08A45"
                          strokeWidth="1"
                          strokeDasharray="3 3"
                        />
                        <circle
                          cx={sx(sel.k, tx).toFixed(1)}
                          cy={syMoney(cumMap[sel.k] ?? 0, mx).toFixed(1)}
                          r="4"
                          fill="#E7A21C"
                          stroke="#fff"
                          strokeWidth="1.6"
                        />
                      </>
                    ) : null}
                  </>
                )
              })()}
            </svg>
          </div>

          <div className="adv-chart-block">
            <div className="adv-chart-cap">
              <span>单次收益</span>
              <b>平均 {fmtAvg(d.avg)}</b>
            </div>
            <svg className="adv-chart" viewBox={`0 0 ${W} ${H}`} aria-label="单次收益散点" {...bind('pts', d)}>
              {[0.2, 0.4, 0.6, 0.8].map((f) => (
                <line
                  key={f}
                  x1={PAD}
                  x2={W - PAD}
                  y1={H * f}
                  y2={H * f}
                  stroke="rgba(196,166,116,.18)"
                  strokeWidth="1"
                />
              ))}
              <line
                x1={PAD}
                x2={W - PAD}
                y1={MID}
                y2={MID}
                stroke="#DCC7A5"
                strokeWidth="1"
                strokeDasharray="4 5"
              />
              {(() => {
                const ys = pts.map((p) => p[1])
                const mx = Math.max(10, ...ys.map((v) => Math.abs(v))) * 1.2
                const avgY = syMoney(d.avg, mx)
                return (
                  <>
                    <line
                      x1={PAD}
                      y1={avgY.toFixed(1)}
                      x2={W - PAD}
                      y2={avgY.toFixed(1)}
                      stroke="#D9A12B"
                      strokeWidth="1.2"
                      strokeDasharray="5 4"
                    />
                    {pts.map((p) => (
                      <circle
                        key={p[0]}
                        cx={sx(p[0], tx).toFixed(1)}
                        cy={syMoney(p[1], mx).toFixed(1)}
                        r="2.4"
                        fill={p[1] > 0 ? '#07AAFC' : p[1] < 0 ? '#E05A4A' : '#D8C29A'}
                        opacity=".85"
                      />
                    ))}
                    {sel?.chart === 'pts' && sel.k in dlMap ? (
                      <>
                        <line
                          x1={sx(sel.k, tx).toFixed(1)}
                          y1={PAD - 4}
                          x2={sx(sel.k, tx).toFixed(1)}
                          y2={H - PAD + 4}
                          stroke="#B08A45"
                          strokeWidth="1"
                          strokeDasharray="3 3"
                        />
                        <circle
                          cx={sx(sel.k, tx).toFixed(1)}
                          cy={syMoney(dlMap[sel.k] ?? 0, mx).toFixed(1)}
                          r="4"
                          fill="#07AAFC"
                          stroke="#fff"
                          strokeWidth="1.6"
                        />
                      </>
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
              {[0.25, 0.5, 0.75].map((f) => (
                <line
                  key={f}
                  x1={PAD}
                  x2={W - PAD}
                  y1={H * f}
                  y2={H * f}
                  stroke="rgba(196,166,116,.18)"
                  strokeWidth="1"
                />
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
              <img src="/qp-icons/logo_adventure-64@2x.png" alt="" />
              这一段还没有可显示的记录
            </div>
          )}
          {recent.length > 400 ? <div className="adv-list-foot">仅显示最近 400 条</div> : null}
        </div>
      </div>
    </div>
  )
}
