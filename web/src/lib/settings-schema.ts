/**
 * 设置页的字段定义（数据驱动）。
 *
 * 字段名与 `dashboard.py` 的 `apply_settings` 映射表**逐字对应** —— 前端只发
 * `{字段名: 值}`，后端负责校验并写回 config.yaml（ruamel 往返，保留注释）。
 * 所以这里不需要知道配置键（`tasks.school.enabled` 那些），只需知道字段名与控件类型。
 *
 * 结构：一级菜单（MENU）→ 二级分组（GROUPS）→ 分组内若干节（sections）。
 * 分节不是为了好看：设置项合并成 8 页后，一页里 8~11 项必须按"学习 / 职业监控"
 * 这种语义再分一层，否则又是一堵墙。
 */

/** `secret` = 凭据类输入（Token / API Key）：
 *  **只写不回显** —— 后端 `editable_snapshot()` 只下发 `<key>_set`（bool），原值永不下发，
 *  所以前端拿不到"当前值"，输入框始终是空的，留空 = 保持不变。 */
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
  /** 行内说明。手机上没有 hover，**所有提示都必须显示出来**，不能只写 title。 */
  tip?: string
  placeholder?: string
  unit?: string
}

export interface SectionDef {
  title: string
  fields: FieldDef[]
  /** 只读的附加块：收益档说明表 / TG 运行状态 */
  extra?: 'efficiency' | 'tgbot-status'
  /** 卡片底部的两列小字说明 */
  notes?: { t: string; b: string }[]
}

export interface GroupDef {
  id: string
  title: string
  /** 一级菜单里的一行说明（合并后光看标题不知道里面有什么） */
  desc: string
  sections: SectionDef[]
}

/** 学习科目（后端 validate_field 的白名单） */
const SCHOOL_ATTRS = ['力量', '智力', '魅力', '夏令营']
/** 课时档位：短课/长课（**按课程轮播卡位选，不绑定具体分钟数**）。
 *
 *  ⚠ 这里曾经写的是 `['10分钟', '30分钟']` —— 那是 2026-09-14（da8b97f）
 *  改按卡位选课之前的旧语义。后端 `course_kind()` 对旧值的兼容规则是
 *  「10分钟→短课，**其余任何分钟数→长课**」，于是界面上选「30分钟」实际选的是长课。
 *  各学园实际时长不同（初级 10/20、中级 20、高级 30、进修 45/135），
 *  所以选项必须是档位名本身，不能是分钟数。 */
const SCHOOL_DURATIONS = ['短课', '长课']
/** 档位 → 界面文案：说明各自对应轮播里的哪几张卡 */
const SCHOOL_DURATION_LABELS: Record<string, string> = {
  短课: '短课（卡 1-3）',
  长课: '长课（卡 4-6）',
}
const WORK_DURATIONS = ['10分钟', '45分钟', '2小时']
const CARE_METHODS = ['ocr检测', '一键护理']
const EMPLOYED_ACTIONS = ['等到25/75（小于45min）', '等到25/75', '立刻召回', '让利雇主（不召回）']
const ADVENTURE_TYPES = ['附近走走', '诗和远方']
/** 「等他空闲」时干什么（work.hire_wait_action，仅 hire_wait 开启时可选） */
const HIRE_WAIT_ACTIONS = ['不做任何事情', '去冒险', '继续打工']

/** 二级分组：id 是 `?grp=<id>` 直开用的值（旧的单页 id 有别名，见 SettingsPage） */
export const GROUPS: Record<string, GroupDef> = {
  school: {
    id: 'school',
    title: '学习与职业',
    desc: '科目、课时档位、隐藏职业解锁监控',
    sections: [
      {
        title: '学习',
        fields: [
          { key: 'school_enabled', label: '启用学习', kind: 'bool' },
          { key: 'school_attribute', label: '学习科目', kind: 'select', opts: SCHOOL_ATTRS },
          { key: 'school_times', label: '每天学习次数', kind: 'int', tip: '0 = 不限', unit: '次' },
          { key: 'school_duration', label: '课时档位', kind: 'select', opts: SCHOOL_DURATIONS, optLabels: SCHOOL_DURATION_LABELS, tip: '短课单位收益更高。各学园的分钟数不同（进修学院实测短课 45 分钟 / 长课 135 分钟），实际时长选课后从详情面板读取并据此结算' },
        ],
      },
      {
        title: '职业解锁监控',
        fields: [
          { key: 'career_watch', label: '监控隐藏职业', kind: 'bool', tip: '每节课结算后读职业树，检测到隐藏线解锁就记录并发通知' },
          { key: 'career_stop_study', label: '解锁后自动停学', kind: 'bool', tip: '隐藏职业解锁后停止学习任务，避免继续占用时间' },
          { key: 'career_interval', label: '兜底检查间隔', kind: 'int', unit: '分', tip: '没有课程结算时，也按这个间隔检查一次职业树' },
        ],
      },
    ],
  },
  work: {
    id: 'work',
    title: '打工与雇佣',
    desc: '打工地点、雇佣好友、被雇佣托管',
    sections: [
      {
        title: '打工',
        fields: [
          { key: 'work_enabled', label: '启用打工', kind: 'bool' },
          { key: 'work_location', label: '打工地点', kind: 'select', opts: ['@work_locations'] },
          { key: 'work_duration', label: '打工时长', kind: 'select', opts: WORK_DURATIONS },
          { key: 'claim_ad_bonus', label: '结算领补贴', kind: 'bool', tip: '每次打工（含雇佣好友打工）结束时，在结算页顺手领「看视频 +N」金币补贴' },
        ],
      },
      {
        title: '雇佣好友',
        fields: [
          { key: 'hire_name', label: '优先雇佣', kind: 'text', placeholder: '好友的宠物名或主人名（留空 = 面板自动挑收益最高的）', tip: '配了就先顺路去他家雇：雇到直接开工；他还在 CD、或不在好友轮播里，就照常去打工地点用面板挑人（不空等）' },
          { key: 'hire_wait', label: '等TA空闲', kind: 'bool', tip: '开 = 好友正忙（出门中/被雇佣中/还在雇佣 CD 里）时等他空下来再雇，而不是跳过。关掉 = 直接换收益最高的人' },
          { key: 'hire_wait_action', label: '等他空闲时', kind: 'select', opts: HIRE_WAIT_ACTIONS, enabledWhen: 'hire_wait', tip: '他正忙 / 还在雇佣 CD 里的这段时间做什么：不做任何事情 = 原地等；去冒险 = 先让出调度器去做别的，到点再回来雇；继续打工 = 不等了，直接去打工地点用面板挑人' },
          { key: 'hire_daily_hours', label: '每人每天被雇上限', kind: 'int', unit: '小时', tip: '游戏机制：每个好友每天最多被雇这么久，满了就不再雇他（再雇没收益）；0 = 不限制' },
        ],
      },
      {
        title: '被雇佣托管',
        fields: [
          { key: 'employed_enabled', label: '启用被雇佣托管', kind: 'bool', tip: '在下面的时间段内按间隔出门检查；被雇佣后按处理方式决定什么时候召回' },
          { key: 'employed_action', label: '处理方式', kind: 'select', opts: EMPLOYED_ACTIONS },
          { key: 'employed_interval', label: '检查间隔', kind: 'int', unit: '秒' },
        ],
      },
    ],
  },
  limits: {
    id: 'limits',
    title: '限额与冒险',
    desc: '每日配额、合计停止点、冒险类型',
    sections: [
      {
        title: '冒险',
        fields: [
          { key: 'adventure_enabled', label: '启用冒险', kind: 'bool' },
          { key: 'adventure_times', label: '次数/天', kind: 'int', tip: '0 = 不冒险；主号策略设 999 ≈ 不限', unit: '次' },
          { key: 'adventure_type', label: '冒险类型', kind: 'select', opts: ADVENTURE_TYPES, tip: '附近走走约 45 秒（靠连跑刷次数）/ 诗和远方约 2 小时' },
        ],
      },
      {
        title: '今日配额',
        fields: [
          { key: 'study_quota_hours', label: '今日学习', kind: 'int', unit: '小时', tip: '0 = 今天不学；24 ≈ 不限' },
          { key: 'work_quota_hours', label: '今日打工', kind: 'int', unit: '小时', tip: '0 = 今天不打工；24 ≈ 不限' },
          { key: 'coin_threshold', label: '金币阈值', kind: 'int', unit: '金币', tip: '金币 >= 该值时优先学习，低于则先打工' },
        ],
      },
      {
        title: '停止点',
        fields: [
          { key: 'stop_total_hours', label: '合计满则停止', kind: 'int', unit: '小时', tip: '学习+打工合计满这个小时数就两项都停（转冒险）。0 = 不限' },
        ],
        extra: 'efficiency',
      },
      {
        title: '主任务优先级',
        fields: [
          { key: 'main_order', label: '优先级', kind: 'text', placeholder: 'school>work>adventure', tip: '用 > 分隔，越靠前越优先。三个主任务：学习/打工/冒险（任务列表也可以直接拖动排序；雇好友已并入打工，不再是独立主任务）' },
        ],
      },
    ],
  },
  care: {
    id: 'care',
    title: '护理',
    desc: '体力清洁阈值、护理方式、好友护理',
    sections: [
      {
        title: '自己',
        fields: [
          { key: 'care_enabled', label: '启用护理', kind: 'bool' },
          { key: 'care_energy', label: '体力阈值', kind: 'int', unit: '点', tip: '低于该值就喂食' },
          { key: 'care_clean', label: '清洁阈值', kind: 'int', unit: '点', tip: '低于该值就洗澡' },
          { key: 'care_method', label: '护理方式', kind: 'select', opts: CARE_METHODS, tip: '要拿护理勋章必须用「ocr检测」——一键护理不计入勋章进度' },
          { key: 'care_exchange', label: '补货数量', kind: 'int', unit: '个', tip: '金币补货时一次买多少个（兑换食物/购买洗澡道具共用）' },
          { key: 'care_interval', label: '检查间隔', kind: 'int', unit: '秒' },
        ],
      },
      {
        title: '好友护理',
        fields: [
          { key: 'friend_care_enabled', label: '启用好友护理', kind: 'bool', tip: '好友护理已并入「护理」任务：护理每轮检查完自己之后，到点就顺路去好友家护理一次。关掉只做自己的护理' },
          { key: 'friend_care_name', label: '好友', kind: 'text', placeholder: '宠物名或主人名', tip: '留空则不会做，需要填一个好友' },
          { key: 'friend_care_range', label: '时间段', kind: 'text', placeholder: '00:00-00:00', tip: 'HH:MM-HH:MM；起止相同 = 全天' },
          { key: 'friend_care_interval', label: '间隔', kind: 'int', unit: '秒' },
          { key: 'friend_care_method', label: '方式', kind: 'select', opts: CARE_METHODS },
        ],
      },
    ],
  },
  social: {
    id: 'social',
    title: '好友互动',
    desc: '踩踩、PK、福袋',
    sections: [
      {
        title: '踩踩',
        fields: [
          { key: 'visit_enabled', label: '启用踩踩', kind: 'bool' },
          { key: 'visit_times', label: '每天次数', kind: 'int', unit: '次' },
        ],
      },
      {
        title: 'PK',
        fields: [
          { key: 'pk_enabled', label: '启用 PK', kind: 'bool' },
          { key: 'pk_times', label: '每天次数', kind: 'int', unit: '次' },
          { key: 'pk_only', label: '只打', kind: 'text', placeholder: '宠物名/主人名，逗号分隔', tip: '留空 = 不限制；填了就只打名单里的' },
          { key: 'pk_skip', label: '跳过', kind: 'text', placeholder: '宠物名/主人名，逗号分隔' },
          { key: 'pk_helper', label: '打手', kind: 'text', placeholder: '宠物名/主人名，逗号分隔', tip: '不在名单里的会解雇、空位从宠友列表雇第一个可雇的' },
          { key: 'pk_helper_fallback', label: '打手兜底', kind: 'bool', tip: '开 = 名单都不可雇时自动雇战力最高的' },
          { key: 'pk_max_level', label: '等级上限', kind: 'int', tip: '-1 = 只打比自己低的；-2 = 只打比打手低的；0 = 不限' },
        ],
      },
      {
        title: '福袋',
        fields: [
          { key: 'gift_bag_enabled', label: '启用福袋', kind: 'bool', tip: '自己家和好友家的福袋都会扫；有可领的才进好友家' },
          { key: 'gift_bag_range', label: '时间段', kind: 'text', placeholder: '00:00-00:00', tip: 'HH:MM-HH:MM；起止相同 = 全天' },
          { key: 'gift_bag_interval', label: '扫描间隔', kind: 'int', unit: '秒', tip: '福袋 24 小时不领会过期，建议保持默认的半小时' },
        ],
      },
    ],
  },
  system: {
    id: 'system',
    title: '连接与机器人',
    desc: 'ADB 连接、TG 控制机器人',
    sections: [
      {
        title: '手机连接（ADB）',
        fields: [
          { key: 'adb_path', label: 'adb 路径', kind: 'text', placeholder: '留空自动探测', tip: '改完需重启调度器才生效' },
          { key: 'adb_serial', label: '设备序列号', kind: 'text', placeholder: 'adb devices 查看' },
        ],
        notes: [
          { t: '生效', b: 'adb 路径与序列号属于连接层，改完需要重启调度器；其它设置下一轮调度自动生效。' },
        ],
      },
      {
        title: 'TG 控制机器人',
        fields: [
          { key: 'tgbot_enabled', label: '启用控制机器人', kind: 'bool', tip: '开 = 让 Telegram 机器人收指令；关 = 完全不响应消息（通知推送不受影响，那是「通知」页的事）' },
          { key: 'tgbot_token', label: 'Bot Token', kind: 'secret', tip: '跟 @BotFather 发 /newbot 拿到的 Token（形如 123456789:AAE…）。只写入、不回显：留空保持不变，输入新值替换，点「清除凭据」明确删除' },
          { key: 'tgbot_allowed_chats', label: '允许操作的 chat id', kind: 'text', placeholder: '逗号分隔，留空 = 只认通知里的 telegram_chat_id', tip: '只有这些 chat id 能发指令改配置，其它人发来一律忽略；留空则只认「通知」里配的 telegram_chat_id。多个用逗号分隔（群/频道是 -100 开头的负数）' },
          { key: 'tgbot_llm_enabled', label: '自然语言解析', kind: 'bool', tip: '开 = 你说的话交给模型解析成要改的配置项（能听懂"把护理阈值调到 50"这种）；关 = 纯指令模式，只认固定命令' },
          { key: 'tgbot_llm_base_url', label: 'LLM base_url', kind: 'text', enabledWhen: 'tgbot_llm_enabled', placeholder: 'https://api.deepseek.com/v1', tip: 'OpenAI 兼容接口的 base_url，末尾要到 /v1 这一层（例：https://api.deepseek.com/v1）' },
          { key: 'tgbot_llm_model', label: 'LLM 模型', kind: 'text', enabledWhen: 'tgbot_llm_enabled', placeholder: 'deepseek-chat', tip: '模型名，要和上面的 base_url 属于同一家（例：deepseek-chat、gpt-4o-mini）' },
          { key: 'tgbot_llm_api_key', label: 'LLM API Key', kind: 'secret', enabledWhen: 'tgbot_llm_enabled', tip: '上面那家服务的 API Key。只写入、不回显：留空保持不变，输入新值替换，点「清除凭据」明确删除' },
        ],
        extra: 'tgbot-status',
        notes: [
          { t: '谁能用', b: '只有「允许操作的 chat id」里的会话能改配置；留空时只认「通知 → Telegram Bot」里配的 Chat ID，等于"只有我自己那个会话"。' },
          { t: '凭据', b: 'Bot Token 与 API Key 仅写入、不回传原值。留空保持不变，要删除就点「清除凭据」。' },
          { t: '模型', b: '关掉「自然语言解析」就是纯指令模式，不需要填 base_url / 模型 / 密钥。' },
        ],
      },
    ],
  },
  /** 访问码页没有普通字段：表单在 SettingsPage 里单独渲染（要换发 Cookie、二次确认），
   *  这里注册 id/标题，好让它照常出现在一级菜单与 `?grp=access` 直达。 */
  access: {
    id: 'access',
    title: '仪表盘访问码',
    desc: '更换访问码、管理登录会话',
    sections: [],
  },
  /** Remote pairing is rendered as a bespoke panel, not schema fields. */
  remote: {
    id: 'remote',
    title: '手机遥控',
    desc: '配对手机、查看和撤销设备',
    sections: [],
  },
}

/** 一级菜单：3 个分区，共 8 个二级页（与 GROUPS 一一对应，新增手机遥控）。
 *  合并原则：同一件事的两半放一页（好友护理 → 护理、被雇佣 → 打工、职业 → 学习、
 *  福袋/踩踩/PK → 好友互动），不再为 2~3 个字段单开一页；原来的「调度」页整个删掉，
 *  它只有冒险次数与护理间隔两个重复字段。 */
export const MENU: { section: string; items: { id: string; title: string }[] }[] = [
  {
    section: '核心任务',
    items: [
      { id: 'school', title: '学习与职业' },
      { id: 'work', title: '打工与雇佣' },
      { id: 'limits', title: '限额与冒险' },
    ],
  },
  {
    section: '日常互动',
    items: [
      { id: 'care', title: '护理' },
      { id: 'social', title: '好友互动' },
    ],
  },
  {
    section: '系统',
    items: [
      { id: 'system', title: '连接与机器人' },
      { id: 'remote', title: '手机遥控' },
      { id: 'access', title: '仪表盘访问码' },
    ],
  },
]

/** 旧的二级页 id → 新分组：用户可能存过 `?tab=set&grp=quota` 这种直链。
 *  `schedule` 页已删除，落到限额页（那两个字段现在在「护理」「限额与冒险」里）。 */
export const LEGACY_GROUP_ALIASES: Record<string, string> = {
  quota: 'limits',
  fatigue: 'limits',
  schedule: 'limits',
  friend_care: 'care',
  visit: 'social',
  pk: 'social',
  gift_bag: 'social',
  employed: 'work',
  career: 'school',
  adb: 'system',
  tgbot: 'system',
}

/** 展开 `'@work_locations'` 这类"选项来自后端"的写法 */
export function resolveOpts(opts: string[] | undefined, editable: Record<string, unknown>): string[] {
  if (!opts) return []
  if (opts.length === 1 && opts[0]?.startsWith('@')) {
    const v = editable[opts[0].slice(1)]
    return Array.isArray(v) ? v.map(String) : []
  }
  return opts
}
