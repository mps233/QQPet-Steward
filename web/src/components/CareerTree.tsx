import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  LINES,
  TIERS,
  ascensionRows,
  careerIcon,
  cellAt,
  conditionText,
  attrIcon,
  placeholderIcon,
  resolveStates,
  type CellState,
  type Trio,
} from '../lib/career-tree'

/**
 * 职业树矩阵（照官方逐像素复刻，规格见 docs/CAREER-TREE-OFFICIAL.md）。
 *
 * 这里只负责「怎么长」——布局 + 横向滑动 + 弹窗；「是什么」全在
 * `lib/career-tree.ts`（40 格静态表 + 状态合成），数据从 `/api/plan` 来。
 *
 * 三个实现要点（踩过就会白干一天）：
 *  1. **左侧层级标签不在滚动容器里**。官方横滑时「见习/初级/…」是钉住不动的，
 *     所以那列单独绝对定位覆盖在左边，渐变遮住滚过去的图标。
 *  2. **五种格子状态不能合并**：`empty` 连六边形都不画（流浪散人初级/中级就是这样），
 *     `placeholder` 画空六边形 + 大问号，`locked` 是灰色立体图标。
 *  3. 弹窗里「条件 / 升星」两行的分段逻辑（什么时候显示 `???`）全在
 *     `conditionText` / `ascensionRows`，组件里不要再拼字符串。
 */

export interface TreeProps {
  attrs: Trio
  /** 见习解锁记录（键名可能是「法师」） */
  treeLines: Record<string, { unlocked?: boolean; name?: string }>
  /** 逐级记录（可能为空：哨兵暂时只记见习） */
  treeTiers: Record<string, Record<string, { unlocked?: boolean; name?: string }>>
  /** 已展示的线 + 职业名 */
  equipped: { line: string; name: string } | null
  gates: { points: number; coins: number; jrGrad: boolean; midGrad: boolean }
  /** 点「展示」时回调（真机上要出门才能改，这里只做本地提示） */
  onEquip?: (line: string, name: string) => void
}

/**
 * 官方尺寸标定：两个**真机实测点**之间做线性插值。
 *
 * | 设备 | 视口宽 | 列步进 col | 六边形本体 | **图标盒 node** | 行步进 row |
 * | --- | --- | --- | --- | --- | --- |
 * | 一加 9 Pro（官方原机） | 360 | 83.0 | 57.0 | 69.1 | 120.0 |
 * | iPhone（用户机，官方 App 截图） | 420 | 92.0 | 60.7 | 73.6 | 134.0 |
 *
 * ⚠️ **node 是图标盒的边长，不是六边形本体**。图标源图是 200×200 的 PNG，
 * 六边形在里头只占 **82.5%**（165/200），所以盒要开得比六边形大：
 * `盒 = 本体 / 0.825`。**这一条踩过**：一开始把 node 直接当成六边形尺寸（60.7），
 * 渲染出来六边形只有 50.1，视觉上小了一圈（用户一眼看出来「图标比官方小那么多」）。
 *
 * **为什么不是"按屏宽等比缩放"**：360→420 涨 16.7%，但 col 只涨 10.8%、
 * node 只涨 6.5%、row 只涨 11.7% —— 三个量都不一样，等比缩放套不上任何一台。
 * 也不是"纯固定 dp"（那样 420 屏上应该还是 83/57/120）。
 * 两个实测点之间线性插值能同时精确命中这两台，是目前唯一有证据支撑的模型。
 *
 * 三点提醒：
 *  1. **不要改成 `col = screen / 常数`**。看着更"优雅"，但原机 360 会算出 78.9
 *     （实测 83，差 4.1px）、420 算出 92 恰好对 —— 等于只顾一台。
 *  2. 上下夹逼（clamp）防极端屏：小屏不许小于 62/48/92（再小点不动），
 *     大屏不许大于 118/74/172（iPad/桌面上不再放大，否则一屏 4 个巨头）。
 *  3. 改这里必须**同时改 CSS 的兜底值**（`--ct-col / --ct-node / --ct-row`），
 *     JS 没跑起来时（首帧/禁用 JS）用兜底值，两者要一致。
 */
const CAL = {
  // [屏宽, col, node, row] —— node 是**图标盒**边长（不是六边形本体）
  p1: [360, 83.0, 69.1, 120.0] as const,
  p2: [420, 92.0, 73.6, 134.0] as const,
}
const CLAMP = {
  col: [62, 118] as const,
  node: [58, 90] as const,
  row: [92, 172] as const,
}
const lerp = (a: number, b: number, t: number): number => a + (b - a) * t
const clamp = (v: number, [lo, hi]: readonly [number, number]): number =>
  Math.min(hi, Math.max(lo, v))

/**
 * 列头行高 / 行步进。CSS 里 `.ct-linehead` 与 `.ct-tiers` 的 padding-top 共用它。
 *
 * **官方实测 0.254，不是原来的 0.34**（2026-10-19 逐像素核对）：官方
 * 「层级标签中心 279.2 − 首行图标中心 261.8 = 17.4」，行步进 134 → 等效列头行高
 * 34.0 = 134 × 0.254；我们原来用 0.34（=45.5）把矩阵整体又推低了 11.5pt。
 */
const LINEHEAD_RATIO = 0.254

/** 由视口宽算出三把尺子（列步进 / 六边形 / 行步进），单位 CSS px。 */
export function careerMetrics(screenW: number): { col: number; node: number; row: number } {
  const [w1, c1, n1, r1] = CAL.p1
  const [w2, c2, n2, r2] = CAL.p2
  const t = (screenW - w1) / (w2 - w1)
  return {
    col: clamp(lerp(c1, c2, t), CLAMP.col),
    node: clamp(lerp(n1, n2, t), CLAMP.node),
    row: clamp(lerp(r1, r2, t), CLAMP.row),
  }
}

/**
 * 纵向放不下时按比例压行高（2026-10-19 用户实报「底部都触底了」）。
 *
 * 真机上矩阵底部顶到屏幕底边、最后一行「???」牌贴着底栏 —— 而官方留了 40+pt
 * 余量。根因是两个「顶部白扔的高度」：
 *   ① `.ct-head` 的 padding 0.297col（27.3）把整页推低，官方只留 0.06col；
 *   ② `.ct-linehead` / `.ct-tiers` 用 0.34row（45.5），官方实测 0.254row（34.0）。
 * 两条合计多占 ~34pt，5 行 × 134 的固定行高于是把底部顶穿。
 *
 * 除了修上面两条，这里再加一道**实测兜底**：不猜安全区有多高，直接量可用高度
 * 反算 `(可用高 - 列头 - 底部留白) / 5`，六边形按 node/row 的比例同步缩放
 * （两者同源，一起缩才不会出现「图标比格子大」）。
 *
 * 420×912 上是 137 > 134，**不触发压缩**，所以标准机的外观与标定值逐像素一致；
 * 只有真的放不下的设备（小屏 / 大安全区）才会压。
 */
const BOTTOM_PAD = 24 // 矩阵底部留白（官方实测最后一行牌底距屏底约 44pt，取保守值）

/** 把三把尺子写到 `.ct-page` 的 CSS 变量上，并按可用高度压行高。 */
function useCareerMetrics() {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const set = () => {
      const w = el.clientWidth
      if (!w) return
      const m = careerMetrics(w)

      // 头部高度由 CSS 算（依赖 --ct-col），所以必须等这一帧的 col 生效后再量；
      // 用 offsetHeight 读的是同一帧的布局结果，不会触发额外的强制回流。
      const headH = (el.querySelector('.ct-head') as HTMLElement | null)?.offsetHeight ?? 0
      const availH = el.clientHeight - headH
      const linehead = m.row * LINEHEAD_RATIO
      const fitRow = (availH - linehead - BOTTOM_PAD) / TIERS.length
      // **下界不再是固定 92**：320×568 这类极端小屏算出来只有 78，硬钉 92 会重新溢出
      // （实测 gap = −38）。允许压到标定值的 55%，再低点按精度就真的不够了。
      const floor = Math.min(m.row, m.row * 0.55)
      const row = Math.max(floor, Math.min(m.row, fitRow))
      // 六边形与行高按标定比例联动（原机 69.1/120 = 0.5758）
      const nodeScale = row / m.row

      el.style.setProperty('--ct-col', `${m.col.toFixed(2)}px`)
      el.style.setProperty('--ct-node', `${(m.node * nodeScale).toFixed(2)}px`)
      el.style.setProperty('--ct-row', `${row.toFixed(2)}px`)
    }
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    window.addEventListener('orientationchange', set)
    return () => {
      ro.disconnect()
      window.removeEventListener('orientationchange', set)
    }
  }, [])
  return ref
}

/** 弹窗的当前状态（null = 关着）。 */
interface Popup {
  line: number
  tier: number
}

export function CareerTree({ attrs, treeLines, treeTiers, equipped, gates }: TreeProps) {
  const [popup, setPopup] = useState<Popup | null>(null)
  const [toast, setToast] = useState('')
  const pageRef = useCareerMetrics()
  const toastTimer = useRef<number | undefined>(undefined)

  const states = useMemo(
    () => resolveStates(attrs, treeLines, equipped, treeTiers, gates),
    [attrs, treeLines, equipped, treeTiers, gates],
  )

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    window.clearTimeout(toastTimer.current)
    // 官方 toast 停留约 2 秒
    toastTimer.current = window.setTimeout(() => setToast(''), 2000)
  }, [])

  useEffect(() => () => window.clearTimeout(toastTimer.current), [])

  const openCell = (li: number, ti: number) => {
    const st = states[li]?.[ti] ?? 'empty'
    if (st === 'empty') {
      // 空格：官方点了完全没反应（反编译里是 `skip: blank item`），这里也不提示
      return
    }
    if (st === 'placeholder') {
      // 占位格 = 大师级未投放
      showToast('该职业还未现世，再观望一下')
      return
    }
    setPopup({ line: li, tier: ti })
  }

  // Esc / 返回键关弹窗
  useEffect(() => {
    if (!popup) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPopup(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [popup])

  return (
    <div className="ct-page" ref={pageRef}>
      <div className="ct-head">
        <div className="ct-head-row">
          <button className="ct-back" title="返回总览" data-back="main" onClick={() => window.history.back()}>
            <img src="/qp-icons/official/off_l1_back.png" alt="" />
          </button>
          <h1 className="ct-title">职业树</h1>
        </div>
        <div className="ct-attrs">
          {(
            [
              ['str', '力量', attrs[0]],
              ['int', '智力', attrs[1]],
              ['cha', '魅力', attrs[2]],
            ] as const
          ).map(([k, label, v]) => (
            <span className="ct-attr" key={k}>
              <img className="ct-attr-ico" src={attrIcon(k)} alt="" />
              <b>{label}</b>
              <i>{v}</i>
            </span>
          ))}
        </div>
        <p className="ct-sub">不同的属性搭配，会点亮宠物不同的职业路线</p>
      </div>

      <div className="ct-body">
        {/* 左侧固定的层级标签列（不在滚动容器里） */}
        <div className="ct-tiers" aria-hidden="true">
          {TIERS.map((t) => (
            <div className="ct-tier" key={t}>
              {t}
            </div>
          ))}
        </div>
        <div className="ct-scroll">
          <div className="ct-grid">
            {LINES.map((lineName, li) => {
              // 该列已解锁的级数 ≥2 → 整列金线（官方：只有武术家 4 级属于这种）
              const lineLit =
                (states[li] ?? []).filter((v) => v === 'unlocked' || v === 'equipped').length >= 2
              return (
              <div className="ct-line" key={lineName}>
                <div className="ct-linehead">{lineName}</div>
                {TIERS.map((tierName, ti) => {
                  const st: CellState = states[li]?.[ti] ?? 'empty'
                  const def = cellAt(li, ti)
                  // **金连线只在「该列多级解锁」时出现**（官方逐像素统计）：
                  //   武术家 4 级解锁 → 金像素 188 个，范围 286~548（真连线）
                  //   画家   1 级解锁 → 金像素 100 个，但范围 224~286 —— 那是
                  //          「已展示」的金色外发光，**不是连线**
                  //   其余 6 条单级解锁的列 → 0 个金像素
                  // 所以判据是 `lineLit`（整列 ≥2 级解锁），不是单格状态。
                  const lit = lineLit
                  return (
                    <div
                      className="ct-cell"
                      key={tierName}
                      data-lit={lit ? '1' : '0'}
                      data-empty={st === 'empty' ? '1' : '0'}
                      data-last={ti === TIERS.length - 1 ? '1' : '0'}
                    >
                      {st !== 'empty' ? (
                        <button
                          type="button"
                          className="ct-node"
                          data-ph={st === 'placeholder' ? '1' : '0'}
                          data-on={st === 'equipped' ? '1' : '0'}
                          aria-label={`${lineName} ${tierName}`}
                          onClick={() => openCell(li, ti)}
                        >
                          {st === 'placeholder' ? (
                            /* 占位格用**官方截图抠的原图**（六边形 + 描边 + 问号一体），
                               不再用 CSS 画 —— 用户指出 CSS 版的形状/描边/问号都跟官方不一样 */
                            <img src={placeholderIcon()} alt="" />
                          ) : (
                            <>
                              <img src={careerIcon(li, ti, lit)} alt="" />
                              {/* 「已现世但未解锁」：官方在彩色图上叠一个半透明暗圆 + 白锁。
                                  实测圆直径约为六边形的 58%（六边形 165px、圆 95px 左右）。 */}
                              {st === 'lockedKnown' ? <span className="ct-lock" /> : null}
                            </>
                          )}
                        </button>
                      ) : null}
                      {st !== 'empty' ? (
                        <div
                          className="ct-plate"
                          data-on={st === 'equipped' ? '1' : '0'}
                          data-lock={st === 'locked' ? '1' : '0'}
                          style={st === 'placeholder' ? { opacity: 0.75 } : undefined}
                        >
                          {st === 'placeholder' ? '???' : def.name}
                        </div>
                      ) : null}
                    </div>
                  )
                })}
              </div>
              )
            })}
          </div>
        </div>
      </div>

      {popup ? (
        <CareerPopup
          line={popup.line}
          tier={popup.tier}
          state={states[popup.line]?.[popup.tier] ?? 'locked'}
          attrs={attrs}
          gates={gates}
          onClose={() => setPopup(null)}
          onEquip={() => {
            setPopup(null)
            showToast('已对外展示该职业')
          }}
        />
      ) : null}

      {toast ? <div className="ct-toast">{toast}</div> : null}
    </div>
  )
}

/** 弹窗：官方白卡（宽 384dp、圆角 24、图标压在顶边）。 */
function CareerPopup({
  line,
  tier,
  state,
  attrs,
  gates,
  onClose,
  onEquip,
}: {
  line: number
  tier: number
  state: CellState
  attrs: Trio
  gates: TreeProps['gates']
  onClose: () => void
  onEquip: () => void
}) {
  const def = cellAt(line, tier)
  if (!def.name) return null
  const unlocked = state === 'unlocked' || state === 'equipped'
  const rows = ascensionRows(def, unlocked, gates)
  const hasAsc = rows.length > 0
  return (
    <div className="ct-mask" onClick={onClose}>
      <div className="ct-card" onClick={(e) => e.stopPropagation()}>
        <div className="ct-card-node">
          <img src={careerIcon(line, tier, unlocked)} alt="" />
        </div>
        <div className="ct-card-body">
        <h2 className="ct-card-name">{def.name}</h2>
        <div className="ct-stars">
          {Array.from({ length: 5 }, (_, i) => (
            <span key={i} className={i < def.stars ? 'on' : ''}>
              ★
            </span>
          ))}
        </div>

        <div className="ct-rows">
          <div className="ct-row">
            <span className="k">简介</span>
            <span className="v">{def.desc}</span>
          </div>
          <div className="ct-row">
            <span className="k">条件</span>
            <span className="v">{conditionText(def, attrs)}</span>
          </div>
          {hasAsc ? (
            <div className="ct-row">
              <span className="k">升星</span>
              <span className="v">
                <span className="ct-asc">
                  {rows.map((r, i) => (
                    <span className={`ln${r.done ? '' : ' dim'}`} key={i}>
                      {r.label ? <span>{r.label} </span> : null}
                      {r.coin ? <span className="ct-coin" /> : null}
                      <span>{r.value}</span>
                    </span>
                  ))}
                </span>
              </span>
            </div>
          ) : null}
        </div>

        {def.reward.items.length ? (
          <div className="ct-reward">
            <div className="ct-reward-title">{def.reward.title}</div>
            <div className="ct-items">
              {def.reward.items.map((it) => (
                <div className="ct-item" key={it.name}>
                  <div className="ct-item-ico">{REWARD_EMOJI[it.kind]}</div>
                  <div className="ct-item-name">
                    {it.name}
                    {it.qty ? ` x${it.qty}` : ''}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        </div>
        <button
          type="button"
          className="ct-btn"
          data-ghost={state === 'equipped' ? '1' : '0'}
          disabled={state === 'equipped'}
          onClick={() => {
            if (state === 'equipped') return
            if (unlocked) onEquip()
            else onClose()
          }}
        >
          {state === 'equipped' ? '已展示' : unlocked ? '展示' : '去打工'}
        </button>
        <button type="button" className="ct-close" title="关闭" onClick={onClose}>
          ✕
        </button>
      </div>
    </div>
  )
}

/** 奖励图标：官方是道具 3D 小图，这里用 emoji 示意（不冒充官方素材）。 */
const REWARD_EMOJI: Record<string, string> = {
  milk: '🥛',
  yogurt: '🍶',
  bg: '🖼️',
  cloth: '👗',
}
