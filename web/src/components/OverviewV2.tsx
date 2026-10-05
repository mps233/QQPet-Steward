import { useRef, useState } from 'react'
import type { Data } from '../api'
import { icon } from '../lib/icons'
import type { useScene } from '../lib/useScene'
import { BgSheet } from './BgSheet'
import { Capsules } from './Capsules'
import { Deck } from './Deck'
import { FuncBar } from './FuncBar'
import { IdCard } from './IdCard'
import { TaskList } from './TaskList'
import type { PageKey } from './Overview'

/**
 * 总览页 V2：**正常文档流**布局（与绝对定位版 Overview 并存，可切换）。
 *
 * 为什么另起一版而不是改 Overview：
 *   Overview 是"照 1080×2412 官方截图用绝对坐标画出来的"——24 处 position:absolute、
 *   199 处 calc(var(--u)*N)。直接改它风险太大（整页可能全乱），所以新开 V2，
 *   稳定性验证后再替换。
 *
 * 与 V1 的关系：
 *   · **复用同一批子组件**（IdCard / Capsules / TaskList / Deck / FuncBar）——
 *     它们内部本来就是正常布局，只有根元素带 `.flt`；V2 用 CSS 在自己的作用域里
 *     取消那个绝对定位（见 styles/overview-v2.css），不碰 V1 的规则。
 *   · **覆盖同等功能**：左右圆钮、资料卡、胶囊、任务列表、功能栏、状态卡、
 *     背景切换（点按开面板 / 长按换下一张）、toast。
 *
 * 布局模型（三行 grid）：
 *   ┌ row1 auto ─ 资料卡 + 胶囊 ────────────┐
 *   ├ row2 1fr ─ 圆钮列 | 任务列表 | 圆钮列 ─┤
 *   └ row3 auto ─ 状态卡 + 功能栏 ──────────┘
 *   好处：内容多时列表自然撑开（不再被裁）；短屏自动收缩；不用高度锁。
 */
export function OverviewV2({
  data,
  busy,
  scene,
  onNav,
  onStart,
  onStop,
  onReorder,
  onToggle,
}: {
  data: Data
  busy: string
  scene: ReturnType<typeof useScene>
  onNav: (p: PageKey) => void
  onStart: () => void
  onStop: () => void
  onReorder: (order: string[]) => void
  onToggle: (k: string, on: boolean) => Promise<unknown>
}) {
  const [petOpen, setPetOpen] = useState(false)
  const [bgOpen, setBgOpen] = useState(false)
  const sch = data.scheduler

  // 左上角圆钮：点按 = 开背景选择面板、长按 500ms = 直接切下一张。
  // ⚠️ 与 V1 同样的坑：开面板必须走 **onClick 而不是 onPointerUp** ——
  // 页面是滚动容器时，iOS 触摸起手会判定"滚动还是点击"，判定为滚动就发
  // pointercancel、不发 pointerup，面板永远开不出来（真机才暴露）。
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
      window.clearTimeout(holdTimer.current)
      if (!longPressed.current) setBgOpen(true)
      longPressed.current = false
    },
    onPointerLeave: () => window.clearTimeout(holdTimer.current),
    onPointerCancel: () => window.clearTimeout(holdTimer.current),
  }

  return (
    <section className="v2-home" data-page="main">
      {/* 左右圆钮从**顶部**开始（与资料卡同一水平线）——
          V1 里它们是浮动层，跨越"资料卡/胶囊/任务卡"三块；
          所以 V2 用「两列 rail 贯穿 + 中间列（top+seat）竖排」的 grid，
          而不是把 rail 塞进任务卡那一行（那样圆钮会被推到 y174 才开始，
          功能栏也跟着低 150px）。见本文件顶部对 V1 规格的说明。 */}
      <nav className="v2-rail left" aria-label="导航">
        <button
          className="rbtn"
          id="btnScene"
          title="房间背景（点按选择 / 长按换下一张）"
          {...sceneBtnHandlers}
        >
          <img src="/qp-icons/official/off_l1_tshirt.png" alt="" />
        </button>
        <button className="rbtn" title="设置" onClick={() => onNav('set')}>
          <img src="/qp-icons/official/off_l2_gear.png" alt="" />
        </button>
        <button className="rbtn" title="日志" onClick={() => onNav('log')}>
          <img src="/qp-icons/official/off_l3_diary.png" alt="" />
        </button>
        <button className="rbtn" title="通知" onClick={() => onNav('notify')}>
          <img src="/qp-icons/official/off_l4_bell.png" alt="" />
        </button>
      </nav>

      <div className="v2-center">
        {/* 顶部信息（资料卡 + 胶囊） */}
        <div className="v2-top">
          <IdCard data={data} open={petOpen} onToggle={setPetOpen} />
          <Capsules data={data} />
        </div>
        {/* 任务卡 */}
        <div className="v2-seat">
          <TaskList data={data} onReorder={onReorder} onToggle={onToggle} />
        </div>
      </div>

      {/* 右列：3 颗圆钮 + 功能栏（V1 里功能栏就在第 3 颗圆钮下方） */}
      <div className="v2-rail right">
        <nav className="v2-rail-right-btns" aria-label="快捷入口">
          <button className="rbtn r" title="冒险" onClick={() => onNav('adv')}>
            <img src="/qp-icons/official/cap_compass.png" alt="" />
          </button>
          <button className="rbtn r" title="职业" onClick={() => onNav('plan')}>
            <img src="/qp-icons/official/off_r2_briefcase.png" alt="" />
          </button>
          <button className="rbtn r" title="实时画面" onClick={() => onNav('shot')}>
            <img src={icon('/qp-icons/ctrl/phone.svg')} alt="" />
          </button>
        </nav>
        <div className="v2-funcseat">
          <FuncBar
            alive={sch.alive}
            busy={busy}
            onStart={onStart}
            onStop={onStop}
            onShot={() => onNav('shot')}
          />
        </div>
      </div>

      {/* 底栏（弧形面板 + 居中状态卡） */}
      <div className="v2-bottom">
        <Deck data={data} />
      </div>

      {/* 切换房间背景的轻提示 */}
      <div id="sceneToast" className={scene.toast ? 'on' : ''}>
        {scene.toast}
      </div>

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
