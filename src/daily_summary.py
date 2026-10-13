"""每日收益汇总：学习/打工收工总账 + 23:59 的冒险日报。

两条通知的数据来源都是**已经落盘的结算记录**，不额外 OCR、不碰设备：

- 学习/打工逐次收益 → `runs/session_rewards.jsonl`
  （`src/scenario.record_session_reward` 在结算页记录，字段含义见
  `parse_session_reward`）：学习有 `credits`/`attrs{力量,智力,魅力}`，
  打工有 `workpoints`/`coins`/`ad_coins`/`pay_detail`。
- 今日时长 → `src/progress.load_durations()` 的当日累计
  （`study_secs`/`work_secs`，跨天自动清零）。
- 冒险逐次收益 → `runs/adventure_live.jsonl`
  （`record_adventure_live` 记录，只有 `coins` 一个收益字段）。

**归属哪天**：学习/打工优先取结算页自带的日期时间（`sig`，= 这次活动的
开始时间，跨零点结算归前一天），识别不到再回落记录时刻 `ts` —— 与
`dashboard.rewards_data` 同一套口径，两处显示的数字必须一致。
冒险的 `sig` 只在结算页日期**不带空格**时才是日期串，实测多为内容 md5，
故冒险一律按 `ts`（记录时刻 = 结算时刻）归属。

只做纯读+文案拼装，不抛异常、不写进度；调用方（runner）负责投递。
"""
from __future__ import annotations

import json
import re

from .config import PROJECT_ROOT
from .progress import log
from .reward_table import drop_empty_columns, pick, rich_lines, rich_table

SESSION_REWARDS_FILE = PROJECT_ROOT / 'runs' / 'session_rewards.jsonl'
ADVENTURE_LIVE_FILE = PROJECT_ROOT / 'runs' / 'adventure_live.jsonl'

# 学习属性的三个维度（结算页徽章只会有这三个；与 dashboard._ATTR_KEYS 同源）
ATTR_KEYS = ('力量', '智力', '魅力')
# 汇总行里属性的显示顺序（用户指定：力量/魅力/智力）。
# 表格列的顺序仍由 ATTR_KEYS 决定 —— 那是解析层的字段顺序，与展示顺序无关。
ATTR_SUMMARY_ORDER = ('力量', '魅力', '智力')

# 结算页自带日期，容忍斜杠两侧空格（实测 `2026 / 09 /27 11:44`）
_REWARD_DATE_RE = re.compile(r'(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})')
# 结算时刻（sig 尾部 / ts 的时间部分）
_CLOCK_RE = re.compile(r'(\d{1,2})\s*:\s*(\d{2})')

# 明细表最多列这么多行（一天几十上百条结算时，长消息会挤爆 Telegram 的字符上限、
# 手机端也刷得没边）。超出部分折叠成一行提示，完整明细看仪表盘。
MAX_TABLE_ROWS = 30
DASH = '—'
# Telegram 普通消息上限 4096 字符；到这里就必须收手，否则整条被 API 拒收。
# （表格走的是 sendRichMessage，上限更高，见 src/notify.py 的 RICH_LIMIT。）
TG_TEXT_LIMIT = 4000

# **两个日报都没有通知标题常量**：用户要求去掉 `[QQ宠物·收工总结]` /
# `[QQ宠物·冒险日报]` 前缀（正文首行已自报家门，再顶前缀是重复的），
# `report_work_summary` / `report_adventure` 都返回空标题。


def _record_day(rec: dict, fallback: str) -> str:
    """这条结算记录算哪一天的收益（与 dashboard.rewards_data 同口径）。"""
    m = _REWARD_DATE_RE.search(str(rec.get('sig') or ''))
    if m:
        return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
    return str(rec.get('ts') or '')[:10] or fallback


# 日志型 jsonl 只回读尾部：adventure_live.jsonl 一天能涨 4MB 且只增不减，
# 整文件 read_text 迟早会把内存和耗时都拖垮。当天的记录必然在文件末尾。
TAIL_BYTES = 6_000_000


def _tail_lines(path) -> list[str]:
    """回读文件尾部（最多 TAIL_BYTES），按行切分。

    截断只可能切掉**更早**的记录，而调用方只关心当天 —— 而当天记录必在末尾。
    首行可能被截成半行，解析失败会被逐行 try 吞掉，不影响其余。
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    chunk = min(size, TAIL_BYTES)
    try:
        with open(path, 'rb') as f:
            if chunk < size:
                f.seek(size - chunk)
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return []
    return data.splitlines()


def load_rewards(day: str) -> list[dict]:
    """读某一天的学习/打工结算记录（按结算页日期归属，ts 兜底）。"""
    rows: list[dict] = []
    for line in _tail_lines(SESSION_REWARDS_FILE):
        try:
            rec = json.loads(line)
        except Exception:                  # noqa: BLE001 - 单行损坏不影响其余
            continue
        if isinstance(rec, dict) and _record_day(rec, day) == day:
            rows.append(rec)
    return rows


def load_adventure(day: str) -> list[dict]:
    """读某一天的冒险结算记录（按记录时刻归属，同一 sig 只算一次）。

    同一结算页可能被重复检测（收尾路径与出门兜底都会记），
    `record_adventure_live` 只跟文件最后一条比 sig 去重，跨会话重复仍会落盘，
    所以这里再按 sig 去重一遍，避免金币重复计。
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for line in _tail_lines(ADVENTURE_LIVE_FILE):
        try:
            rec = json.loads(line)
        except Exception:                  # noqa: BLE001
            continue
        if not isinstance(rec, dict) or not str(rec.get('ts') or '').startswith(day):
            continue
        sig = str(rec.get('sig') or '')
        if sig and sig in seen:
            continue
        if sig:
            seen.add(sig)
        rows.append(rec)
    return rows


# 冒险结算页里的**属性收益**（实测 token：`心情值+10` / `清洁值+10` / `体力值+10`，
# 436/491 条命中）。这是除金币外**唯一能确定**的收益维度，日报里必须带上——
# 冒险一天几百次，光是"多少金币"说不清这轮值不值，属性才是另一个产出。
_ADV_ATTR_RE = re.compile(r'(心情|清洁|体力)值?\s*[+＋]\s*(\d{1,4})')
ADV_ATTR_KEYS = ('心情', '清洁', '体力')


def _adventure_attrs(rows) -> tuple[dict, int]:
    """汇总冒险的属性收益，返回 ({心情,清洁,体力}, 有属性记录的条数)。"""
    totals = {k: 0 for k in ADV_ATTR_KEYS}
    hit = 0
    for rec in rows:
        found = _ADV_ATTR_RE.findall(''.join(rec.get('settle') or []))
        if found:
            hit += 1
        for key, value in found:
            if key in totals:
                totals[key] += int(value)
    return totals, hit


def summarize(day: str, study_s: int = 0, work_s: int = 0) -> dict:
    """汇总某一天的收益，返回结构化结果（供文案与测试共用）。

    只统计**能确定**的字段：某条记录没解析出金币就不计入金币合计，
    同时用 `*_n` 记下"解析出数值的条数"，文案里据此区分"没收益"与"没解析出来"。

    一并返回 `school_rows` / `work_rows`（逐次明细表的单元格），供通知里的
    等宽表格直接取用——表格与合计走同一份数据，不会出现"表里 3 条、合计按 4 条算"。
    """
    rewards = load_rewards(day)
    school = [r for r in rewards if r.get('kind') == 'school']
    work = [r for r in rewards if r.get('kind') == 'work']

    attrs = {k: 0 for k in ATTR_KEYS}
    attrs_n = 0
    credits = credits_n = 0
    for rec in school:
        for k, v in (rec.get('attrs') or {}).items():
            if k in attrs:
                attrs[k] += int(v or 0)
                attrs_n += 1
        if rec.get('credits') is not None:
            credits += int(rec['credits'])
            credits_n += 1

    coins = coins_n = 0
    workpoints = workpoints_n = 0
    ad_coins = ad_coins_n = 0
    for rec in work:
        if rec.get('coins') is not None:
            coins += int(rec['coins'])
            coins_n += 1
        if rec.get('workpoints') is not None:
            workpoints += int(rec['workpoints'])
            workpoints_n += 1
        if rec.get('ad_coins') is not None:
            ad_coins += int(rec['ad_coins'])
            ad_coins_n += 1

    adventures = load_adventure(day)
    adv_coins = sum(int(r.get('coins') or 0) for r in adventures)
    adv_paid = sum(1 for r in adventures if int(r.get('coins') or 0) > 0)
    adv_attrs, adv_attr_n = _adventure_attrs(adventures)
    adv_best = max((int(r.get('coins') or 0) for r in adventures), default=0)
    # 大额（100+）次数很少但贡献常占大头，是判断"今天运气好不好"的关键
    adv_big = sum(1 for r in adventures if int(r.get('coins') or 0) >= 100)
    adv_big_coins = sum(int(r.get('coins') or 0) for r in adventures
                        if int(r.get('coins') or 0) >= 100)

    return {
        'day': day,
        'study_secs': int(study_s), 'work_secs': int(work_s),
        'school': {'sessions': len(school), 'credits': credits, 'credits_n': credits_n,
                   'attrs': attrs, 'attrs_n': attrs_n},
        'work': {'sessions': len(work), 'coins': coins, 'coins_n': coins_n,
                 'workpoints': workpoints, 'workpoints_n': workpoints_n,
                 'ad_coins': ad_coins, 'ad_coins_n': ad_coins_n},
        'adventure': {'count': len(adventures), 'coins': adv_coins, 'paid': adv_paid,
                      'attrs': adv_attrs, 'attrs_n': adv_attr_n,
                      'best': adv_best, 'big': adv_big, 'big_coins': adv_big_coins},
        'school_rows': [_school_row(r) for r in school],
        'work_rows': [_work_row(r) for r in work],
        # 原始记录（汇总行要取档位等跨行信息，表格单元格里已不再重复携带）
        'work_records': work,
    }


def _record_time(rec: dict) -> str:
    """逐次明细要的是**几点几分**：优先取结算页自带时间（sig），退回落盘时刻。"""
    m = _CLOCK_RE.search(str(rec.get('sig') or ''))
    if m:
        return f'{int(m.group(1)):02d}:{m.group(2)}'
    ts = str(rec.get('ts') or '')
    return ts[11:16] if len(ts) >= 16 else ts


def _num(value, sign: str = '') -> str:
    """数值单元格：None（没解析出来）用破折号，别显示成 0 骗人。"""
    return DASH if value is None else f'{sign}{int(value)}'


def _school_row(rec: dict) -> list[str]:
    """学习逐次明细的原始行：时间 / 课程 / 学分 / 力量 / 智力 / 魅力（定长 6 列）。"""
    attrs = rec.get('attrs') or {}
    return [_record_time(rec), rec.get('course') or '(未识别课程)',
            _num(rec.get('credits'), '+'),
            *(_num(attrs.get(k), '+') for k in ATTR_KEYS)]


def _hire_cell(rec: dict) -> str:
    """「雇佣」列：这次打工的雇佣关系。

    两种方向语义相反，**不能混为一谈**（结算页文案实测）：
    - 正向「我雇佣了张二狗」（`hired_pet`）= 我雇了别人，加成归我；
    - 反向「我被张二狗雇佣了」（`hired_by`）= 别人雇了我、加成被对方拿走
      —— 前缀「被…雇」标出方向，免得看表的人以为这次是我们雇的人。
    """
    if rec.get('hired_pet'):
        return rec['hired_pet']
    if rec.get('hired_by'):
        return f"被{rec['hired_by']}雇"
    return DASH


def _work_row(rec: dict) -> list[str]:
    """打工逐次明细的原始行：时间 / 岗位 / 雇佣 / 金币 / 工分。

    **岗位不带档位**：档位（`(45分钟)`）实测一天内基本统一（本机 100/109 条同档），
    放进单元格等于每行重复 6 个字、把岗位列撑到 12 字宽 —— 手机上一超宽，
    Telegram 就硬折行，连 `01:43` 都会被劈成 `01:4`+`3`（用户实报）。
    档位改由汇总行统一说明（见 `format_work` 的 `_work_durations`）。

    **不含看视频补贴**（用户要求不显示）：补贴数据照常落盘（`ad_coins`），
    仪表盘收益记录页仍在用，只是通知表格里不单列一栏。
    """
    return [_record_time(rec), rec.get('job') or '(未识别岗位)', _hire_cell(rec),
            _num(rec.get('coins'), '+'), _num(rec.get('workpoints'), '+')]


def _work_durations(rows: list[dict]) -> str:
    """打工档位说明（汇总行用）：去重后的时长，如 `45分钟` 或 `10分钟 / 45分钟`。"""
    seen: list[str] = []
    for rec in rows:
        d = str(rec.get('job_duration') or '').strip()
        if d and d not in seen:
            seen.append(d)
    return ' / '.join(seen)


def _pick_columns(header: list[str], rows: list[list[str]],
                  keep: list[int]) -> tuple[list[str], list[list[str]]]:
    """只留 `keep` 指定的列（列索引来自原始行）。"""
    return [header[i] for i in keep], [[r[i] for i in keep] for r in rows]


def _table_block(rows: list[list[str]], header: list[str],
                 aligns: list[str]) -> list[str]:
    """一张**原生表格**（Telegram 富消息渲染）。

    **整列都是 `—` 的列会被丢掉**（`drop_empty_columns`）：手机宽度有限，列一多
    Telegram 会把窄列内容硬折行、连数字都劈开（实测 `01:43`→`01:4`+`3`）。
    「雇佣」列在没雇人的日子整列空值，正是它把表挤到折行 —— 这类列不输出。

    行数超限只保留**最近**的 MAX_TABLE_ROWS 条：既压住消息长度，也贴住
    "今天后来怎么样"的阅读习惯，完整明细去仪表盘看。

    返回富消息 HTML 片段列表：含 `<table>` 时 `src/notify.py` 走
    `sendRichMessage`（原生表格），其它渠道剥成一行一条的纯文本。
    """
    header, rows, keep = drop_empty_columns(header, rows, DASH)
    shown = rows[-MAX_TABLE_ROWS:]
    # aligns 必须跟着列一起裁，否则删列后对齐属性整体错位（金币会变成左对齐）
    out = [rich_table(header, shown, aligns=pick(aligns, keep))]
    if len(rows) > MAX_TABLE_ROWS:
        out.append(f'（仅列最近 {MAX_TABLE_ROWS} 条，共 {len(rows)} 条，'
                   f'完整明细见仪表盘收益记录）')
    return out


def _attr_summary(attrs: dict, has: bool) -> str:
    """学习汇总行的属性段：只写**当天真有涨**的属性，如 `力量 +216 / 魅力 +8`。

    **值为 0 的不写**（用户要求）：结算页徽章只给本次训练那一项，所以一天通常
    只涨一门；把 `魅力 +0 / 智力 +0` 也列出来纯属噪音。
    一条属性都没解析出来时给 `—`（区别于"这三项真没涨"）。
    """
    if not has:
        return DASH
    got = [k for k in ATTR_SUMMARY_ORDER if attrs.get(k)]
    if not got:
        return DASH                       # 解析到了徽章但值都是 0：也不编造
    return ' / '.join(f'{k} +{attrs[k]}' for k in got)


def format_work(data: dict) -> str:
    """学习/打工收工总账的正文（结构与文案由用户钉死）：

        🐾 2026-10-11 打工学习日报
        今日学习 学分 +231 力量 +216 / 魅力 +0 / 智力 +0
        <学习明细表>
        今日打工 工分 +378 金币 +3776
        <打工明细表>

    **其余文案一律不要**：没有"两项都已跑完"、没有时长、没有平均每次、
    没有"今天没上课"这类说明，也没有通知标题（首行已自报家门）。
    - 学习表 = 时间/课程/学分/（本次加到的属性）
    - 打工表 = 时间/岗位/雇佣/金币/工分；「看视频补贴」按用户要求不显示
      （数据照常落盘，仪表盘收益记录页仍能看到）
    - 属性列**按需出现**：结算页徽章只给本次训练的那一项，固定列三维的话
      每天都有一整列空值，手机上既占宽又难读。

    返回的正文是**富消息 HTML**：Telegram 走 `sendRichMessage` 渲染成原生表格
    （`<table bordered striped>`，`parse_mode=HTML` 不支持 table）；
    其它渠道由 `src/notify.py` 剥成纯文本，明细退化成一行一条。
    """
    school = data['school']
    work = data['work']
    rows_school = data.get('school_rows') or []
    rows_work = data.get('work_rows') or []

    parts = [f"🐾 <b>{data['day']} 打工学习日报</b>"]

    # ---- 学习 ----
    parts.append('')                      # 空串 = 段间空一行
    credits = f"学分 +{school['credits']}" if school['credits_n'] else f'学分 {DASH}'
    parts.append(f"<b>今日学习</b> {credits} "
                 f"{_attr_summary(school['attrs'], bool(school['attrs_n']))}")
    if not school['sessions']:
        parts.append(f'（今天没上课）' if not data['study_secs']
                     else '（没读到结算明细）')
    else:
        # 原始列：0 时间 / 1 课程 / 2 学分 / 3-5 力量·智力·魅力
        keep = [0, 1, 2] + [3 + i for i, k in enumerate(ATTR_KEYS)
                            if any(row[3 + i] != DASH for row in rows_school)]
        header, rows = _pick_columns(
            ['时间', '课程', '学分', '力量', '智力', '魅力'], rows_school, keep)
        parts.extend(_table_block(
            rows, header, ['left', 'left'] + ['right'] * (len(header) - 2)))

    # ---- 打工 ----
    parts.append('')
    points = f"工分 +{work['workpoints']}" if work['workpoints_n'] else f'工分 {DASH}'
    coins = f"金币 +{work['coins']}" if work['coins_n'] else f'金币 {DASH}'
    durations = _work_durations(data.get('work_records') or [])
    parts.append(f"<b>今日打工</b> {points} {coins}"
                 + (f"（{durations}）" if durations else ''))
    if not work['sessions']:
        parts.append(f'（今天没打工）' if not data['work_secs']
                     else '（没读到结算明细）')
    else:
        parts.extend(_table_block(
            rows_work, ['时间', '岗位', '雇佣', '金币', '工分'],
            ['left', 'left', 'left', 'right', 'right']))

    return rich_lines(parts)


def format_adventure(day: str, data: dict) -> str:
    """冒险日报的正文：**指标汇总**表，不列逐次明细。

    为什么跟打工表不一样：冒险一天几百次（实测 491 次），而打工只有 7 次。
    逐次列表在这里既刷屏又没信息量（36% 命中率意味着大半行都是 `+0`），
    所以日报给的是**指标表**：总次数 / 金币 / 心情 / 清洁 / 体力 / 命中率 /
    平均每次（用户指定的口径，其余指标一概不加）。

    **不带通知标题**（`report_adventure` 返回空标题）：正文首行已有
    `🧭 <日期> 冒险日报`，再顶一个 `[QQ宠物·冒险日报]` 就重复了。

    返回的正文含富消息 HTML：Telegram 走 `sendRichMessage` 渲染成原生表格，
    其它渠道由 `src/notify.py` 剥成纯文本（制表符分列）。
    """
    adv = data['adventure']
    if not adv['count']:
        return f'{day} 冒险：今天没有冒险记录'

    count, coins = adv['count'], adv['coins']
    paid = adv['paid']
    attrs = adv.get('attrs') or {}
    # 属性一个都没解析出来时显示 `—` 而不是 `+0`：0 会被误读成"今天这条属性真没涨"
    has_attrs = bool(adv.get('attrs_n'))

    def attr(name):
        return f'+{attrs.get(name, 0)}' if has_attrs else DASH

    lines = [f"🧭 <b>{day} 冒险日报</b>", '']
    lines.append(rich_table(
        ['指标', '数值'],
        [['总次数', f'{count} 次'],
         ['金币', f'+{coins}'],
         ['心情', attr('心情')],
         ['清洁', attr('清洁')],
         ['体力', attr('体力')],
         ['命中率', f'{paid / count:.0%}'],
         ['平均每次', f'+{coins / count:.1f}']],
        aligns=['left', 'right']))
    return rich_lines(lines)


def report_work_summary(day: str, study_s: int, work_s: int) -> tuple[str, str]:
    """返回 (标题, 正文)：学习/打工收工总账。

    **标题返回空串**（与冒险日报同理）：正文首行已写着
    `🐾 <日期> 打工学习日报`，再顶一个 `[QQ宠物·收工总结]` 是重复的。
    各发送渠道对空标题都做了容错（不留空行），见 `src/notify.py`。
    """
    data = summarize(day, study_s, work_s)
    log(f'收工总账已汇总: 学习 {data["study_secs"] // 60} 分钟、'
        f'打工 {data["work_secs"] // 60} 分钟，'
        f'冒险 {data["adventure"]["count"]} 次 {data["adventure"]["coins"]} 金币')
    return '', format_work(data)


def report_adventure(day: str) -> tuple[str, str]:
    """返回 (标题, 正文)：当日冒险日报。

    **标题返回空串**（用户要求去掉 `[QQ宠物·冒险日报]` 这个前缀）：正文首行已经
    写着 `🧭 <日期> 冒险日报`，再顶一个前缀是重复的。各发送渠道对空标题都做了
    容错（不会留下一个空行），见 `src/notify.py` 的 `_join_title`。
    """
    data = summarize(day)
    adv = data['adventure']
    log(f'冒险日报已汇总: {adv["count"]} 次，{adv["coins"]} 金币'
        f'（命中 {adv["paid"]} 次，最欧 +{adv["best"]}）')
    return '', format_adventure(day, data)
