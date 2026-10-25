import { assetUrl } from './connection'

/**
 * 房间背景库与推导逻辑（从旧版原生实现移植，含注释里的那些坑）。
 *
 * file/dark：`static/qp-icons/bg/<file>.jpg`。深色模式**必须换图**——家居背景的暗版
 *   是官方的夜灯/月光版，不是同一张调暗。
 * sb/sbDark：该图**顶部实测色**，PWA 独立窗口的状态栏那条带取它。
 *   CSS `:root` 里的 `--qp-room-*` / `--qp-sb-*` 只当"JS 还没跑"的首帧兜底。
 */

/** 背景图版本号：**换图后必须 +1**。`/qp-icons/*` 走 `Cache-Control: max-age=3600`，
 *  同名覆盖时浏览器一小时内仍显示旧图（踩过：素材已更新、页面还是旧的）。
 *  6 = 新增「花田诗意」动态背景（huatian-day/night + 两个 loop.mp4）。 */
export const BG_VER = '6'

export interface SceneInfo {
  name: string
  file: string
  dark?: string
  sb: string
  sbDark?: string
  /** 动态背景的视频层（相对 `/qp-icons/bg/`，**不带扩展名**，与 file/dark 同风格）。
   *  「花田诗意」是官方第一套灵动背景：房间背景 = 4 秒循环的 mp4（天空/云朵）
   *  叠在一张带 alpha、位置固定的立绘（花树/草坡/树桩台）上。这里只放 mp4 那一层，
   *  立绘已经烘进 file/dark 的静态图里 —— 静态图既是首帧，也是不给视频时的兜底。
   *  `videoDark` 对应深色模式（官方夜版是月光场景，不是把白天调暗）。 */
  video?: string
  videoDark?: string
}

export const SCENE_INFO: Record<string, SceneInfo> = {
  // 官方 5 套房间场景
  main: { name: '主房间', file: 'room-main', dark: 'room-main-dark', sb: '#D5A758', sbDark: '#A9722D' },
  feed: { name: '喂食区', file: 'room-feed', dark: 'room-feed-dark', sb: '#CA9F5B', sbDark: '#C39145' },
  shower: { name: '浴室', file: 'room-shower', dark: 'room-shower-dark', sb: '#E7BC6C', sbDark: '#A3651D' },
  record: { name: '教室 / 打工', file: 'room-record', dark: 'room-record-dark', sb: '#CAA05C', sbDark: '#C18C3A' },
  // 官方那套房间场景里还有个「商店」（room-store）—— 用户要求去掉，已移除。
  // 移除是安全的：SCENE_OF 里没有任何任务映射到 store，computeScene 也不会返回它；
  // readManualScene 会校验值是否还在 SCENE_INFO 里，所以旧 localStorage 里留下的
  // 'store' 会自动回落到「自动」，不会白屏。图片文件仍在磁盘上，随时可加回来。
  // 官方「装扮 → 背景」16 款（顺序照官方页面从上到下、左到右；最前面那款 2026-10 新上）
  // 「花田诗意」是 2026-10 上新的**灵动背景**（背景 id 61），排在官方列表第一位。
  // 它是唯一带视频层的：顶 820px 的 mp4 循环 + 全天候立绘叠层，只有 9.3.75+ 客户端能播。
  // 素材来源见 qqpet_assets/_raw/pet_home_bg_dynamic/（root 从 app 私有目录拉的原始包）。
  'home-huatian': {
    name: '花田诗意',
    file: 'huatian-day',
    dark: 'huatian-night',
    sb: '#9BC9FB',
    sbDark: '#152578',
    video: 'huatian-day-loop',
    videoDark: 'huatian-night-loop',
  },
  'home-yueer': { name: '月儿圆圆', file: 'home-yueer', dark: 'home-yueer-dark', sb: '#E0BA96', sbDark: '#252C46' },
  'home-sunset': { name: '朝朝落霞', file: 'home-sunset', dark: 'home-sunset-dark', sb: '#E9EEFD', sbDark: '#B7ADB8' },
  'home-starry': { name: '夕夕星河', file: 'home-starry', dark: 'home-starry-dark', sb: '#E6ECFE', sbDark: '#BFB0B7' },
  'home-ocean': { name: '浪花泡泡鱼', file: 'home-ocean', dark: 'home-ocean-dark', sb: '#B2E1FB', sbDark: '#5F8FB8' },
  'home-nordic': { name: '简约星阁', file: 'home-nordic', dark: 'home-nordic-dark', sb: '#A9AAB5', sbDark: '#C5BAB3' },
  'home-coast': { name: '意式海岸', file: 'home-coast', dark: 'home-coast-dark', sb: '#EFE4E2', sbDark: '#D7BBA1' },
  'home-geo': { name: '撞色几何', file: 'home-geo', dark: 'home-geo-dark', sb: '#F7D374', sbDark: '#D4A976' },
  'home-mint': { name: '薄荷清新', file: 'home-mint', dark: 'home-mint-dark', sb: '#BDBEBA', sbDark: '#D1CCC1' },
  'home-sunny': { name: '暖阳午后', file: 'home-sunny', dark: 'home-sunny-dark', sb: '#ECD0C0', sbDark: '#DCC7B9' },
  'home-greyblue': { name: '沉稳灰蓝', file: 'home-greyblue', dark: 'home-greyblue-dark', sb: '#99A6B6', sbDark: '#C0BCBE' },
  'home-pink': { name: '粉色童话', file: 'home-pink', dark: 'home-pink-dark', sb: '#FBDEDA', sbDark: '#F2C5BF' },
  'home-green': { name: '绿色童话', file: 'home-green', dark: 'home-green-dark', sb: '#B9D1BA', sbDark: '#CAC9AF' },
  'home-blue': { name: '蓝色童话', file: 'home-blue', dark: 'home-blue-dark', sb: '#CCDDED', sbDark: '#D1D8E0' },
  'home-snow': { name: '蓝色雪花', file: 'home-snow', dark: 'home-snow-dark', sb: '#7A9CBE', sbDark: '#6383A2' },
  'home-yellowpaw': { name: '黄色爪爪', file: 'home-yellowpaw', dark: 'home-yellowpaw-dark', sb: '#DAA95D', sbDark: '#CEA367' },
  // 官方「宠物职业小镇」7 个打工地点主题（到对应职业解锁）+ 高级学院毕业奖励
  'career-caihong': { name: '彩虹画室', file: 'career-caihong', dark: 'career-caihong-dark', sb: '#DFCFCB', sbDark: '#BC9076' },
  'career-miwu': { name: '迷雾侦探所', file: 'career-miwu', dark: 'career-miwu-dark', sb: '#8E8B7E', sbDark: '#3D3630' },
  'career-zhuying': { name: '竹影武馆', file: 'career-zhuying', dark: 'career-zhuying-dark', sb: '#8499A0', sbDark: '#2F4064' },
  'career-shanyao': { name: '闪耀星屋', file: 'career-shanyao', dark: 'career-shanyao-dark', sb: '#D7D2F0', sbDark: '#2C2664' },
  'career-yunduo': { name: '云朵梦舍', file: 'career-yunduo', dark: 'career-yunduo-dark', sb: '#106AC6', sbDark: '#101A62' },
  'career-xingchen': { name: '星尘魔法塔', file: 'career-xingchen', dark: 'career-xingchen-dark', sb: '#4E3163', sbDark: '#2C2352' },
  'career-gulu': { name: '咕噜厨房', file: 'career-gulu', dark: 'career-gulu-dark', sb: '#E8CAAF', sbDark: '#A17559' },
  'career-graduate': { name: '高级学院毕业', file: 'career-graduate', dark: 'career-graduate-dark', sb: '#ECD4CB', sbDark: '#CEC9BA' },
}

/** 长按循环顺序 + 选择面板顺序（= 上面声明顺序） */
export const SCENE_ORDER: string[] = Object.keys(SCENE_INFO)

/** 任务键 → 场景（自动模式） */
export const SCENE_OF: Record<string, string> = {
  care: 'feed',
  friend_care: 'feed',
  school: 'record',
  work: 'record',
  hire_friend: 'record',
  adventure: 'main',
  visit: 'main',
  pk: 'main',
  gift_bag: 'main',
}

export const isDarkTheme = (): boolean =>
  !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)

/** 自动模式下由"正在做的事"推导场景 */
export function computeScene(curKey: string, etaKind: string): string {
  const k = etaKind || ''
  if (k.includes('洗澡') || k.includes('护理')) return 'shower'
  if (k.includes('上课') || k.includes('学习') || k.includes('打工')) return 'record'
  if (curKey) return SCENE_OF[curKey] || 'main'
  return 'main'
}

export const sceneBgFile = (key: string, dark = isDarkTheme()): string => {
  const info = SCENE_INFO[key]
  if (!info) return ''
  return dark && info.dark ? info.dark : info.file
}

export const sceneBgUrl = (key: string, dark = isDarkTheme()): string => {
  const f = sceneBgFile(key, dark)
  return f ? `url('${assetUrl(`/qp-icons/bg/${f}.jpg?v=${BG_VER}`)}')` : ''
}

/** 动态背景的视频层文件（不带扩展名）；没有就是空串 = 纯静态背景。 */
export const sceneVideoFile = (key: string, dark = isDarkTheme()): string => {
  const info = SCENE_INFO[key]
  if (!info) return ''
  return (dark && info.videoDark) || info.video || ''
}

export const sceneVideoUrl = (key: string, dark = isDarkTheme()): string => {
  const f = sceneVideoFile(key, dark)
  return f ? assetUrl(`/qp-icons/bg/${f}.mp4?v=${BG_VER}`) : ''
}

export const sceneName = (key: string): string =>
  key === 'auto' ? '自动' : (SCENE_INFO[key]?.name ?? key)

const VIDEO_EL_ID = 'qpRoomVideo'

/** 自动播放被拒是常态（低电量模式 / 无用户交互 / 企业策略），静默即可 ——
 *  底下垫着的静态图就是同一帧，用户看不出差别。 */
function playQuietly(v: HTMLVideoElement): void {
  const p = v.play()
  if (p && typeof p.catch === 'function') p.catch(() => undefined)
}

/** 动态背景的视频层：一个盖在 `html` 背景图之上、`pointer-events:none` 的 <video>。
 *
 *  **为什么不用 `<div style="background-image">` 或 canvas**：mp4 只能交给浏览器解码器，
 *  `<video>` 是唯一能自动循环、自动降级（解码不了就只剩底下的静态图）的容器。
 *
 *  几个必须守住的点：
 *  - **`muted` + `playsInline`**：iOS 上不带 muted 的视频不允许自动播放；
 *    `playsInline` 不加的话 iPhone 会劫持成全屏播放器（整页被视频顶掉）。
 *  - **挂在 `document.body` 而不是 `html`**：`html` 的 `background-image` 是画在自己身上的，
 *    给它插子元素会参与 html 的布局（`height:100%` 链条上多一层，见 FRONTEND_PITFALLS「高度链」），
 *    而 body 已经有确定性高度，`position:fixed` 的子元素只当独立图层用，不撑任何父级。
 *  - **只有当前场景有 `video` 时才创建**，切走就删掉并 `pause()` —— 常驻一个解码器
 *    在手机上是很实在的电量/内存开销。
 *  - 静态图（`--qp-room`）始终在视频下层：视频没加载完/解码失败/省流量模式下，
 *    用户看到的就是同一帧的静态版，不会闪黑或空白。 */
function syncSceneVideo(key: string, dark: boolean): void {
  // 系统开了「减弱动态效果」就完全不创建视频：这套背景唯一的价值就是那点动效，
  // 对晕动症/低电量用户来说静态图反而是正确表现（官方客户端也遵守这条）。
  const reduceMotion = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const url = reduceMotion ? '' : sceneVideoUrl(key, dark)
  const existing = document.getElementById(VIDEO_EL_ID)
  if (!url) {
    if (existing) {
      const v = existing as HTMLVideoElement
      v.pause()
      v.removeAttribute('src')
      v.load()            // 让解码器立刻释放，而不是等 GC
      v.remove()
    }
    return
  }
  if (existing) {
    const v = existing as HTMLVideoElement
    if (v.getAttribute('data-src') === url) return   // 同一个视频，别重载（会闪一帧黑）
    v.pause()
    v.setAttribute('data-src', url)
    v.src = url
    v.load()
    playQuietly(v)
    return
  }
  const video = document.createElement('video')
  video.id = VIDEO_EL_ID
  video.className = 'qp-room-video'
  video.muted = true
  video.defaultMuted = true
  video.loop = true
  video.autoplay = true
  video.playsInline = true
  video.setAttribute('muted', '')
  video.setAttribute('playsinline', '')
  video.setAttribute('webkit-playsinline', '')
  video.setAttribute('disablepictureinpicture', '')
  video.setAttribute('aria-hidden', 'true')
  video.setAttribute('data-src', url)
  // 首帧优先：这套背景一进页面就要看得见，等它慢慢预加载会出现「先静态再跳一下」
  video.preload = 'auto'
  video.src = url
  document.body.appendChild(video)
  playQuietly(video)
}

/** 把背景与状态栏色写到 `html` 上（CSS 变量 + data-scene）。
 *  家居背景 15 套 × 明/暗 = 30 张，逐个写 CSS 规则会重复两份表，所以由 JS 内联写。
 *  带 `video` 的（目前只有「花田诗意」）另外同步视频层。 */
export function applyScene(key: string, dark = isDarkTheme()): void {
  const root = document.documentElement
  const info = SCENE_INFO[key]
  root.setAttribute('data-scene', key)
  if (!info) {
    syncSceneVideo('', dark)
    return
  }
  root.style.setProperty('--qp-room', sceneBgUrl(key, dark))
  const sb = dark && info.sbDark ? info.sbDark : info.sb
  if (sb) root.style.setProperty('--qp-statusbar', sb)
  syncSceneVideo(key, dark)
}

const LS_KEY = 'qpet_scene'

export function readManualScene(): string {
  try {
    const v = localStorage.getItem(LS_KEY)
    if (v === 'auto' || (v && SCENE_INFO[v])) return v
  } catch {
    /* 隐私模式下 localStorage 会抛，忽略 */
  }
  return 'auto'
}

export function writeManualScene(key: string): void {
  try {
    localStorage.setItem(LS_KEY, key)
  } catch {
    /* 同上 */
  }
}
