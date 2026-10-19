/**
 * 职业树静态数据：8 条职业线 × 5 个层级 = 40 格。
 *
 * **全部文案来自真机实测**（见 `docs/CAREER-TREE-OFFICIAL.md` 第 3.3 节）：
 * 2026-10-19 在一加 9 Pro（1080×2412）上逐格点开弹窗、dump content-desc 抓的原文，
 * 不是编的。改文案请先回去对那台机器，别在这里顺手"润色"。
 *
 * 三个层级维度要分清：
 *  - `tier`   0..4 = 见习/初级/中级/高级/大师（矩阵的行）
 *  - `line`   0..7 = 职业线（矩阵的列）
 *  - `state`  placeholder / locked / unlocked / equipped（单元格四态，运行时合成）
 */

/** 五级（行）。`label` 是矩阵左侧那列层级文字。 */
export const TIERS = ['见习', '初级', '中级', '高级', '大师'] as const
export type TierName = (typeof TIERS)[number]

/** 每条线的短名（矩阵列头 / 弹窗标题用）。 */
export const LINES = [
  '流浪散人',
  '画家',
  '侦探',
  '大魔法师',
  '大厨',
  '武术家',
  '梦境旅人',
  '大明星',
] as const
export type LineName = (typeof LINES)[number]

/**
 * 后端 `career_unlock.json` 的 `lines` 键名与前端短名不完全一致：
 * 哨兵写的是「法师」，而官方页面列头是「大魔法师」。
 * **这个映射是唯一桥接点**，加线时两处一起改。
 */
export const LINE_ALIAS: Record<string, LineName> = {
  流浪散人: '流浪散人',
  画家: '画家',
  侦探: '侦探',
  法师: '大魔法师',
  大魔法师: '大魔法师',
  大厨: '大厨',
  武术家: '武术家',
  梦境旅人: '梦境旅人',
  大明星: '大明星',
}

/**
 * 一格的定义（静态部分）。
 *
 * 三种"没有内容"必须分开，它们在页面上长得完全不同（实测见规格文档第 7 节）：
 *  - `empty` 与 `placeholder` 的区别：**empty 连格子都不画**，只有列内那条竖线穿过去；
 *    placeholder 画一个只有描边的六边形 + 大「?」+「???」名字牌。
 *  - 流浪散人就是 `[见习, empty, empty, placeholder, placeholder]`
 *    （这行只有见习一级真实存在，初级/中级游戏里根本没格子）。
 */
export interface CareerCellDef {
  /** 职业名，如「素描写手」；占位/空格为 null */
  name: string | null
  /** 该格在页面上是否根本不渲染（连六边形都没有）。默认 false。 */
  empty?: boolean
  /** 星级：见习 2、初级 3、中级 4、高级 5、大师 5（大师未投放，占位） */
  stars: number
  /** 简介原文；占位格为 null */
  desc: string | null
  /**
   * 解锁条件的「需求值」[力, 智, 魅]。
   * - `null` = 该格是占位（游戏里没投放）
   * - `[0,0,0]` = 隐藏线（单属性），实际判定见 `unlockRule`
   * - 具体三元组 = 普通线，三维同时达标
   */
  need: [number, number, number] | null
  /** 隐藏线（武术家/梦境旅人/大明星）：数值是"某一维 ≥ 其余两维和的 2.5 倍" */
  hidden?: 0 | 1 | 2
  /** 升星要求的工分（初级=840，以此类推）；见习是单格 200 */
  points?: { need: number; label: string }
  /** 学园毕业前置（初级/中级/高级各要对应学园毕业） */
  school?: string
  /** 解锁费用（金币）；`null` = 未知（显示 ???） */
  cost: number | null
  /** 奖励区文案 + 标题 */
  reward: { title: string; items: { name: string; qty?: number; kind: RewardKind }[] }
}

/** 奖励图标种类：用具象的小图表示，避免 40 张道具图都去抠（图标只是示意）。 */
export type RewardKind = 'milk' | 'yogurt' | 'bg' | 'cloth'

const MILK = { name: '高钙奶', qty: 5, kind: 'milk' as const }
const MILK10 = { name: '高钙奶', qty: 10, kind: 'milk' as const }
const YOGURT = { name: '咕噜酸奶', qty: 5, kind: 'yogurt' as const }
const BG = { name: '职业背景', kind: 'bg' as const }
const CLOTH = { name: '职业服饰', kind: 'cloth' as const }

/** 见习：条件真实值、单格工分、已解锁奖励高钙奶×5 */
const first = (name: string, desc: string, need: [number, number, number]): CareerCellDef => ({
  name,
  stars: 2,
  desc,
  need,
  points: { need: 200, label: '获得该星级工分' },
  cost: null,
  reward: { title: '已解锁奖励', items: [MILK] },
})

/** 初级：三维门槛 + 工分 840 + 初级学园毕业 + 1000 金币；奖励酸奶×5 + 高钙奶×10 */
const second = (name: string, desc: string, need: [number, number, number]): CareerCellDef => ({
  name,
  stars: 3,
  desc,
  need,
  points: { need: 840, label: '获得上一阶段工分' },
  school: '宠物初级学园毕业',
  cost: 1000,
  reward: { title: '待解锁奖励', items: [YOGURT, MILK10] },
})

/** 中级：需求游戏里没公开（`力量>?`）+ 中级学园毕业；奖励职业背景 */
const third = (name: string, desc: string): CareerCellDef => ({
  name,
  stars: 4,
  desc,
  need: null,
  points: { need: -1, label: '获得上一阶段工分' },
  school: '宠物中级学园毕业',
  cost: null,
  reward: { title: '待解锁奖励', items: [BG] },
})

/** 高级：最后一实级；奖励职业服饰（敬请期待） */
const fourth = (name: string, desc: string): CareerCellDef => ({
  name,
  stars: 5,
  desc,
  need: null,
  points: { need: -1, label: '获得上一阶段工分' },
  school: '宠物高级学园毕业',
  cost: null,
  reward: { title: '奖励（敬请期待）', items: [CLOTH] },
})

/**
 * 大师：游戏里**还没投放**（服务端 level=500 不存在），矩阵里是占位格，
 * 点击弹 toast「该职业还未现世，再观望一下」。
 */
const master = (): CareerCellDef => ({
  name: null,
  stars: 5,
  desc: null,
  need: null,
  points: undefined,
  cost: null,
  reward: { title: '', items: [] },
})

/** 空格：连六边形都不画（流浪散人的初级/中级就是这么没的）。 */
const none = (stars: number): CareerCellDef => ({ ...master(), stars, empty: true })

/** 整张表：`TREE[line][tier]`。 */
export const TREE: CareerCellDef[][] = [
  // 0 流浪散人 —— 只有见习一级真实存在；初级/中级连格子都没有，高级/大师是 ? 占位
  [
    first('小乞丐', '现在还没找到正经方向，日子过得紧巴巴，但也没彻底认输。', [0, 0, 0]),
    none(3),
    none(4),
    { ...master(), stars: 5 },
    master(),
  ],
  // 1 画家
  [
    first('涂鸦小徒', '刚拿起画笔的小家伙，画得还不太稳，但每一笔都藏着新鲜想法。', [7, 3, 11]),
    second('素描写手', '开始学会观察身边的事物，几根简单线条，也能画出一点神气。', [225, 113, 412]),
    third('调色师', '对颜色越来越有感觉，知道什么时候该明亮，什么时候该安静一点。'),
    fourth('妙笔丹青', '笔下的景物渐渐有了灵气，路过的宠物都会忍不住多看几眼。'),
    master(),
  ],
  // 2 侦探
  [
    first('见习侦探', '随身带着小本子，看到什么都想记下来，虽然偶尔会记错重点。', [11, 5, 5]),
    second('线索猎手', '开始懂得从细节里找线索，越是不起眼的地方，越想凑近看看。', [350, 200, 200]),
    third('破案高手', '面对复杂案件也不慌，能把散落的线索一条条串到一起。'),
    fourth('推理大师', '宠物小镇里出了怪事，大家第一个想到的，往往就是它。'),
    master(),
  ],
  // 3 大魔法师（后端数据里叫「法师」）
  [
    first('见习法师', '刚学会念咒，魔杖还不太听话，有时火花会蹦到自己头上。', [3, 11, 7]),
    second('魔法学徒', '咒语念得越来越顺，虽然不算厉害，但已经能帮上不少小忙。', [113, 412, 225]),
    third('星语巫师', '喜欢在夜里研究星星，据说能从星光里听见一点魔法的提示。'),
    fourth('大魔法师', '掌握了不少强力法术，走到哪里，都带着一点不好惹的气场。'),
    master(),
  ],
  // 4 大厨
  [
    first('帮厨小工', '从洗菜、递盘子开始学起，忙得团团转，却总惦记锅里的香味。', [11, 7, 3]),
    second('切配学徒', '刀工慢慢稳了，食材摆得整整齐齐，看着就让人放心不少。', [412, 225, 113]),
    third('掌勺主厨', '终于能站到灶台前掌勺，一锅热菜端出来，屋里立刻安静了。'),
    fourth('星级名厨', '手艺在小镇里传开了，熟客一进门，就知道该点它的招牌菜。'),
    master(),
  ],
  // 5 武术家（隐藏线：力量专修）
  [
    {
      ...first('习武小童', '扎马步还会晃，出拳也不够稳，但练完总会认真再来一次。', [24, 0, 0]),
      hidden: 0,
    },
    {
      ...second('入门弟子', '基本招式已经有模有样，虽然经验不多，胜在肯吃苦。', [750, 0, 0]),
      hidden: 0,
    },
    { ...third('内门武师', '练出了扎实功底，遇事不急不躁，出手比从前稳了很多。'), hidden: 0 },
    { ...fourth('武道宗师', '一招一式都有自己的章法，不用多说话，也能让人认真起来。'), hidden: 0 },
    master(),
  ],
  // 6 梦境旅人（隐藏线：智力专修）
  [
    {
      ...first('浅梦行者', '只敢在浅浅的梦里走一走，醒来时，口袋里像还装着星光。', [0, 24, 0]),
      hidden: 1,
    },
    {
      ...second('入梦旅人', '已经能进入梦境旅行，常带回一些听起来不像真的小故事。', [0, 750, 0]),
      hidden: 1,
    },
    { ...third('织梦者', '学会编织柔软的梦，让累了一天的宠物，也能睡得安稳些。'), hidden: 1 },
    { ...fourth('造梦师', '能造出完整又奇妙的梦境，有些梦醒来以后还让人舍不得。'), hidden: 1 },
    master(),
  ],
  // 7 大明星（隐藏线：魅力专修）
  [
    {
      ...first('偶像练习生', '每天在练习室里打转，动作还不熟，但眼睛一直亮亮的。', [0, 0, 24]),
      hidden: 2,
    },
    {
      ...second('闪耀新人', '第一次登台就被注意到，虽然还有点紧张，却已经很会发光。', [0, 0, 750]),
      hidden: 2,
    },
    { ...third('人气偶像', '粉丝越来越多，走在小镇路上，也常会被认出来打招呼。'), hidden: 2 },
    { ...fourth('当红巨星', '舞台经验十分丰富，一站到灯光下，气氛就会立刻热起来。'), hidden: 2 },
    master(),
  ],
]

/** 取一格的定义。项目开了 `noUncheckedIndexedAccess`，二维下标访问会被推成
 *  `CareerCellDef | undefined`；矩阵是 8×5 固定表，越界属代码错误，这里直接兜住。 */
export function cellAt(line: number, tier: number): CareerCellDef {
  return TREE[line]?.[tier] ?? master()
}

/**
 * 图标内容版本号。**只要动过 `static/qp-icons/career/*.png` 就必须 +1**。
 *
 * 为什么必须手改：`/qp-icons/*` 是 `Cache-Control: max-age=3600`，同名覆盖后
 * 浏览器一小时内仍拿旧图。这个坑真踩过——重做了三轮图标，电脑上（headless
 * 每次全新 profile）一直是对的，手机上一直显示第一版被裁切的旧图，
 * 看起来像"响应式坏了"。同 `lib/icons.ts` 的 ICON_VER 一个道理，只是图标目录不同。
 */
export const CAREER_ICON_VER = '14'

/**
 * 图标路径。两套素材：
 *
 *  - **未解锁**：`career/off/<line>_<tier>.png` —— **官方原始 PNG**
 *    （228×228 透明底，从 MuMu 模拟器的 `kuikly/download/pic/` 里捞出来的，
 *    就是 App 自己下载的那张图）。比从截图抠的清晰得多，优先用这套。
 *    目前 21 格有原图；剩下的（流浪散人见习、画家见习、侦探见习…）
 *    官方没缓存到，回落到下一套。
 *  - **其它**：`career/<line>_<tier>.png` —— 从 1080 截图抠的透明图。
 *    彩色版本服务端按「解锁状态」下发，只有自己号上已解锁的那几格才有原图，
 *    所以大部分格子只能靠抠图兜底。
 *
 * ⚠️ 动过任何一个 PNG 都要把 `CAREER_ICON_VER` +1（`/qp-icons/*` 走 max-age=3600）。
 */
/**
 * 官方**彩色**原图已有的格子（解锁态）。服务端只在宠物真正解锁后才下发彩色 URL，
 * 所以这几张是从模拟器里把「已解锁该格的号」一个个翻过去捞的。
 * 目前只有画家线三格（那号解锁到中级）—— 其余 16 格官方从没发过彩色图，
 * 游戏里那些格子本来就是灰的，所以不需要。
 */
const OFFICIAL_ON = new Set([
  '0_0', // 流浪散人·见习 饭桶
  '0_3', // 流浪散人·高级 万象宗师（紫钥匙）
  '1_0', '1_1', '1_2', // 画家线 见习/初级/中级（官方原图，最清晰）
  '2_0', // 侦探·见习 放大镜
  '2_1', // 侦探·初级 放大镜+记录板
  '2_2', // 侦探·中级 文件+放大镜
  '3_0', // 大魔法师·见习 星星法杖
  '3_1', // 大魔法师·初级 紫书（眼睛徽记）
  '4_0', // 大厨·见习 汤勺
  '5_0', // 武术家·见习 木剑
  '5_1', // 武术家·初级 银剑
  '5_2', // 武术家·中级 弯刀
  '5_3', // 武术家·高级 八卦盘
  '6_0', // 梦境旅人·见习 浅色水晶球
  '7_0', // 大明星·见习 麦克风
])

/**
 * 官方**灰色**原图已有的格子。
 *
 * ⚠️ **不要在这里 filter 掉 OFFICIAL_ON 的 key**：一个格子可以同时有彩色和灰色两份素材
 * （`on/1_2.png` 与 `off/1_2.png` 并存）。选哪个由调用方传的 `unlocked` 决定。
 * 之前写成 `.filter(k => !ON.has(k))`，结果未解锁时 `on` 分支不命中、`off` 又被过滤掉，
 * 两个都不中 → 落到根目录的旧抠图，界面上就是破图（踩过）。 */
const OFFICIAL_OFF = new Set([
  '1_1', '1_2', '1_3',
  '2_1', '2_2', '2_3',
  '3_1', '3_2', '3_3',
  '4_1', '4_2', '4_3',
  '5_1', '5_2', '5_3',
  '6_1', '6_2', '6_3',
  '7_1', '7_2', '7_3',
])

/**
 * @param unlocked 该格是否已解锁（决定用彩色原图还是灰色原图）
 */
/** 三个属性小图标（拳套/灯泡/花）—— 从官方 1080 截图抠的 3D 原图。
 *  之前用 emoji（🥊💡🌸）代替，形状和官方差太远（用户指出）。 */
export const attrIcon = (kind: 'str' | 'int' | 'cha'): string =>
  `/qp-icons/career/attr/${kind}.png?v=${CAREER_ICON_VER}`

/** 占位格（大师级未投放）的图标：从官方 1080 截图抠的六边形+问号原图。
 *  之前用 CSS `clip-path` 画，形状/描边粗细/问号字重都跟官方对不上。 */
export const placeholderIcon = (): string =>
  `/qp-icons/career/placeholder.png?v=${CAREER_ICON_VER}`

export const careerIcon = (line: number, tier: number, unlocked = false): string => {
  const key = `${line}_${tier}`
  let dir = 'career'
  if (unlocked && OFFICIAL_ON.has(key)) dir = 'career/on'
  else if (OFFICIAL_OFF.has(key)) dir = 'career/off'
  return `/${'qp-icons'}/${dir}/${key}.png?v=${CAREER_ICON_VER}`
}

/** 单元格五态（`empty` 是"连格子都不画"的那种，见 CareerCellDef）。 */
/**
 * 单元格状态。
 *
 * `lockedKnown` 是扫非好友时发现的**第三态**（2026-10-19）：
 * 官方的未解锁格有两种长相 ——
 *   · `locked`      **纯灰**（浅灰底 + 灰职业物）  = 该职业"未现世"
 *   · `lockedKnown` **彩色 + 半透明暗圆 + 白锁**   = 职业已存在，这只宠物还没解锁
 * 之前只做了「彩/灰」两态，把带锁的彩色当成灰色，判断错了。
 */
export type CellState =
  | 'empty'
  | 'placeholder'
  | 'locked'
  | 'lockedKnown'
  | 'unlocked'
  | 'equipped'

/** 属性三元组（力/智/魅）。 */
export type Trio = [number, number, number]

/**
 * 由「属性读数 + 已解锁记录」合成每一格的状态。
 *
 * 判定口径（**与游戏实测对齐，别按直觉改**）：
 *
 *  | 层级 | 解锁条件 |
 *  | --- | --- |
 *  | 见习 | **只看属性**。实测 画家见习 工分 `0/200`（没满）照样是彩色格 |
 *  | 初级及以上 | 属性 **且** 工分 **且** 学园毕业 **且** 金币够 |
 *
 * 第二条是 2026-10-19 对着真机学的：画家/初级 属性三项全达标
 * （8726/225 · 222/113 · 589/412），游戏里却仍是**灰格**，唯一没满足的是
 * `获得上一阶段工分 0/840`。所以初级+ 的工分/学园/费用是真门槛，不是"只管升星"。
 *
 * 另外两条实测事实：
 *  - **见习以哨兵实测记录为准**：`career_unlock.json` 的 `lines` 只记见习一级的名字，
 *    且实测存在「数值达标却没解锁」（隐藏线有概率性）与「数值不达标却已解锁」并存，
 *    记录优先，没记录才退回数值。
 *  - **中级/高级的需求游戏里没公开**（弹窗显示 `力量>?`），无法推算，
 *    只能靠后端逐级记录（`tierRecords`）点亮。要让它亮，得让
 *    `src/career_watch.py` 把每一级都记下来（现在只记见习）。
 */
export function resolveStates(
  attrs: Trio,
  unlockedLines: Record<string, { unlocked?: boolean; name?: string }>,
  equipped: { line: string; name: string } | null,
  /** 逐级记录：`{ '画家': { '1': { unlocked: true, name: '素描写手' } } }` */
  tierRecords: Record<string, Record<string, { unlocked?: boolean; name?: string }>> = {},
  /** 工分 / 金币 / 学园毕业 —— 初级及以上要用它们判门槛 */
  gates: { points: number; coins: number; jrGrad: boolean; midGrad: boolean } = {
    points: 0,
    coins: 0,
    jrGrad: false,
    midGrad: false,
  },
): CellState[][] {
  const sum = attrs[0] + attrs[1] + attrs[2]
  const ratioOk = (axis: 0 | 1 | 2): boolean => {
    const v = attrs[axis]
    return v >= 24 && v >= 2.5 * (sum - v)
  }
  /** 属性是否满足该格 */
  const attrOk = (def: CareerCellDef): boolean => {
    if (!def.need) return false // 需求未知（中级/高级）→ 不推算
    if (def.hidden !== undefined) return ratioOk(def.hidden)
    return attrs[0] >= def.need[0] && attrs[1] >= def.need[1] && attrs[2] >= def.need[2]
  }
  /** 初级及以上的额外门槛：工分 + 学园毕业 + 解锁费用（见文件头那张表） */
  const gateOk = (def: CareerCellDef): boolean => {
    if (def.points && def.points.need > 0 && gates.points < def.points.need) return false
    if (def.school) {
      const grad = def.school.includes('初级')
        ? gates.jrGrad
        : def.school.includes('中级')
          ? gates.midGrad
          : false
      if (!grad) return false
    }
    if (def.cost !== null && gates.coins < def.cost) return false
    return true
  }
  /** 后端记录里的该线（键名可能是「法师」而不是「大魔法师」） */
  const recOf = (lineName: string): { unlocked?: boolean; name?: string } | undefined => {
    const direct = unlockedLines[lineName]
    if (direct) return direct
    const alias = Object.keys(LINE_ALIAS).find((k) => LINE_ALIAS[k] === lineName)
    return alias ? unlockedLines[alias] : undefined
  }

  const out: CellState[][] = []
  for (let li = 0; li < LINES.length; li++) {
    const lineName = LINES[li] ?? ''
    const rec = recOf(lineName)
    // 逐级记录同样要过别名映射：后端写的是「法师」，前端列名是「大魔法师」
    const tierRec = tierRecords[lineName]
      ?? tierRecords[Object.keys(LINE_ALIAS).find((k) => LINE_ALIAS[k] === lineName) ?? '']
      ?? {}
    const row: CellState[] = []
    for (let ti = 0; ti < TIERS.length; ti++) {
      const def = cellAt(li, ti)
      if (def.empty) {
        row.push('empty')
        continue
      }
      if (!def.name) {
        row.push('placeholder')
        continue
      }
      // ---- 判定优先级 ----
      // 1. **职业树哨兵的逐格实测**（`tiers`）—— 最权威，直接就是游戏里看到的样子
      // 2. 见习级退回 `lines`（哨兵的老字段，也是实测）
      // 3. 都没有才按手填属性推算
      const tr = ti === 0 ? rec : tierRec[String(ti)]
      const hasRec = !!tr && 'unlocked' in tr
      let ok: boolean
      if (hasRec) {
        ok = !!tr!.unlocked
      } else if (ti === 0) {
        ok = attrOk(def) // 见习只看属性
      } else {
        ok = attrOk(def) && gateOk(def)
      }
      if (ok) {
        row.push('unlocked')
        continue
      }
      // 未解锁分两种：
      //   · `lockedKnown` **彩色 + 白锁** —— 官方"职业已现世但没解锁"的样子
      //   · `locked` **纯灰** —— 职业还没现世
      //
      // **有实测记录时，实测里没解锁就直接纯灰**（记录里没有"带锁"这一态，
      // 因为哨兵只判彩色/灰色两种像素特征；带锁是像素层面也能识别，
      // 但先按"记录说没解锁就是灰"来，避免又拿手填数据猜一遍）。
      const prev = ti === 0 ? null : row[ti - 1]
      const prevUnlocked = prev === 'unlocked' || prev === 'equipped'
      row.push(!hasRec && prevUnlocked && attrOk(def) ? 'lockedKnown' : 'locked')
    }
    out.push(row)
  }
  if (equipped) {
    const li = LINES.indexOf(equipped.line as LineName)
    if (li >= 0) {
      const rowDefs = TREE[li] ?? []
      const outRow = out[li]
      const ti = rowDefs.findIndex((c) => c.name === equipped.name)
      if (outRow && ti >= 0 && outRow[ti] === 'unlocked') outRow[ti] = 'equipped'
    }
  }
  return out
}

/** 弹窗里 `条件` 行的三段文案：`力量8726/225 智力222/113 魅力589/412`。
 *  需求未知（中级/高级）时显示 `力量>?`；占位格整行不显示。 */
export function conditionText(def: CareerCellDef, attrs: Trio): string {
  const names = ['力量', '智力', '魅力'] as const
  return names
    .map((n, i) => {
      if (!def.need) return `${n}>?`
      return `${n}${attrs[i]}/${def.need[i]}`
    })
    .join('  ')
}

/** 弹窗 `升星` 段的一行。 */
export interface AscensionRow {
  /** 行首标签（「获得上一阶段工分」/「宠物初级学园毕业」/「支付解锁费用」）；学园行前缀空 */
  label: string
  value: string
  /** 是否已达成（达成 = 深色，未达成 = 灰紫） */
  done: boolean
  /** 值前面挂一枚金币图标（官方「支付解锁费用 ¢1000」） */
  coin?: boolean
}

/**
 * 弹窗 `升星` 段的三行（官方原文逐字，见规格文档 3.2）：
 *
 * ```
 * 获得该星级工分 0/200         ← 见习；已解锁时给真实工分（360/360、278/???）
 * 获得上一阶段工分 0/840       ← 初级及以上
 * 宠物初级学园毕业（已毕业）     ← 学园行
 * 支付解锁费用 ¢1000           ← 费用行（未知时 ???）
 * ```
 *
 * 注意「278/???」：工分已够但需求未知时，分母也要显示 `???` 而不是编一个数。
 */
export function ascensionRows(
  def: CareerCellDef,
  unlocked: boolean,
  ctx: { points: number; coins: number; jrGrad: boolean; midGrad: boolean },
): AscensionRow[] {
  const rows: AscensionRow[] = []
  if (!def.points) return rows
  const need = def.points.need
  if (need < 0) {
    rows.push({ label: def.points.label, value: '???', done: false })
  } else {
    // 已解锁的见习格：需求未知（游戏里显示 ???）时也照实写 ???
    const denom = unlocked && def.points.label === '获得该星级工分' && ctx.points > need ? '???' : String(need)
    rows.push({
      label: def.points.label,
      value: `${ctx.points}/${denom}`,
      done: ctx.points >= need,
    })
  }
  if (def.school) {
    const grad = def.school.includes('初级')
      ? ctx.jrGrad
      : def.school.includes('中级')
        ? ctx.midGrad
        : false
    rows.push({ label: '', value: `${def.school}（${grad ? '已毕业' : '未毕业'}）`, done: grad })
  }
  if (def.cost !== null) {
    rows.push({ label: '支付解锁费用', value: String(def.cost), done: ctx.coins >= def.cost, coin: true })
  } else if (def.school) {
    rows.push({ label: '支付解锁费用', value: '???', done: false, coin: true })
  }
  return rows
}
