"""好友被雇时长配额（游戏机制：每个好友每天最多被雇 4 小时）。

「雇佣好友」并入打工后不再用"每天雇佣 N 次"来限制，而是按游戏规则记账：
每雇某个好友打工一次，就把他当天的被雇时长加上这次打工的时长（`work.duration`）；
累计到上限（`work.hire_daily_hours`，默认 4）后就不再雇他——**再雇也不给收益**。

记账键用**配置里那个目标名**（`work.hire_name`）：面板/好友列表里同一个人可能显示
主人名，也可能显示宠物名，用配置名当键才不会把自己拆成两条账。

文件：`runs/hire_quota.json`
    {"date": "2026-09-30", "mins": {"柠檬..": 135, "张二狗": 45}}
跨天在读取时自动清零（日期不一致就当空账本）。
"""
from __future__ import annotations

import json
from datetime import date

from .config import PROJECT_ROOT

HIRE_QUOTA_FILE = PROJECT_ROOT / 'runs' / 'hire_quota.json'

# 打工时长文案 → 分钟（与 scenarios/work.py 的 DURATION_BOXES 键一一对应）
DURATION_MINUTES = {'10分钟': 10, '45分钟': 45, '2小时': 120}


def duration_minutes(duration: str) -> int:
    """'45分钟' → 45；未知时长按 45 分钟算（与默认配置一致）。"""
    return DURATION_MINUTES.get(duration, 45)


def load_hire_quota() -> tuple[str, dict[str, int]]:
    """读当天账本：返回 (日期, {好友名: 已用分钟})。文件缺失/损坏/跨天 → 空账本。"""
    today = date.today().isoformat()
    try:
        raw = json.loads(HIRE_QUOTA_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return today, {}
    if not isinstance(raw, dict) or raw.get('date') != today:
        return today, {}   # 跨天（或格式不对）→ 清零
    mins = raw.get('mins')
    if not isinstance(mins, dict):
        return today, {}
    out: dict[str, int] = {}
    for k, v in mins.items():
        if isinstance(v, (int, float)) and v > 0:
            out[str(k)] = int(v)
    return today, out


def _write(today: str, mins: dict[str, int]) -> None:
    try:
        HIRE_QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        HIRE_QUOTA_FILE.write_text(
            json.dumps({'date': today, 'mins': mins}, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass   # 记账失败只影响"还要不要雇"，不该让打工中断


def used_minutes(name: str) -> int:
    """某个好友今天已被雇的分钟数（name 传配置里的目标名）。"""
    _, mins = load_hire_quota()
    return int(mins.get(name, 0))


def is_full(name: str, limit_hours: float) -> bool:
    """该好友今天是否已被雇满（limit_hours <= 0 表示不限制）。"""
    if not name or limit_hours <= 0 or not _name_configured(name):
        return False
    return used_minutes(name) >= int(limit_hours * 60)


def add_hire(name: str, minutes: int, quiet_log=None) -> int:
    """记一次雇佣：把该好友今天的被雇时长加上 minutes，返回累计分钟数。"""
    if not name or minutes <= 0:
        return 0
    today, mins = load_hire_quota()
    mins[name] = int(mins.get(name, 0)) + int(minutes)
    _write(today, mins)
    if quiet_log is not None:
        quiet_log(f'好友配额记账: {name} 今天已被雇 {mins[name]} 分钟')
    return mins[name]


def _name_configured(name: str) -> bool:
    """占位判断：只有"配置过的目标名"才记账/判满，避免把面板自动挑的人也记进来。

    面板路径自动选人时传的是**实际被雇者**的行文字，与配置名不一定一致——
    那种情况调用方会自己决定是否记账（见 scenarios/work.py）。
    """
    return bool(name and name.strip())
