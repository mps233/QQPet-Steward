/**
 * 设置页的字段定义（数据驱动）。
 *
 * 字段名与 `dashboard.py` 的 `apply_settings` 映射表**逐字对应** —— 前端只发
 * `{字段名: 值}`，后端负责校验并写回 config.yaml（ruamel 往返，保留注释）。
 * 所以这里不需要知道配置键（`tasks.school.enabled` 那些），只需知道字段名与控件类型。
 *
 * 与 旧版 的差别：那边把 62 个字段的 HTML 全渲染进 `#setForm` 再用 `.hide`
 * 切换分组；这边**只渲染当前组**，省掉一半 DOM。
 */

/** `secret` = 凭据类输入（Token / API Key）：
 *  **只写不回显** —— 后端 `editable_snapshot()` 只下发 `<key>_set`（bool），原值永不下发，
 *  所以前端拿不到"当前值"，输入框始终是空的，留空 = 保持不变。渲染见 `SettingsPage`
 *  的 `SecretControl`（占位提示 / 清除按钮 / 失焦保存）。 */
export type FieldKind = 'bool' | 'int' | 'text' | 'select' | 'secret'

export interface FieldDef {
  key: string
  label: string
  kind: FieldKind
  /** select 的选项；也支持用 `'@work_locations'` 这种从 editable 里取选项的写法 */
  opts?: string[]
  /** select 的显示文案（值 → 界面上的中文），不写就直接显示值 */
  optLabels?: Record<string, string>
  /** 依赖另一个 bool 字段：它为 false 时本项**置灰不可选**
   *  （如"等他空闲时"依赖"等TA空闲"——开关没开就没什么可等的） */
  enabledWhen?: string
  tip?: string
  placeholder?: string
  /** 输入框宽度（u 单位，1u ≈ 视口宽/360）；不给则自适应 */
  w?: number
  unit?: string
  /** 这一行**整行块级**（标签在上一行、输入框占满下一行）。
   *  给长 URL / 长 Token 这类"一行塞不进 label 右边"的字段用：
   *  `.form input[type=text]` 被 `max-width:58%` 和窄屏的 `52%` 两重夹着，
   *  加上 .ctrl 与标签分掉的一半，实测 390 宽下只剩 81px，"https://…" 都显示不全。 */
  wide?: boolean
}

export interface GroupDef {
  id: string
  title: string
  fields: FieldDef[]
  /** 卡片下方的说明条目（渲染成 `.noterow` 的两列小字），用于"这一组到底是干什么的" */
  notes?: { t: string; b: string }[]
}

/** 学习科目（后端 validate_field 的白名单） */
const SCHOOL_ATTRS = ['力量', '智力', '魅力', '夏令营']
/** 课时档位：短课/长课（具体分钟数按学园卡位定） */
const SCHOOL_DURATIONS = ['10分钟', '30分钟']
const WORK_DURATIONS = ['10分钟', '45分钟', '2小时']
const CARE_METHODS = ['ocr检测', '一键护理']
const EMPLOYED_ACTIONS = ['等到25/75（小于45min）', '等到25/75', '立刻召回', '让利雇主（不召回）']
const ADVENTURE_TYPES = ['附近走走', '诗和远方']
/** 「等他空闲」时干什么（work.hire_wait_action，仅 hire_wait 开启时可选） */
const HIRE_WAIT_ACTIONS = ['不做任何事情', '去冒险', '继续打工']

/** 二级分组：id 必须与 MENU 里的 key 一致（也是 `?grp=<id>` 直开用的值） */
export const GROUPS: Record<string, GroupDef> = {
  school: {
    id: 'school',
    title: '学习',
    fields: [
      { key: 'school_enabled', label: '启用学习', kind: 'bool' },
      { key: 'school_attribute', label: '学习科目', kind: 'select', opts: SCHOOL_ATTRS },
      { key: 'school_times', label: '每天学习次数', kind: 'int', tip: '0 = 不限', w: 60 },
      { key: 'school_duration', label: '课时档位', kind: 'select', opts: SCHOOL_DURATIONS, tip: '短课单位收益更高' },
    ],
  },
  work: {
    id: 'work',
    title: '打工',
    fields: [
      { key: 'work_enabled', label: '启用打工', kind: 'bool' },
      { key: 'work_location', label: '打工地点', kind: 'select', opts: ['@work_locations'] },
      { key: 'work_duration', label: '打工时长', kind: 'select', opts: WORK_DURATIONS },
      { key: 'hire_name', label: '优先雇佣', kind: 'text', placeholder: '好友的宠物名或主人名（留空 = 面板自动挑收益最高的）', tip: '配了就先顺路去他家雇：雇到直接开工；他还在 CD、或不在好友轮播里，就照常去打工地点用面板挑人（不空等）' },
      { key: 'hire_wait', label: '等TA空闲', kind: 'bool', tip: '开 = 好友正忙（出门中/被雇佣中/还在雇佣 CD 里）时等他空下来再雇，而不是跳过。关掉 = 直接换收益最高的人' },
      { key: 'hire_wait_action', label: '等他空闲时', kind: 'select', opts: HIRE_WAIT_ACTIONS, enabledWhen: 'hire_wait', tip: '他正忙 / 还在雇佣 CD 里的这段时间做什么：不做任何事情 = 原地等；去冒险 = 先让出调度器去做别的，到点再回来雇；继续打工 = 不等了，直接去打工地点用面板挑人' },
      { key: 'hire_daily_hours', label: '每人每天被雇上限', kind: 'int', unit: '小时', tip: '游戏机制：每个好友每天最多被雇这么久，满了就不再雇他（再雇没收益）；0 = 不限制', w: 56 },
      { key: 'claim_ad_bonus', label: '结算领补贴', kind: 'bool', tip: '每次打工（含雇佣好友打工）结束时，在结算页顺手领「看视频 +N」金币补贴' },
    ],
  },
  quota: {
    id: 'quota',
    title: '学习/打工 配额',
    fields: [
      { key: 'study_quota_hours', label: '今日学习', kind: 'int', unit: '小时', tip: '0 = 今天不学；24 ≈ 不限', w: 56 },
      { key: 'work_quota_hours', label: '今日打工', kind: 'int', unit: '小时', tip: '0 = 今天不打工；24 ≈ 不限', w: 56 },
      { key: 'coin_threshold', label: '金币阈值', kind: 'int', tip: '金币 >= 该值时优先学习，低于则先打工', w: 80 },
    ],
  },
  fatigue: {
    id: 'fatigue',
    title: '合计停止点与收益档',
    fields: [
      { key: 'stop_total_hours', label: '合计满则停止', kind: 'int', unit: '小时', tip: '学习+打工合计满这个小时数就两项都停（转冒险）。0 = 不限', w: 56 },
      { key: 'daily_hour_limit', label: '停学习点', kind: 'int', unit: '小时', tip: '合计满这么多小时后今天不再学习、只打工（手改 config.yaml 才生效于此项，界面默认跟合计同值）', w: 56 },
      { key: 'work_stop_hours', label: '停打工点', kind: 'int', unit: '小时', tip: '合计满这么多小时后今天连打工也停', w: 56 },
      { key: 'main_order', label: '主任务优先级', kind: 'text', placeholder: 'school>work>adventure', tip: '> 分隔，越靠前越优先。三个主任务：学习/打工/冒险（任务列表也可以直接拖动排序；雇好友已并入打工，不再是独立主任务）' },
    ],
  },
  care: {
    id: 'care',
    title: '护理',
    fields: [
      { key: 'care_energy', label: '体力阈值', kind: 'int', tip: '低于该值就喂食', w: 56 },
      { key: 'care_clean', label: '清洁阈值', kind: 'int', tip: '低于该值就洗澡', w: 56 },
      { key: 'care_method', label: '护理方式', kind: 'select', opts: CARE_METHODS, tip: '要拿护理勋章必须用「ocr检测」——一键护理不计入勋章进度' },
      { key: 'care_exchange', label: '补货数量', kind: 'int', unit: '个', tip: '金币补货时一次买多少个（兑换食物/购买洗澡道具共用）', w: 56 },
      { key: 'care_interval', label: '检查间隔', kind: 'int', unit: '秒', w: 56 },
    ],
  },
  friend_care: {
    id: 'friend_care',
    title: '好友护理',
    fields: [
      { key: 'friend_care_enabled', label: '启用', kind: 'bool', tip: '好友护理已并入「护理」任务：护理每轮检查完自己之后，到点就顺路去好友家护理一次。这个开关就是它的单独开关（关掉只做自己的护理）' },
      { key: 'friend_care_name', label: '好友', kind: 'text', placeholder: '宠物名或主人名', tip: '留空则不会做，需要填一个好友' },
      { key: 'friend_care_range', label: '时间段', kind: 'text', placeholder: '00:00-00:00', tip: 'HH:MM-HH:MM；起止相同 = 全天' },
      { key: 'friend_care_interval', label: '间隔', kind: 'int', unit: '秒', w: 56 },
      { key: 'friend_care_method', label: '方式', kind: 'select', opts: CARE_METHODS },
    ],
  },
  visit: {
    id: 'visit',
    title: '踩踩',
    fields: [
      { key: 'visit_enabled', label: '启用', kind: 'bool' },
      { key: 'visit_times', label: '每天次数', kind: 'int', w: 56 },
    ],
  },
  pk: {
    id: 'pk',
    title: 'PK',
    fields: [
      { key: 'pk_enabled', label: '启用', kind: 'bool' },
      { key: 'pk_times', label: '每天次数', kind: 'int', w: 56 },
      { key: 'pk_only', label: '只打', kind: 'text', placeholder: '宠物名/主人名，逗号分隔', tip: '留空 = 不限制；填了就只打名单里的' },
      { key: 'pk_skip', label: '跳过', kind: 'text', placeholder: '宠物名/主人名，逗号分隔' },
      { key: 'pk_helper', label: '打手', kind: 'text', placeholder: '宠物名/主人名，逗号分隔', tip: '不在名单里的会解雇、空位从宠友列表雇第一个可雇的' },
      { key: 'pk_helper_fallback', label: '打手兜底', kind: 'bool', tip: '开 = 名单都不可雇时自动雇战力最高的' },
      { key: 'pk_max_level', label: '等级上限', kind: 'int', tip: '-1 = 只打比自己低的；-2 = 只打比打手低的；0 = 不限', w: 56 },
    ],
  },
  adventure: {
    id: 'adventure',
    title: '冒险',
    fields: [
      { key: 'adventure_enabled', label: '启用', kind: 'bool' },
      { key: 'adventure_times', label: '次数/天', kind: 'int', tip: '0 = 不冒险；主号策略设 999 ≈ 不限', w: 64 },
      { key: 'adventure_type', label: '冒险类型', kind: 'select', opts: ADVENTURE_TYPES, tip: '附近走走约 45 秒（靠连跑刷次数）/ 诗和远方约 2 小时' },
    ],
  },
  employed: {
    id: 'employed',
    title: '被雇佣',
    fields: [
      { key: 'employed_enabled', label: '被雇佣托管', kind: 'bool' },
      { key: 'employed_action', label: '处理方式', kind: 'select', opts: EMPLOYED_ACTIONS },
      { key: 'employed_interval', label: '检查间隔', kind: 'int', unit: '秒', w: 56 },
    ],
  },
  gift_bag: {
    id: 'gift_bag',
    title: '福袋',
    fields: [
      { key: 'gift_bag_enabled', label: '启用', kind: 'bool' },
      { key: 'gift_bag_range', label: '时间段', kind: 'text', placeholder: '00:00-00:00' },
      { key: 'gift_bag_interval', label: '扫描间隔', kind: 'int', unit: '秒', w: 56 },
    ],
  },
  career: {
    id: 'career',
    title: '职业',
    fields: [
      { key: 'career_watch', label: '隐藏职业解锁监控', kind: 'bool', tip: '每节课结算后读职业树，检测到隐藏线解锁就记录并发通知' },
      { key: 'career_stop_study', label: '解锁后自动停学', kind: 'bool' },
      { key: 'career_interval', label: '兜底检查间隔', kind: 'int', unit: '分', w: 56 },
    ],
  },
  schedule: {
    id: 'schedule',
    title: '调度',
    fields: [
      { key: 'adventure_times', label: '冒险次数/天', kind: 'int', tip: '与「冒险」页同一项', w: 64 },
      { key: 'care_interval', label: '护理检查间隔', kind: 'int', unit: '秒', tip: '与「护理」页同一项', w: 56 },
    ],
  },
  adb: {
    id: 'adb',
    title: '连接手机（ADB）',
    fields: [
      { key: 'adb_path', label: 'adb 路径', kind: 'text', placeholder: '留空自动探测', tip: '改完需重启调度器才生效' },
      { key: 'adb_serial', label: '设备序列号', kind: 'text', placeholder: 'adb devices 查看' },
    ],
  },
  /** 访问码页没有普通字段：表单在 SettingsPage 里单独渲染（要换发 Cookie、二次确认），
   *  这里注册 id/标题，好让它照常出现在一级菜单与 `?grp=access` 直达。 */
  access: {
    id: 'access',
    title: '仪表盘访问码',
    fields: [],
  },
  /** TG 机器人：Telegram 控制机器人（远程发指令改配置 / 看状态）。
   *  字段名逐字对齐 `dashboard.py` 的 `apply_settings` 映射；两个凭据（Bot Token /
   *  LLM API Key）用 `kind: 'secret'`，回填只拿得到 `*_set` 的布尔值。 */
  tgbot: {
    id: 'tgbot',
    title: 'TG 机器人',
    fields: [
      { key: 'tgbot_enabled', label: '启用控制机器人', kind: 'bool', tip: '开 = 让 Telegram 机器人收指令；关 = 完全不响应消息（通知推送不受影响，那是「通知」页的事）' },
      { key: 'tgbot_token', label: 'Bot Token', kind: 'secret', tip: '跟 @BotFather 发 /newbot 拿到的 Token（形如 123456789:AAE…）。只写入、不回显：留空保持不变，输入新值替换，点「清除已保存凭据」明确删除' },
      { key: 'tgbot_allowed_chats', label: '允许操作的 chat id', kind: 'text', wide: true, placeholder: '逗号分隔，留空 = 只认通知里的 telegram_chat_id', tip: '只有这些 chat id 能发指令改配置，其它人发来一律忽略；留空则只认「通知」里配的 telegram_chat_id。多个用逗号分隔（群/频道是 -100 开头的负数）' },
      { key: 'tgbot_llm_enabled', label: '自然语言解析', kind: 'bool', tip: '开 = 你说的话交给模型解析成要改的配置项（能听懂"把护理阈值调到 50"这种）；关 = 纯指令模式，只认固定命令' },
      { key: 'tgbot_llm_base_url', label: 'LLM base_url', kind: 'text', wide: true, enabledWhen: 'tgbot_llm_enabled', placeholder: 'https://api.deepseek.com/v1', tip: 'OpenAI 兼容接口的 base_url，末尾要到 /v1 这一层（例：https://api.deepseek.com/v1）' },
      { key: 'tgbot_llm_model', label: 'LLM 模型', kind: 'text', wide: true, enabledWhen: 'tgbot_llm_enabled', placeholder: 'deepseek-chat', tip: '模型名，要和上面的 base_url 属于同一家（例：deepseek-chat、gpt-4o-mini）' },
      { key: 'tgbot_llm_api_key', label: 'LLM API Key', kind: 'secret', enabledWhen: 'tgbot_llm_enabled', tip: '上面那家服务的 API Key。只写入、不回显：留空保持不变，输入新值替换，点「清除已保存凭据」明确删除' },
    ],
    notes: [
      { t: '谁能用', b: '只有「允许操作的 chat id」里的会话能改配置；留空时只认「通知 → Telegram Bot」里配的 Chat ID，等于"只有我自己那个会话"。' },
      { t: '凭据', b: 'Bot Token 与 API Key 仅写入、不回传原值。留空保持不变，要删除就点「清除已保存凭据」。' },
      { t: '模型', b: '关掉「自然语言解析」就是纯指令模式，不需要填 base_url / 模型 / 密钥。' },
    ],
  },
}

/** 一级菜单：4 个分区，共 16 个二级页（与 GROUPS 一一对应） */
export const MENU: { section: string; items: { id: string; title: string }[] }[] = [
  {
    section: '核心任务',
    items: [
      { id: 'school', title: '学习' },
      { id: 'work', title: '打工' },
      { id: 'quota', title: '学习/打工 配额' },
      { id: 'fatigue', title: '合计停止点与收益档' },
    ],
  },
  {
    section: '日常互动',
    items: [
      { id: 'care', title: '护理' },
      { id: 'friend_care', title: '好友护理' },
      { id: 'visit', title: '踩踩' },
      { id: 'pk', title: 'PK' },
      { id: 'adventure', title: '冒险' },
    ],
  },
  {
    section: '扩展',
    items: [
      { id: 'employed', title: '被雇佣' },
      { id: 'gift_bag', title: '福袋' },
      { id: 'career', title: '职业' },
    ],
  },
  {
    section: '系统',
    items: [
      { id: 'schedule', title: '调度' },
      { id: 'adb', title: '连接手机（ADB）' },
      { id: 'tgbot', title: 'TG 机器人' },
      { id: 'access', title: '仪表盘访问码' },
    ],
  },
]

/** 展开 `'@work_locations'` 这类"选项来自后端"的写法 */
export function resolveOpts(opts: string[] | undefined, editable: Record<string, unknown>): string[] {
  if (!opts) return []
  if (opts.length === 1 && opts[0]?.startsWith('@')) {
    const v = editable[opts[0].slice(1)]
    return Array.isArray(v) ? v.map(String) : []
  }
  return opts
}
