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

SESSION_REWARDS_FILE = PROJECT_ROOT / 'runs' / 'session_rewards.jsonl'
ADVENTURE_LIVE_FILE = PROJECT_ROOT / 'runs' / 'adventure_live.jsonl'

# 学习属性的三个维度（结算页徽章只会有这三个；与 dashboard._ATTR_KEYS 同源）
ATTR_KEYS = ('力量', '智力', '魅力')

# 结算页自带日期，容忍斜杠两侧空格（实测 `2026 / 09 /27 11:44`）
_REWARD_DATE_RE = re.compile(r'(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})')

# 标题（与告警/职业解锁区分，便于在手机通知栏上分辨来源）
WORK_SUMMARY_TITLE = '[QQ宠物·收工总结]'
ADVENTURE_REPORT_TITLE = '[QQ宠物·冒险日报]'


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


def summarize(day: str, study_s: int = 0, work_s: int = 0) -> dict:
    """汇总某一天的收益，返回结构化结果（供文案与测试共用）。

    只统计**能确定**的字段：某条记录没解析出金币就不计入金币合计，
    同时用 `*_n` 记下"解析出数值的条数"，文案里据此区分"没收益"与"没解析出来"。
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

    return {
        'day': day,
        'study_secs': int(study_s), 'work_secs': int(work_s),
        'school': {'sessions': len(school), 'credits': credits, 'credits_n': credits_n,
                   'attrs': attrs, 'attrs_n': attrs_n},
        'work': {'sessions': len(work), 'coins': coins, 'coins_n': coins_n,
                 'workpoints': workpoints, 'workpoints_n': workpoints_n,
                 'ad_coins': ad_coins, 'ad_coins_n': ad_coins_n},
        'adventure': {'count': len(adventures), 'coins': adv_coins, 'paid': adv_paid},
    }


def _hours(secs: int) -> str:
    return f'{secs / 3600:.1f} 小时'


def format_work(data: dict) -> str:
    """学习/打工收工总账的正文。

    用户口径：先报**各自跑了多久**，再报**各自的收益** ——
    学习是三维属性（力量/智力/魅力）与学分，打工是工分与金币
    （「看视频 +N」是独立一笔，单列，不混进工资金币）。

    时长取进度账本（`study_secs`/`work_secs`），收益取结算记录 —— 两者来源不同，
    可能不一致（结算页 OCR 失败就没记录，但时长照记），所以时长非 0 而结算条数
    为 0 时要说明"没读到明细"，不能直接写"今天没上课"自相矛盾。
    """
    school = data['school']
    work = data['work']
    attrs = school['attrs']
    attr_txt = ' · '.join(f'{k} +{v}' for k, v in attrs.items())

    lines = [f"今天学习 {_hours(data['study_secs'])} · 打工 {_hours(data['work_secs'])}"
             f"（合计 {_hours(data['study_secs'] + data['work_secs'])}），两项都已跑完"]

    lines.append('')
    lines.append(f"学习 {_hours(data['study_secs'])}"
                 + (f"（{school['sessions']} 节）" if school['sessions'] else ''))
    if not school['sessions']:
        lines.append('  今天没上课' if not data['study_secs']
                     else '  没记录到结算明细（时长账本有，但收益没读到）')
    else:
        if school['attrs_n']:
            lines.append(f'  属性：{attr_txt}')
        else:
            lines.append('  属性：未解析出（结算页没读到徽章）')
        if school['credits_n']:
            lines.append(f"  学分：+{school['credits']}")

    lines.append('')
    lines.append(f"打工 {_hours(data['work_secs'])}"
                 + (f"（{work['sessions']} 次）" if work['sessions'] else ''))
    if not work['sessions']:
        lines.append('  今天没打工' if not data['work_secs']
                     else '  没记录到结算明细（时长账本有，但收益没读到）')
    else:
        if work['workpoints_n']:
            lines.append(f"  工分：+{work['workpoints']}")
        else:
            lines.append('  工分：未解析出')
        if work['coins_n']:
            lines.append(f"  金币：+{work['coins']}")
        else:
            lines.append('  金币：未解析出')
        if work['ad_coins_n']:
            lines.append(f"  看视频补贴：+{work['ad_coins']}")

    return '\n'.join(lines)


def format_adventure(day: str, data: dict) -> str:
    """冒险日报的正文：这一天的总次数与总金币。"""
    adv = data['adventure']
    if not adv['count']:
        return f'{day} 冒险：今天没有冒险记录'
    lines = [f'{day} 冒险共 {adv["count"]} 次，获得 {adv["coins"]} 金币']
    lines.append(f'（{adv["paid"]} 次有收益，{adv["count"] - adv["paid"]} 次空手）')
    return '\n'.join(lines)


def report_work_summary(day: str, study_s: int, work_s: int) -> tuple[str, str]:
    """返回 (标题, 正文)：学习/打工收工总账。"""
    data = summarize(day, study_s, work_s)
    log(f'收工总账已汇总: 学习 {data["study_secs"] // 60} 分钟、'
        f'打工 {data["work_secs"] // 60} 分钟，'
        f'冒险 {data["adventure"]["count"]} 次 {data["adventure"]["coins"]} 金币')
    return WORK_SUMMARY_TITLE, format_work(data)


def report_adventure(day: str) -> tuple[str, str]:
    """返回 (标题, 正文)：当日冒险日报。"""
    data = summarize(day)
    return ADVENTURE_REPORT_TITLE, format_adventure(day, data)
