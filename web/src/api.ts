import {
  ConnectionError,
  apiUrl,
  clearDeviceToken,
  ensureDeviceId,
  normalizeServer,
  readConnection,
  saveDeviceToken,
  saveServer,
} from './lib/connection'

export {
  ConnectionError,
  apiUrl,
  clearDeviceToken,
  ensureDeviceId,
  normalizeServer,
  readConnection,
  saveDeviceToken,
  saveServer,
}

// 与 dashboard.py 的 /api/* 对齐的类型定义。
// 以前这些字段靠脑记（`care_energy`、`work_eta`、`adventure_type`…），现在有编译器兜着。

/** GET /api/data —— 总览页的主数据源 */
export interface Data {
  now: string
  scheduler: SchedulerInfo
  queue: QueueInfo
  status: PetStatus
  progress: Record<string, TaskProgress>
  config: ConfigSnapshot
  /** 今日学习/打工时长与效率档（分钟） */
  today_duration?: TodayDuration
  /** 进行中的活动（kind = 上课/打工/冒险，remaining = 剩余秒） */
  work_eta?: WorkEta
  /** 设置页可编辑项的快照（含 stop_total_hours 等） */
  editable?: Record<string, unknown>
  shots?: Shot[]
  friends?: string[]
  last_line?: string
  /** 访问权限状态（设置 → 访问码 用；不含访问码本身） */
  access?: AccessInfo
  onboarding?: OnboardingStatus
  /** 应用版本（设置页底部显示；手机端只能从这里看版本号） */
  app?: { version?: string }
}

/** GET /api/update —— 电脑端 App 的更新器状态（手机远程更新用） */
export interface RemoteUpdateStatus {
  phase: 'idle' | 'checking' | 'latest' | 'available'
    | 'downloading' | 'installing' | 'restarting' | 'error'
  current: string
  latest: string
  notes: string
  error: string
  installable: boolean
  done: number
  total: number | null
  /** 最近一次真正联网检查的时间（epoch 秒） */
  lastChecked: number | null
  /** 桌面壳最后一次心跳（epoch 秒）；过期说明 App 没在运行 */
  updated_at?: number
}

export interface RemoteUpdateState {
  running: boolean
  status: RemoteUpdateStatus | null
}

export interface OnboardingStatus {
  version: number
  required: boolean
  completed: boolean
}

export interface AccessInfo {
  /** 访问码是不是用户自己设的（false = 自动生成的长随机串） */
  custom: boolean
  /** 自定义访问码的最短长度 */
  min_length: number
}

export interface SchedulerInfo {
  alive: boolean
  pid: number
  uptime: string
}

export interface TodayDuration {
  learn_min: number
  work_min: number
  eff_pct: number
  total_min: number
  next_pct: number
  next_in_min: number
}

export interface WorkEta {
  eta_clock: string
  remaining: number
  /** 中文活动名：上课 / 打工 / 冒险 */
  kind: string
}

export interface Shot {
  name: string
  mtime: string
}

export interface QueueTaskState {
  state: string
  next?: string
  next_ts?: number
}

export interface QueueInfo {
  current: string
  pending: string
  pending_key: string
  next: string
  next_at: string
  next_ts: number
  /** 为什么在等（人类可读，如「雇佣好友：想雇的「墨瞳」正打工中」）——
   *  由 TaskQueueRunner 产出，仪表盘的"等待中"下面显示这句 */
  next_reason?: string
  /** 为什么在做当前这件事（如「打工要雇的「柠檬..」正忙，先去冒险；到点回去雇他」）：
   *  场景让路时随 TaskDeferred.note 登记，只在执行那个优先任务期间有值 */
  note?: string
  ready: number
  waiting: number
  tasks: Record<string, QueueTaskState>
  pid: number
  started: number
  updated: string
  /** 调度器刚起来、还没实测完当前状态（消费端显示"启动中"，见 src/queue_status.py） */
  starting?: boolean
  /** 调度器已停止（消费端据此显示"已停止"，而不是把上一轮残留的活动当现状） */
  stopped?: boolean
}

export interface PetStatus {
  coins: number
  updated: string
  pet_name: string
  energy: number | null
  clean: number | null
  mood: number | null
}

export interface TaskProgress {
  date?: string
  learned?: number
  done?: boolean
  /** 最近从选课面板识别到的学园阶段 */
  school?: string
  study_secs?: number
  work_secs?: number
  duration?: string
  [k: string]: unknown
}

export interface ConfigSnapshot {
  strategy: string
  school_enabled: boolean
  work_enabled: boolean
  work_location: string
  work_duration: string
  coin_threshold: number
  study_quota_hours: number
  work_quota_hours: number
  visit_per_day: number
  pk_per_day: number
  adventure_times: number
  adventure_start: string
  [k: string]: unknown
}

/** 任务键 → 中文名（与后端 key 一一对应）
 *  **没有 hire_friend**：它已并入 work（打工前顺路去好友家点 hire）
 *  **没有 friend_care**：它已并入 care（护理时顺路去好友家护理一次，开关仍在 friend_care 段）
 *  见 src/config.py 的 TASK_KEYS */
export const TASK_NAME: Record<string, string> = {
  care: '护理',
  school: '学习',
  gift_bag: '福袋',
  adventure: '冒险',
  visit: '踩踩',
  pk: 'PK',
  work: '打工',
}

/** 任务类型标签：「循环」= 按间隔巡检；「每日」= 每天定时；「主线」= 主任务组互斥 */
export const TASK_GROUP: Record<string, '循环' | '每日' | '主线'> = {
  care: '循环',
  gift_bag: '循环',
  visit: '每日',
  pk: '每日',
  adventure: '主线',
  school: '主线',
  work: '主线',
}

/** 任务名（中文）→ 任务键。后端 `queue.current` 存的是 TaskQueueRunner 写的
 *  `task.name`（中文，如「PK」「护理」「福袋」），而任务行高亮等地方要的是任务键
 *  （`pk` / `care` / `gift_bag`）—— 直接拿中文名查键表永远命中不了。 */
export const keyOfTaskName = (name: string | undefined): string =>
  name ? (KEY_OF_NAME[name] ?? '') : ''

const KEY_OF_NAME: Record<string, string> = Object.fromEntries(
  Object.entries(TASK_NAME).map(([k, v]) => [v, k]),
)

/** 日常轮巡组（顺序固定，不参与拖动排序） */
export const isLoopTask = (k: string): boolean => TASK_GROUP[k] === '循环'

/** Shared request wrapper for cookie sessions and paired-device tokens. */
export async function requestJSON<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const c = readConnection()
  const headers = new Headers(init.headers)
  if (c.token) headers.set('Authorization', `Bearer ${c.token}`)
  if (init.body && typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const r = await fetch(apiUrl(path, c.server), {
    ...init,
    headers,
    cache: 'no-store',
    // A remote client must not depend on a cookie issued by another origin.
    credentials: c.server ? 'omit' : 'same-origin',
  })
  if (!r.ok) {
    let msg = `${path} → HTTP ${r.status}`
    try {
      const body = await r.json() as { msg?: string }
      if (body?.msg) msg = body.msg
    } catch {
      /* non-JSON error body */
    }
    throw new ConnectionError(msg, r.status)
  }
  return (await r.json()) as T
}

async function getJSON<T>(url: string): Promise<T> {
  return requestJSON<T>(url)
}

/** Public endpoint: send neither a cookie nor a device token. */
async function requestJSONWithoutAuth<T>(path: string): Promise<T> {
  const c = readConnection()
  const r = await fetch(apiUrl(path, c.server), {
    cache: 'no-store',
    credentials: 'omit',
  })
  if (!r.ok) throw new ConnectionError(`${path} → HTTP ${r.status}`, r.status)
  return (await r.json()) as T
}

export const fetchData = (): Promise<Data> => getJSON<Data>('/api/data')

/** 电脑端更新桥：状态由桌面壳每 2 秒镜像，请求由桌面壳轮询执行。 */
export const fetchRemoteUpdate = (): Promise<RemoteUpdateState> =>
  getJSON<RemoteUpdateState>('/api/update')

export const requestRemoteUpdate = (
  action: 'check' | 'install',
): Promise<{ ok: boolean; msg?: string }> =>
  requestJSON('/api/update', { method: 'POST', body: JSON.stringify({ action }) })

export const completeOnboarding = (): Promise<{ ok: boolean; onboarding: OnboardingStatus }> =>
  requestJSON('/api/onboarding', {
    method: 'POST',
    body: JSON.stringify({ action: 'complete' }),
  })

export interface AdbDevice {
  serial: string
  state: string
  model?: string
  product?: string
}

export interface AdbInfo {
  path: string
  resolved: string
  serial: string
  devices: AdbDevice[]
  error: string
}

export const fetchAdb = (): Promise<AdbInfo> => getJSON('/api/adb')

export const connectAdb = (addr: string): Promise<{ ok: boolean; msg: string; addr?: string }> =>
  requestJSON('/api/adb/connect', { method: 'POST', body: JSON.stringify({ addr }) })

/** A screenshot only reads the device; it never opens QQ or starts a task. */
export async function fetchDeviceScreenshot(): Promise<{ blob: Blob; at: string }> {
  const c = readConnection()
  const r = await fetch(apiUrl(`/api/screenshot?t=${Date.now()}`, c.server), {
    cache: 'no-store',
    credentials: c.server ? 'omit' : 'same-origin',
    headers: c.token ? { Authorization: `Bearer ${c.token}` } : {},
  })
  if (!r.ok) {
    const detail = (await r.text().catch(() => '')).trim().slice(0, 160)
    throw new Error(detail || `获取画面失败 HTTP ${r.status}`)
  }
  return { blob: await r.blob(), at: r.headers.get('X-Shot-At') || '' }
}

export interface SettingsResult {
  ok: boolean
  applied: Record<string, unknown>
  rejected: string[]
}

/** POST /api/settings —— 设置页保存（后端用 ruamel 往返写 config.yaml，保留注释） */
export async function saveSettings(
  updates: Record<string, unknown>,
): Promise<SettingsResult> {
  const c = readConnection()
  const r = await fetch(apiUrl('/api/settings', c.server), {
    method: 'POST',
    credentials: c.server ? 'omit' : 'same-origin',
    headers: {
      'Content-Type': 'application/json',
        ...(c.token ? { Authorization: `Bearer ${c.token}` } : {}),
    },
    body: JSON.stringify({ updates }),
  })
  const body = await r.json().catch(() => null) as SettingsResult | null
  if (!body || typeof body.ok !== 'boolean' || !Array.isArray(body.rejected)) {
    throw new Error(`保存失败 HTTP ${r.status}`)
  }
  return body
}

/** 调度器启停（功能栏的两个按钮）的返回体 */
export interface RunnerActionResult {
  ok: boolean
  msg?: string
}

/** Public protocol metadata; no secrets and no pairing ticket. */
export interface ClientCapabilities {
  ok: boolean
  protocol_version: number
  server: {
    name: string
    protocol_version: number
  }
  capabilities: string[]
  pairing: {
    available: boolean
    ticket_ttl_seconds: number
  }
  auth: {
    schemes: string[]
    device_tokens: boolean
  }
}

export const fetchClientMeta = (): Promise<ClientCapabilities> =>
  requestJSONWithoutAuth<ClientCapabilities>('/api/client/meta')

export interface PairingTicket {
  ok: boolean
  protocol_version: number
  ticket: string
  expires_at: number
  expires_at_iso: string
  ticket_ttl_seconds: number
  candidate_base_urls: string[]
  /** Canonical qpet://pair?... link used by the QR code. */
  deep_link: string
  deep_links: string[]
  qr_payload: string
  /** PNG data-URL generated by the server. */
  qr_image?: string
}

/** Authenticated: create a short-lived one-time pairing ticket. */
export const createPairingTicket = (): Promise<PairingTicket> =>
  requestJSON<PairingTicket>('/api/client/pairing', { method: 'POST' })

export interface PairedSession {
  ok: boolean
  /** Returned as `device.id` by protocol v1. */
  device: DeviceRecord
  token: string
  server: string
  protocol: number
  capabilities: string[]
  server_name?: string
}

let pairedSession: PairedSession | null = null

/** Last successful pairing in this tab; used to show the connection chip. */
export const readPairedSession = (): PairedSession | null => pairedSession

/** Validate the stored device token and load public server metadata. */
export const refreshClientSession = async (): Promise<PairedSession> => {
  const c = readConnection()
  if (!c.server || !c.token) throw new ConnectionError('尚未连接托管服务器', 401)
  const body = await requestJSON<{
    ok: boolean
    server: { name: string; protocol_version: number }
    device: DeviceRecord | null
    auth_type: string
    capabilities: string[]
  }>('/api/client/session')
  pairedSession = {
    ok: true,
    device: body.device as DeviceRecord,
    token: c.token,
    server: c.server,
    protocol: body.server.protocol_version,
    capabilities: body.capabilities,
    server_name: body.server.name,
  }
  return pairedSession
}

/** Cheap connectivity check used before retrying a saved connection. */
export const probeClientMeta = (): Promise<ClientCapabilities> => fetchClientMeta()

/** Exchange a ticket for a long-lived device token (unauthenticated by design). */
export async function completePairing(
  ticket: string,
  server: string,
  name: string,
  platform = 'android-web',
): Promise<PairedSession> {
  const normalized = normalizeServer(server)
  const r = await fetch(apiUrl('/api/client/pair', normalized), {
    method: 'POST',
    credentials: 'omit',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ticket,
      name,
      platform,
      device_id: ensureDeviceId(),
    }),
  })
  const body = await r.json().catch(() => null) as Partial<PairedSession> & { msg?: string } | null
  if (!r.ok || !body?.ok || !body.token || !body.device?.id) {
    throw new ConnectionError(body?.msg || `配对失败 HTTP ${r.status}`, r.status)
  }
  saveDeviceToken(body.token, body.device.id)
  saveServer(normalized)
  pairedSession = { ...body, token: body.token, server: normalized } as PairedSession
  return pairedSession
}

export interface DeviceRecord {
  id: string
  name: string
  platform: string
  /** ISO-8601 timestamps from the protocol store. */
  created_at: string
  last_seen: string | null
}

export const fetchDevices = (): Promise<{ devices: DeviceRecord[] }> =>
  getJSON('/api/client/devices')

export const revokeDevice = (id: string): Promise<{ ok: boolean }> =>
  requestJSON(`/api/client/devices/${encodeURIComponent(id)}`, { method: 'DELETE' })

/**
 * 调度器启停。
 *
 * ⚠ 这里**必须**判 `r.ok` 并把后端的 msg 透传出去：
 * 后端在失败时返回 **HTTP 400 + `{ok:false, msg:"调度器已在运行（PID N），不重复启动"}`**，
 * 而裸 `fetch` 对 400 是正常 resolve 的 —— 调用方的 catch 永远不会触发，
 * 界面表现为"点了启动没反应"，且无从排查（见 docs/review/01 缺陷 2）。
 */
async function runnerAction(path: string): Promise<RunnerActionResult> {
  const c = readConnection()
  const r = await fetch(apiUrl(path, c.server), {
    method: 'POST',
    credentials: c.server ? 'omit' : 'same-origin',
    headers: c.token ? { Authorization: `Bearer ${c.token}` } : {},
  })
  let body: RunnerActionResult = { ok: r.ok }
  try {
    body = (await r.json()) as RunnerActionResult
  } catch {
    // 后端异常时可能没有 JSON 体；此时至少把状态码带出去
  }
  if (!r.ok || body.ok === false) {
    throw new Error(body.msg || `${path} → HTTP ${r.status}`)
  }
  return body
}

export const runnerStart = () => runnerAction('/api/runner/start')
export const runnerStop = () => runnerAction('/api/runner/stop')

/** GET /api/logs —— 当天日志（尾部 N 行） */
export interface LogsData {
  name: string
  total: number
  lines: string[]
}
export const fetchLogs = (): Promise<LogsData> => getJSON<LogsData>('/api/logs')

/** 收益汇总的一个桶（学习 / 打工各一份，另有 today_* 两份） */
export interface RewardBucket {
  sessions: number
  credits: number
  coins: number
  tired: number
  /** 解析出数值的条数 —— 前端据此区分"没收益"与"没解析出来"，不拿 +0 冒充 */
  credits_n: number
  coins_n: number
  workpoints: number
  workpoints_n: number
  /** 看视频加成（结算页「看视频获得 N 金币」），独立一笔、不计进 coins */
  ad_coins: number
  ad_coins_n: number
  attrs: Record<string, number>
}

/** 按次记录的一行：[时间, kind, 标题, 学分, 属性, 金币, 疲惫, 工分, 工资构成] */
export type RewardRow = [
  string,
  string,
  string,
  number | null,
  string,
  number | null,
  number,
  number | null,
  string,
]

export interface RewardsData {
  ok: boolean
  date: string
  dates: string[]
  date_n: Record<string, number>
  today: string
  yesterday: string
  all_n: number
  n: number
  school: RewardBucket
  work: RewardBucket
  today_school: RewardBucket
  today_work: RewardBucket
  recent: RewardRow[]
  /** 结算页头部解析出来的宠物名 / 主人名（换宠物时一眼看出这份数据是谁的） */
  pet: string
  owner: string
  updated: string
}

export const fetchRewards = (date?: string): Promise<RewardsData> =>
  getJSON<RewardsData>(`/api/rewards${date ? `?date=${encodeURIComponent(date)}` : ''}`)

/** 发送一条测试通知（通知页的"发送测试"按钮） */
export async function testNotify(): Promise<{ ok: boolean; msg?: string; results?: Record<string, string> }> {
  const c = readConnection()
  const r = await fetch(apiUrl('/api/notify/test', c.server), {
    method: 'POST',
    credentials: c.server ? 'omit' : 'same-origin',
    headers: c.token ? { Authorization: `Bearer ${c.token}` } : {},
  })
  const body = await r.json().catch(() => null) as { ok?: boolean; msg?: string; results?: Record<string, string> } | null
  if (!body || typeof body.ok !== 'boolean') throw new Error(body?.msg || `HTTP ${r.status}`)
  return { ...body, ok: body.ok }
}

export interface NotificationRecord {
  ts: string
  title: string
  content: string
  ok: boolean
  results: Record<string, string>
}

export const fetchNotifyHistory = (): Promise<{ records: NotificationRecord[] }> =>
  getJSON('/api/notify/history')

/** POST /api/access-key —— 自定义仪表盘访问码。
 *
 *  ⚠ 后端成功时会**同时换发新 Cookie**（会话 = 访问码的 HMAC，换码后旧会话
 *  全部失效）。所以这里必须走 credentials: 'same-origin'，否则浏览器丢掉
 *  Set-Cookie，用户改完码当场被踢回登录页。 */
export async function changeAccessKey(key: string): Promise<{ ok: boolean; msg?: string }> {
  const c = readConnection()
  const r = await fetch(apiUrl('/api/access-key', c.server), {
    method: 'POST',
    credentials: c.server ? 'omit' : 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(c.token ? { Authorization: `Bearer ${c.token}` } : {}),
    },
    body: JSON.stringify({ key }),
  })
  const body = await r.json().catch(() => null) as { ok?: boolean; msg?: string } | null
  if (!body || typeof body.ok !== 'boolean') throw new Error(body?.msg || `HTTP ${r.status}`)
  if (!body.ok) throw new Error(body.msg || '设置失败')
  return { ok: true, msg: body.msg }
}

/** 职业解锁哨兵的状态与历史事件 */
export interface WatchInfo {
  enabled: boolean
  stop_study: boolean
  interval: number
  last_check: string
  alive: boolean
  events: { career: string; name: string; ts: string; stopped: boolean }[]
}

export interface PlanLine {
  name: string
  jr: boolean
  ch: boolean
}

/** 阶梯路线一行：[编号, 描述, 是否完成, 进度说明] */
export type PlanStep = [string, string, boolean, string]

export interface PlanData {
  ok: boolean
  /** 当前属性值（键是单字：力/智/魅/工分/金币 + 初级毕业/中级毕业） */
  values: Record<string, number | boolean>
  total: number
  total_target: number
  /** 8 条线里已解锁的数量 */
  jr_n: number
  ch_n: number
  lines: PlanLine[]
  steps: PlanStep[]
  lines_meta: string
  gates: Record<string, boolean>
  updated: string
  watch: WatchInfo
  /** 职业树页：8 线实测解锁记录（键名可能是「法师」）。见习一级的权威来源。 */
  tree_lines?: Record<string, { unlocked?: boolean; name?: string; at?: string }>
  /** 职业树页：逐级解锁记录 `{线: {层级序号: {...}}}`。哨兵目前只记见习，所以常为空。 */
  tree_tiers?: Record<string, Record<string, { unlocked?: boolean; name?: string }>>
  /** 职业树页：已展示（佩挂）的线与职业名 */
  equipped?: { line: string; name: string } | null
}

export const fetchPlan = (): Promise<PlanData> => getJSON<PlanData>('/api/plan')

/** 保存手工录入的进度 */
export async function savePlan(updates: Record<string, unknown>): Promise<{ ok: boolean; msg?: string }> {
  const c = readConnection()
  const r = await fetch(apiUrl('/api/plan', c.server), {
    method: 'POST',
    credentials: c.server ? 'omit' : 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(c.token ? { Authorization: `Bearer ${c.token}` } : {}),
    },
    body: JSON.stringify({ updates }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as { ok: boolean; msg?: string }
}

/** 出门→职业小镇 OCR 自动识别（子进程隔离，防并发锁） */
export async function syncPlan(): Promise<{ ok: boolean; msg?: string }> {
  const c = readConnection()
  const r = await fetch(apiUrl('/api/plan/sync', c.server), {
    method: 'POST',
    credentials: c.server ? 'omit' : 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(c.token ? { Authorization: `Bearer ${c.token}` } : {}),
    },
    body: '{}',
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as { ok: boolean; msg?: string }
}

/** 冒险记录（统一数据源 runs/adventure_live.jsonl） */
export interface AdvData {
  ok: boolean
  /** 统计范围内的总把数 / 净收益 / 平均每把 */
  n: number
  net: number
  avg: number
  /** 收益档分布：{"20": 69, "0": 180, ...} */
  dist: Record<string, number>
  win: number
  zero: number
  loss: number
  /** 状态增益汇总：[属性名, 次数, 总量] */
  gains: [string, number, number][]
  /** 累计收益曲线 [索引, 值] */
  cum: [number, number][]
  /** 单次收益散点 [索引, 值] */
  pts: [number, number][]
  /** 状态轨迹 [索引, 体力, 清洁, 心情, 是否护理后] */
  stats: [number, number | null, number | null, number | null, number][]
  /** 逐次明细 [索引, 时间, 本趟收益, 增益文字, 扣费] */
  recent: [number, string, number | null, string, number][]
  today_n: number
  today_net: number
  date: string
  dates: string[]
  date_n: Record<string, number>
  today: string
  yesterday: string
  all_n: number
  updated: string
  /** 护理阈值（画那条红虚线用；两个值可能不同） */
  care_energy: number | null
  care_clean: number | null
}

export const fetchAdventure = (date?: string): Promise<AdvData> =>
  getJSON<AdvData>(`/api/adventure${date ? `?date=${encodeURIComponent(date)}` : ''}`)

