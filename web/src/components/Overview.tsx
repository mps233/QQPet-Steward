import { useRef, useState } from 'react'
import type { Data } from '../api'
import { assetUrl } from '../lib/connection'
import { icon } from '../lib/icons'
import { THEME_ICON, THEME_LABEL, nextThemePref } from '../lib/theme'
import { showToast } from '../lib/toast'
import type { useScene } from '../lib/useScene'
import type { ThemeState } from '../lib/useTheme'
import { BgSheet } from './BgSheet'
import { Capsules } from './Capsules'
import { Deck } from './Deck'
import { FuncBar } from './FuncBar'
import { IdCard } from './IdCard'
import { TaskList } from './TaskList'

export type PageKey = 'main' | 'adv' | 'plan' | 'log' | 'notify' | 'set' | 'shot'

/** 总览页：照 QQ 宠物首页 1:1 还原的绝对定位浮动层。
 *  坐标全部来自官方运行时 dump（见 qqpet_assets/web/UI_TREE_OFFICIAL.md、
 *  UI_SPEC_MEASURED.md；数值在 styles/app.css 里逐项对齐），
 *  CSS 按 `calc(var(--u) * N)` 定位 —— 这里只负责放对 class/id。 */
export function Overview({
  data,
  busy,
  scene,
  theme,
  onNav,
  onStart,
  onStop,
  onReorder,
  onToggle,
}: {
  data: Data
  busy: string
  scene: ReturnType<typeof useScene>
  theme: ThemeState
  onNav: (p: PageKey) => void
  onStart: () => void
  onStop: () => void
  onReorder: (order: string[]) => void
  /** 点任务行勾选框切启用（写回 `<k>_enabled`）。返回 Promise 供 TaskList 回滚乐观值。 */
  onToggle: (k: string, on: boolean) => Promise<unknown>
}) {
  const [petOpen, setPetOpen] = useState(false)
  const [bgOpen, setBgOpen] = useState(false)
  const sch = data.scheduler

  // 左上角圆钮：**点按 = 开背景选择面板**、**长按 500ms = 直接切下一张**。
  // 官方那个位置是"返回"，但总览页就是根页面（点了永远早退，曾是置灰死键），
  // 所以改成换背景。
  //
  // ⚠️ 开面板走 **onClick 而不是 onPointerUp**：`.home` 本身是个滚动容器，iOS 在
  // 触摸起手时会先判定"这是滚动还是点击"，判定为滚动就发 `pointercancel`、
  // **不会发 `pointerup`** —— 那样面板永远开不出来（真机表现就是"点了没反应"，
  // 而 headless 里程序化派发 PointerEvent 一切正常，所以只有真机能暴露）。
  // click 事件不依赖这套判定，可靠得多。
  const holdTimer = useRef<number>()
  const longPressed = useRef(false)
  const sceneBtnHandlers = {
    onPointerDown: () => {
      longPressed.current = false
      holdTimer.current = window.setTimeout(() => {
        longPressed.current = true
        scene.cycle()
      }, 500)
    },
    onClick: () => {
      // 短按时必须把长按定时器撤掉 —— 否则 500ms 后它还会再切一次背景
      window.clearTimeout(holdTimer.current)
      if (!longPressed.current) setBgOpen(true)
      longPressed.current = false
    },
    onPointerLeave: () => window.clearTimeout(holdTimer.current),
    onPointerCancel: () => window.clearTimeout(holdTimer.current),
  }

  /** 主题圆钮：三态循环 + 一句轻提示。
   *  主要反馈是图标本身（半明半暗 / 太阳 / 月亮）和房间背景换夜间图；
   *  提示走全局 ToastHost，图标跟着新模式走。 */
  const toggleTheme = () => {
    const next = theme.cycle()
    showToast(`主题：${THEME_LABEL[next]}`, {
      icon: next === 'light' ? 'theme-light' : next === 'dark' ? 'theme-dark' : 'theme-system',
    })
  }

  return (
    <section className="home" data-page="main">
      {/* 左列圆钮（官方 4 个：返回/设置/消息/日记 x=20 y=36.3/94.3/154.3/214.3） */}
      <nav className="flt col-l" id="tabbar">
        <button className="rbtn" id="btnScene" data-tab="main" title="房间背景（点按选择 / 长按换下一张）" {...sceneBtnHandlers}>
          <img src={assetUrl('/qp-icons/official/off_l1_tshirt.png')} alt="" />
        </button>
        <button className="rbtn" data-tab="set" title="设置" onClick={() => onNav('set')}>
          <img src={assetUrl('/qp-icons/official/off_l2_gear.png')} alt="" />
        </button>
        <button className="rbtn" data-tab="log" title="日志" onClick={() => onNav('log')}>
          <img src={assetUrl('/qp-icons/official/off_l3_diary.png')} alt="" />
        </button>
        <button className="rbtn" data-tab="notify" title="通知" onClick={() => onNav('notify')}>
          <img src={assetUrl('/qp-icons/official/off_l4_bell.png')} alt="" />
        </button>
      </nav>

      {/* 右列圆钮（官方 3 个：装扮/会员/盲盒 x=298 y=36.3/94.3/154.3，官方手机版） */}
      <nav className="flt col-r" id="tabbar2">
        <button className="rbtn r" data-tab="adv" title="冒险" onClick={() => onNav('adv')}>
          <img src={assetUrl('/qp-icons/official/cap_compass.png')} alt="" />
        </button>
        <button className="rbtn r" data-tab="plan" title="职业树" onClick={() => onNav('plan')}>
          {/* `?v=` 见 OverviewV2 同名说明：文件名复用必须挂版本号才拿得到新图。 */}
          <img src={assetUrl('/qp-icons/official/off_r2_career.png?v=4')} alt="" />
        </button>
        {/* 第三颗：明暗切换（三态：跟随系统 → 浅色 → 深色）。实时画面仍可从
            功能栏的「画面」按钮进入，所以这里不再重复放手机图标。
            `/qp-icons/*` 走 max-age=3600，版本号统一由 lib/icons.ts 的 ICON_VER 提供。 */}
        <button
          className="rbtn r theme-rbtn"
          title={`主题：${THEME_LABEL[theme.pref]}（点击切换为${THEME_LABEL[nextThemePref(theme.pref)]}）`}
          aria-label={`主题：${THEME_LABEL[theme.pref]}，点击切换`}
          onClick={toggleTheme}
        >
          <img src={icon(THEME_ICON[theme.pref])} alt="" />
        </button>
      </nav>

      <IdCard data={data} open={petOpen} onToggle={setPetOpen} />
      <Capsules data={data} />

      {/* 中部场景层（官方是 3D 宠物；这里放任务队列） */}
      <div className="flt scene">
        <TaskList data={data} onReorder={onReorder} onToggle={onToggle} />
      </div>

      <FuncBar
        alive={sch.alive}
        busy={busy}
        onStart={onStart}
        onStop={onStop}
        onShot={() => onNav('shot')}
      />
      <Deck data={data} />

      <BgSheet
        open={bgOpen}
        manual={scene.manual}
        dark={scene.dark}
        onSelect={(k) => scene.select(k)}
        onClose={() => setBgOpen(false)}
      />
    </section>
  )
}
