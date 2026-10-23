import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { TASK_GROUP, TASK_NAME, isLoopTask, keyOfTaskName, runnerStart, runnerStop, saveSettings, type Data } from './api'
import { Overview, type PageKey } from './components/Overview'
import { OverviewV2 } from './components/OverviewV2'
import { AdvPage } from './components/AdvPage'
import { AccessKeyGate, ConnectionGate, useConnectionState } from './components/ConnectionGate'
import { LivePage } from './components/LivePage'
import { LogPage } from './components/LogPage'
import { NavHead } from './components/NavHead'
import { NotifyPage } from './components/NotifyPage'
import { Onboarding } from './components/Onboarding'
import { PlanPage } from './components/PlanPage'
import { SettingsPage } from './components/SettingsPage'
import { useData } from './lib/useData'
import { useScene } from './lib/useScene'
import { readConnection } from './lib/connection'

/** 前端外壳：页面切换 + 全部内页（总览 / 冒险 / 职业 / 日志 / 通知 / 设置 / 实时画面）。
 *  构建产物由 dashboard.py 的 `/` 直接服务（旧版原生界面已删除，构建见 web/build.sh）。 */
/** 一个"视图" = 页面 + 二级键（设置分组 / 日志子页）。
 *  转场按**视图粒度**做，所以"设置一级 → 二级"与"总览 → 内页"共用同一套滑动动画。 */
type View = { page: PageKey; grp: string | null; sub: string | null }
type LeavingView = View & { scrollTop: number }

/** 真正的滚动容器：**当前页的 `.page-slot`**。
 *
 *  这个前端里 `html` 是固定的 `overflow:hidden` 画布、`body` 也没有可滚高度，
 *  只有 `.page-slot` 是 `overflow-y:auto` 的那一层（见 FRONTEND_PITFALLS 第三节：
 *  `.page-slot` / `.home` / `.advlist` / `#logbox` 都是滚动容器）。
 *  探针实测（390×420 的 iframe 里打开设置页）：
 *    html 可滚 0 · body 可滚 0 · main 可滚 0 · section 可滚 0 · #root 0，
 *    只有 `.page-slot.page-enter` scrollHeight=1000 / clientHeight=420（可滚 580），
 *    对它写 `scrollTop=300` 能读回 300。
 *  所以 `document.scrollingElement`（指向 html）与 `document.body` 都读写无效 ——
 *  早先两版按它们取，表现就是"记忆了但没生效"。
 *  转场期间退场页 `.page-leave-*` 也在 DOM 里，必须锁定 `.page-enter`。 */
function scrollerEl(): HTMLElement | null {
  const enter = document.querySelector('.page-slot.page-enter')
  return enter instanceof HTMLElement ? enter : null
}

export default function App() {
  const [connectedOnce, setConnectedOnce] = useState(() => !readConnection().server)
  const [loginRequired, setLoginRequired] = useState(false)
  if (!connectedOnce) {
    return (
      <div className="app">
        <main>
          <ConnectionGate onConnected={() => setConnectedOnce(true)} />
        </main>
      </div>
    )
  }
  if (loginRequired) {
    return (
      <div className="app">
        <main>
          <AccessKeyGate onAuthenticated={() => window.location.reload()} />
        </main>
      </div>
    )
  }
  return <DashboardApp onLoginRequired={() => setLoginRequired(true)} />
}

/** Dashboard after the remote shell has paired. Keeping this in a child means
 *  the connection gate's early return cannot change the App hook order. */
function DashboardApp({ onLoginRequired }: { onLoginRequired: () => void }) {
  const { data, error, reload } = useData(6000)
  useConnectionState()
  const loginRequested = useRef(false)
  const [busy, setBusy] = useState('')
  const [guideMode, setGuideMode] = useState<'auto' | 'open' | 'closed'>('auto')
  const guideOpen = guideMode === 'open'
    || (guideMode === 'auto' && !!data?.onboarding?.required && !data.scheduler.alive)
  /**
   * 历史栈模型（与 旧版 的 showTab / popstate 一一对应）：
   *   navDepth = 0（总览）/ 1（内页）/ 2（二级：设置 grp、日志子页）
   *     往下一层   → pushState
   *     同级互切   → replaceState（栈不增长）
   *     回总览     → 一次退够（back 或 go(-navDepth)）
   *     二级→别的内页 → 先退到总览再压目标（pendingTab 收尾）
   *   **"返回"类操作绝不能 pushState** —— 按钮在压栈、手势在退栈，方向相反会退不回去。
   *   这套是侧滑返回能用的前提；迁移时漏掉导致侧滑失效，这里补回来。
   */
  const navDepth = useRef(0)
  const pendingTab = useRef<PageKey | null>(null)

  /** 从 URL 解析当前所在位置：页面 + 二级键（设置的分组 / 日志的子页）。
   *  **URL 是唯一事实来源** —— 侧滑返回时浏览器只改 URL 再抛 popstate，
   *  界面状态必须照着 URL 重建，"栈里有什么就能退到什么"才成立。 */
  const parseLoc = useCallback(() => {
    const sp = new URLSearchParams(location.search)
    const known: PageKey[] = ['main', 'adv', 'plan', 'log', 'notify', 'set', 'shot']
    const t = sp.get('tab') as PageKey | null
    return {
      page: (t && known.includes(t) ? t : 'main') as PageKey,
      grp: sp.get('grp'),
      sub: sp.get('sub'),
    }
  }, [])

  // 首帧就按 URL 落在目标视图，直开内页不会先闪过总览。
  const [page, setPage] = useState<PageKey>(() => parseLoc().page)
  // 二级（设置分组 / 日志子页）：两个都从 URL 初始化，直开 ?tab=set&grp=work 也能落在二级
  const [grp, setGrp] = useState<string | null>(() => parseLoc().grp)
  const [sub, setSub] = useState<string | null>(() => parseLoc().sub)

  /** 0 总览 / 1 内页 / 2 二级（设置分组 · 日志子页） */
  const depthOf = (loc: { page: PageKey; grp: string | null; sub: string | null }): number =>
    loc.page === 'main' ? 0 : loc.grp || loc.sub ? 2 : 1

  // ---- 各视图的滚动位置记忆 ----
  // 设置一级菜单滚到下面 → 点进二级 → 返回时停在顶部（用户实报）：真正滚动的是 `.page-slot`，
  // 二级内容短，浏览器会把 scrollTop 直接 clamp 成 0，
  // 再回一级时内容虽然变回来，滚动位置不会自动恢复。
  // 做法：**在 DOM 更新前**（各导航入口里）存下当前视图的 scrollTop，视图 key 变了再写回去。
  // key = 页面 或 页面:二级键 —— 一级与二级各记各的，不会互相覆盖。
  const scrollMemo = useRef<Record<string, number>>({})
  const scrollKeyRef = useRef<string>('main')
  const scrollKeyOf = (p: PageKey, g: string | null, s: string | null): string =>
    g || s ? `${p}:${g || s}` : p
  const saveScroll = useCallback(() => {
    const el = scrollerEl()
    if (el) scrollMemo.current[scrollKeyRef.current] = el.scrollTop
  }, [])

  const go = useCallback((name: PageKey) => {
    saveScroll()
    const target = name === 'main' ? 0 : 1
    const url = name === 'main' ? location.pathname : `?tab=${name}`
    try {
      if (target > navDepth.current) {
        navDepth.current = target
        history.pushState({ tab: name }, '', url)
      } else if (target === navDepth.current) {
        if (target === 1) history.replaceState({ tab: name }, '', url)
      } else if (target === 0) {
        const steps = navDepth.current
        navDepth.current = 0
        if (steps === 1) history.back()
        else history.go(-steps)
      } else {
        pendingTab.current = name
        navDepth.current = 0
        history.go(-2)
      }
    } catch {
      /* 某些环境禁 pushState，退化成纯状态切换 */
    }
    setGrp(null)
    setSub(null)
    setPage(name)
  }, [saveScroll])

  /** 进二级 / 二级之间互切：**往下压栈、同级只替换当前条**。
   *  离开二级一律走 `backSub()`（history.back()）——"返回"类操作绝不能用 pushState：
   *  按钮在压栈、手势在退栈，方向相反会退不回去（旧版实测踩过，见 FRONTEND_PITFALLS）。 */
  const openSub = useCallback((kind: 'grp' | 'sub', key: string) => {
    saveScroll()   // 记下一级菜单滚到哪儿了，返回时好还原
    const cur = parseLoc()
    const atSameLevel = kind === 'grp' ? !!cur.grp : !!cur.sub
    const sp = new URLSearchParams(location.search)
    sp.set('tab', cur.page === 'main' ? (kind === 'grp' ? 'set' : 'log') : cur.page)
    sp.set(kind, key)
    const url = location.pathname + '?' + sp.toString()
    try {
      if (atSameLevel) history.replaceState({ tab: sp.get('tab') }, '', url)
      else history.pushState({ tab: sp.get('tab') }, '', url)
    } catch {
      /* 忽略 */
    }
    navDepth.current = 2
    if (kind === 'grp') setGrp(key)
    else setSub(key)
  }, [parseLoc, saveScroll])

  /** 二级 → 一级（返回按钮与侧滑同一条路：退浏览器栈，不压栈） */
  const backSub = useCallback(() => {
    try {
      history.back()
    } catch {
      setGrp(null)
      setSub(null)
    }
  }, [])

  // 侧滑 / 浏览器返回
  useEffect(() => {
    const onPop = () => {
      saveScroll()   // 先把**正在离开的那个视图**的位置记下来（此刻 DOM 还没更新）
      if (pendingTab.current) {
        // 跳级退栈的收尾：落回总览后再压目标页
        const t = pendingTab.current
        pendingTab.current = null
        try {
          history.pushState({ tab: t }, '', `?tab=${t}`)
        } catch {
          /* 忽略 */
        }
        navDepth.current = 1
        setPage(t)
        return
      }
      // 一律按 URL 重建（**含二级**）：从"设置二级"侧滑回"设置一级"时 page 仍是 set，
      // 只在 page 变化时才同步的话 grp 会残留成 2 层，下一次返回就多退一层（旧版踩过）
      const loc = parseLoc()
      navDepth.current = depthOf(loc)
      setPage(loc.page)
      setGrp(loc.grp)
      setSub(loc.sub)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [parseLoc, saveScroll])

  // 直开 ?tab=xxx[&grp=yyy][&sub=zzz]（便于分享链接 / 截图 / 调试）
  useLayoutEffect(() => {
    const loc = parseLoc()
    setPage(loc.page)
    setGrp(loc.grp)
    setSub(loc.sub)
    navDepth.current = depthOf(loc)
  }, [parseLoc])

  // 视图（页面 + 二级）变化后，把记住的滚动位置写回去。
  // 新视图没记录过 → 落在顶部（进二级/进内页从顶部开始，符合预期）。
  useLayoutEffect(() => {
    const key = scrollKeyOf(page, grp, sub)
    scrollKeyRef.current = key
    const y = scrollMemo.current[key] ?? 0
    const put = () => {
      const el = scrollerEl()   // 每次现取：转场中/渲染后 .page-enter 可能是新节点
      if (el) el.scrollTop = y
    }
    put()   // 首帧前恢复，避免先显示顶部再跳到记忆位置。
    const raf = window.requestAnimationFrame(put)
    const t = window.setTimeout(put, 90)   // 兜底：iOS 上布局偶尔晚一帧才算完
    return () => {
      window.cancelAnimationFrame(raf)
      window.clearTimeout(t)
    }
  }, [page, grp, sub])

  const [orderMsg, setOrderMsg] = useState('')

  // ---- iOS 式转场：前后两个**视图**同时在场（旧视图左让 / 新视图右入，返回时反过来）----
  // leaving = 正在退场的视图及其滚动位置，动画结束就卸载；dir 决定方向。
  // **视图 = 页面 + 二级**：设置一级↔二级、日志↔子页也走同一套滑动，不再只有页面级才有动画
  // （早先这里只认 PageKey，所以"进二级"是硬切，看着像动画丢了；实际是从来没挂过）。
  const [leaving, setLeaving] = useState<LeavingView | null>(null)
  const [dir, setDir] = useState<'fwd' | 'back'>('fwd')
  const [transiting, setTransiting] = useState(false)
  const prevView = useRef<View | null>(null)
  const leaveTimer = useRef<number>()
  const leaveSlot = useRef<HTMLDivElement>(null)
  const finishTransition = useCallback(() => {
    window.clearTimeout(leaveTimer.current)
    setLeaving(null)
    setTransiting(false)
  }, [])

  /** 首帧前准备转场，不能先画目标页、下一帧才把它移到动画起点。
   *  首次渲染不触发（prevView 还是 null）。方向按**层级**判：变深 = fwd、变浅 = back；
   *  日志子页互切（sub 为空 / index / reward）不滑，直接换内容。 */
  useLayoutEffect(() => {
    const cur: View = { page, grp, sub }
    const from = prevView.current
    prevView.current = cur
    if (!from) return
    if (from.page === cur.page && (from.grp || '') === (cur.grp || '')
        && (from.sub || '') === (cur.sub || '')) {
      return   // 同一个视图（只是数据刷新）→ 不动画
    }
    window.clearTimeout(leaveTimer.current)
    const dFrom = depthOf(from)
    const dTo = depthOf(cur)
    // 同页同级只换内容：日志刚进来时 sub 是空的，点 tab 才变成 index/reward，
    // 只看 depth 会把这次点击当成"进二级"，整页（含标题栏和 tab 条）横滑一次 —— 用户实报。
    // 注意只认 sub：设置页的 grp（null → work）仍要走进二级的动画。
    const sameLevel = from.page === cur.page && (from.grp || '') === (cur.grp || '')
    if (sameLevel || dFrom === dTo || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      finishTransition()
      return
    }
    setDir(dTo > dFrom ? 'fwd' : 'back')
    setLeaving({
      ...from,
      scrollTop: scrollMemo.current[scrollKeyOf(from.page, from.grp, from.sub)] ?? 0,
    })
    setTransiting(true)
    // animationend 是主路径，这里的定时器只兜"动画被取消/浏览器没抛事件"。
    // **时长必须按实际动画算**（原来固定 600ms）：宽屏是 200ms 淡出、手机是 320ms 横滑，
    // 写死 600 的话万一 animationend 没来，离场页会多停 280~400ms —— 用户看到的就是
    // "切完页旧页还赖着"（宽屏版尤其明显，因为两侧空白区会把它露出来）。
    // 真正的时长在下面那个 leaveSlot 的 layoutEffect 里读 computed style 后重设。
    leaveTimer.current = window.setTimeout(finishTransition, 600)
    return () => window.clearTimeout(leaveTimer.current)
  }, [page, grp, sub, finishTransition])

  useLayoutEffect(() => {
    if (!leaving || !leaveSlot.current) return
    leaveSlot.current.scrollTop = leaving.scrollTop
    // 按**实际**动画时长重设兜底定时器（见上面那条注释）。
    // 动画被禁用（prefers-reduced-motion，或样式没命中）时 animationDuration 是 0s，
    // 此时下一帧就收尾 —— 绝不能让离场页在屏幕上多停。
    const el = leaveSlot.current
    const raw = window.getComputedStyle(el).animationDuration || '0s'
    // 可能是 "0.2s" / "200ms" / 逗号分隔的多个值，取最大的那个
    const ms = Math.max(
      0,
      ...raw.split(',').map((s) => {
        const t = s.trim()
        const n = parseFloat(t)
        if (!Number.isFinite(n)) return 0
        return t.endsWith('ms') ? n : n * 1000
      }),
    )
    window.clearTimeout(leaveTimer.current)
    // +80ms 余量：animationend 正常会先到，这只是迟到的保险
    leaveTimer.current = window.setTimeout(finishTransition, ms > 0 ? ms + 80 : 0)
  }, [leaving, finishTransition])

  // 自动换背景要跟着「当前任务 + 进行中的活动」走
  // 场景键统一用**任务键**：pending 时后端给的就是键（pending_key），平时
  // queue.current 是中文任务名（TaskQueueRunner 写 task.name）要反查回来 ——
  // 直接把中文名交给 SCENE_OF（键是 care/school/pk…）永远命中不了，
  // 会让"按任务自动换背景"只在 pending 期间生效。
  const curKey = data?.queue.pending
    ? data.queue.pending_key || ''
    : keyOfTaskName(data?.queue.current)
  const etaKind = data?.work_eta?.kind ?? ''
  const scene = useScene(curKey, etaKind)

  // 切页/换背景时同步两件「旧版有、迁移时漏掉」的东西：
  //
  // 1) `html[data-page]` —— CSS 里 `html[data-page]:not([data-page="main"])` 靠它把内页的
  //    底色与背景图切成白色顶栏。不设这个属性的话，进设置页后顶部的安全区**仍然显示总览页
  //    的房间暖色**（旧版是 showTab() 里 setAttribute 的，见 git 历史 web/legacy/app.js）。
  // 2) `theme-color` meta —— iOS 18 及以前读它画状态栏那条带；iOS 26+ 改为采样 html 的
  //    background-color（那条路靠 CSS 的 --qp-statusbar，本身已经对）。
  useLayoutEffect(() => {
    const root = document.documentElement
    root.setAttribute('data-page', guideOpen ? 'onboarding' : page)
    const dark = !!window.matchMedia?.('(prefers-color-scheme: dark)').matches
    let want = '#FFFFFF' // 内页顶栏是白的
    if (guideOpen) {
      want = dark ? '#27231D' : '#FCF7ED'
    } else if (page === 'main') {
      const v = getComputedStyle(root).getPropertyValue('--qp-statusbar').trim()
      want = v || (dark ? '#A9722D' : '#D5A758') // CSS 变量读不到时的兜底
    } else if (page === 'plan') {
      // **职业树是整屏深棕页面**，状态栏那条带不能跟着内页白底 ——
      // PWA 全屏时 body 的 padding-top 露出的是 html 底色，
      // 而 html[data-page] 规则把所有内页设成 #fff，职业树顶部就会白一条
      // （用户实报「手机上 PWA 顶部是白色的」）。
      // 取职业树背景渐变的**顶部色** #5D4443（见 career-tree.css 的 --ct-bg-top）。
      want = '#5D4443'
    } else if (page === 'adv') {
      // 冒险手账是米色页面（透明 navhead 叠在奶油渐变上），跟着内页白底会在
      // PWA 顶部露出白带。取页头渐变顶部实测色。
      want = '#FAF0DA'
    } else if (page === 'shot') {
      // 实时画面页头是暖白半透明（见 live.css），同样跟着内页白底会露白带。
      want = '#FFFAF1'
    }
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
      // 两组 meta 各带一个 media 查询，只改属于当前主题的那条 ——
      // 两条都改的话，切系统主题时会把另一主题的值也覆盖成当前主题的色（串色）
      const isDark = (m.getAttribute('media') || '').includes('dark')
      if (isDark === dark) m.setAttribute('content', want)
    })
  }, [page, scene.scene, scene.dark, guideOpen])

  /**
   * 调度器启停。
   *
   * 历史坑：这里原先只有 try/finally —— 请求失败时 finally 照常跑，3 秒后按钮
   * 从「启动中」变回「启动」，界面没有任何错误文本（而 api.ts 层的裸 fetch 连
   * r.ok 都不判，catch 根本不会触发）。用户看到的是"点了没反应，也无从排查"。
   * 现在：错误落 orderMsg；**失败信息不自动消失**（成功才 1.8 秒后清），
   * 保证用户来得及读。见 docs/review/01-p0-交互急救.md 缺陷 2。
   */
  const runRunnerAction = async (kind: 'start' | 'stop') => {
    setBusy(kind)
    try {
      await (kind === 'start' ? runnerStart() : runnerStop())
      setOrderMsg(kind === 'start' ? '调度器启动中…' : '调度器停止中…')
      window.setTimeout(() => setOrderMsg(''), 1800)
    } catch (e) {
      setOrderMsg(`${kind === 'start' ? '启动' : '停止'}失败：${String(e)}`)
      // 不设自动清除 —— 失败信息必须留在屏幕上
    } finally {
      window.setTimeout(() => {
        setBusy('')
        reload()
      }, 3000)
    }
  }
  const start = () => void runRunnerAction('start')
  const stop = () => void runRunnerAction('stop')

  /** 拖完写回配置：tasks.order（全量，轮巡组保持在最前）+ tasks.main_order
   *  （四个主任务按新相对顺序）——**两个都写拖动才真的影响调度**：主任务组互斥时
   *  看的是 main_order，只改 order 会"拖了但没生效"。（与 旧版 的 submitTaskOrder 一致） */
  const onReorder = useCallback(async (restOrder: string[]) => {
    const loopKeys = Object.keys(TASK_NAME).filter(isLoopTask)
    const full = [...loopKeys, ...restOrder.filter((k) => !loopKeys.includes(k))]
    const mains = restOrder.filter((k) => TASK_GROUP[k] === '主线')
    try {
      const r = await saveSettings({
        task_order: full.join('>'),
        main_order: mains.join('>'),
      })
      setOrderMsg(r.ok ? '任务顺序已保存 · 下一轮调度生效' : `保存失败：${r.rejected.join('、')}`)
    } catch (e) {
      setOrderMsg(`保存失败：${String(e)}`)
    } finally {
      window.setTimeout(() => setOrderMsg(''), 2200)
      reload()
    }
  }, [reload])

  /** 单字段保存（通知页的开关/文本框、设置页的表单都走这里）。
   *  与 旧版 的「改动即自动保存」一致：后端 ruamel 往返写 config.yaml，下一轮生效。
   *
   *  **失败时向外抛**（而不是吞掉）：TaskList 的乐观更新要靠它回滚
   *  （见 TaskList.handleToggle）。原先这里 catch 后不 rethrow，导致调用方的
   *  .catch 永远不触发、乐观值永不消解 —— 与 01 缺陷 2 同一个模式。
   *  失败信息也**不自动清除**，保证用户来得及读。 */
  const saveOne = useCallback(
    async (updates: Record<string, unknown>) => {
      try {
        const r = await saveSettings(updates)
        if (!r.ok) {
          setOrderMsg(`保存失败：${r.rejected.join('、') || '后端拒绝'}`)
          throw new Error(r.rejected.join('、') || '后端拒绝')
        }
        setOrderMsg('已保存')
        window.setTimeout(() => setOrderMsg(''), 1800)
      } catch (e) {
        setOrderMsg(`保存失败：${String(e)}`)
        throw e
      } finally {
        reload()
      }
    },
    [reload],
  )

  if (error && !data) {
    const needsLogin = error.includes('HTTP 401') || error.includes('访问验证')
    if (needsLogin && !loginRequested.current) {
      loginRequested.current = true
      onLoginRequired()
    }
    return (
      <div className="app">
        <main>
          <section className="home" data-page="main">
            <div style={{ padding: 'calc(var(--u) * 40)', color: '#b91c1c' }}>
              连接失败：{error}
              <button
                type="button"
                onClick={onLoginRequired}
                style={{
                  display: 'block', marginTop: 18, minHeight: 44,
                  padding: '0 18px', border: 0, borderRadius: 10,
                  background: '#FF990F', color: '#5A2D00', fontSize: 16,
                }}
              >
                重新验证访问码
              </button>
            </div>
          </section>
        </main>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="app">
        <main>
          <section className="home" data-page="main">
            <div style={{ padding: 'calc(var(--u) * 40)', opacity: 0.6 }}>加载中…</div>
          </section>
        </main>
      </div>
    )
  }

  if (guideOpen) {
    return (
      <div className="app">
        <main>
          <div className="page-slot page-enter">
            <Onboarding
              data={data}
              onSave={saveOne}
              onLater={() => {
                setGuideMode('closed')
                go('main')
              }}
              onDone={() => {
                setGuideMode('closed')
                go('main')
                reload()
              }}
            />
          </div>
        </main>
      </div>
    )
  }

  /** 按**视图**渲染（页面 + 二级键）。抽成函数是为了让"正在退场的那个视图"也能被渲染
   *  —— iOS 式转场需要前后两个视图同时在场（单页滑入那种是旧页瞬间消失，不像原生）。
   *  grp/sub 一律走参数，**不能读闭包里的全局值**：退场的那个视图往往带着不同的二级键
   *  （例如从设置二级退回一级时，退场视图是 grp=work、进场视图是 grp=null）。
   *  第二参数显式传数据，避免依赖闭包里的类型窄化。 */
  const renderView = (v: View, d: Data) => {
    const p = v.page
    if (p === 'main') {
      // 总览页两版并存：**默认是正常流版（V2）**，带 `?ui=v1` 可切回绝对定位版。
      // V1 保留一段时间用于对照（它的圆钮等全部是 position:absolute，
      // 坐标逐颗写死；V2 全部走正常流 —— 可用 ?ui=v1 / ?ui=v2 直接对比）。
      const useV1 =
        typeof window !== 'undefined' &&
        new URLSearchParams(window.location.search).get('ui') === 'v1'
      const OverviewView = useV1 ? Overview : OverviewV2
      return (
        <OverviewView
          data={d}
          busy={busy}
          scene={scene}
          onNav={go}
          onStart={() => void start()}
          onStop={() => void stop()}
          onReorder={(o) => void onReorder(o)}
          // 不写 void —— TaskList 要靠这个 Promise 决定是否回滚乐观值
          onToggle={(k, on) => saveOne({ [`${k}_enabled`]: on })}
        />
      )
    }
    if (p === 'log') {
      return (
        <section className="card" data-page="log">
          <NavHead title="日志" onBack={() => go('main')} />
          <LogPage shots={d.shots ?? []} sub={v.sub} onSub={(k) => openSub('sub', k)} />
        </section>
      )
    }
    if (p === 'shot') {
      return (
        <section className="shotpage" data-page="shot">
          <NavHead title="实时画面" onBack={() => go('main')} />
          <LivePage />
        </section>
      )
    }
    if (p === 'adv') {
      return (
        <section className="card" data-page="adv">
          <NavHead title="冒险记录" onBack={() => go('main')} />
          <AdvPage />
        </section>
      )
    }
    if (p === 'plan') {
      // 职业树自带官方样式的整屏头（返回圆钮 + 标题 + 属性胶囊 + 副标题），
      // **不能再套 NavHead** —— 那会变成两条标题栏叠在一起。
      return (
        <section className="card" data-page="plan">
          <PlanPage />
        </section>
      )
    }
    if (p === 'notify') {
      return (
        <section className="card" data-page="notify">
          <NavHead title="通知" onBack={() => go('main')} />
          <NotifyPage editable={d.editable ?? {}} onSave={saveOne} />
        </section>
      )
    }
    if (p === 'set') {
      return (
        <section className="card" data-page="set">
          <SettingsPage
            editable={d.editable ?? {}}
            access={d.access}
            onSave={saveOne}
            onExit={() => go('main')}
            grp={v.grp}
            onOpen={(id) => openSub('grp', id)}
            onBackSub={backSub}
            onOnboarding={() => setGuideMode('open')}
          />
        </section>
      )
    }
    return null
  }

  return (
    <div className="app">
      {/* className 带的方向 class 变化即触发滑入动画（不需要给 main 加 key，
          key 会重建整棵子树、把内页状态和滚动位置一起清掉）。 */}
      <main>
        {/* 退场与进场 key 分开：快速返回时同一视图会短暂同时扮演两个角色。 */}
        {leaving ? (
          <div
            key={'leave:' + scrollKeyOf(leaving.page, leaving.grp, leaving.sub)}
            ref={leaveSlot}
            className={'page-slot page-leave page-leave-' + dir}
            aria-hidden="true"
            onAnimationEnd={(e) => {
              if (e.target === e.currentTarget) finishTransition()
            }}
          >
            {renderView(leaving, data)}
          </div>
        ) : null}
        {/* 进场 slot 的 key **不含 sub**：日志两个子页共用同一个 slot，tab 条不重建，
            白色药丸的 180ms 过渡才跑得起来；页面 / 设置分组（grp）变化仍换 key，
            保持整页转场。 */}
        <div
          key={'enter:' + scrollKeyOf(page, grp, null)}
          className={'page-slot page-enter' + (transiting ? ' page-enter-' + dir : '')}
        >
          {renderView({ page, grp, sub }, data)}
        </div>
      </main>

      {/* 拖拽保存结果的轻提示（复用 #torderToast 的样式，与 旧版 一致） */}
      <div id="torderToast" className={orderMsg ? 'on' : ''}>
        {orderMsg}
      </div>
    </div>
  )
}
