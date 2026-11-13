"""学习 / 打工时用道具（衣橱里那些「使用道具可提升表现」的东西）。

**用途**：每天「每日福利」领到的道具（高钙奶之类）不主动用就只是躺在衣橱里。
这个模块负责“今天还能用几个”的账本，以及在道具面板里挑哪个槽位点下去。

真机实测（2026-11-13，1080x2412，45 分钟短课）的页面结构 —— 见
`src/locators.py` 里 `prop_card_add` / `prop_panel_close` / `prop_exchange_add`：

  出门页「正在学习」卡片
    → `desc="prop_card_add"`（那个 +）
    → 「道具」面板：顶部 OCR「已喝 1/45 杯」（Y = 这节课的分钟数）
                   横向 RecyclerView：`prop_slot_2` `prop_slot_3` … 每种道具一个槽
                   右侧 `desc="prop_exchange_add"` = **钻石购买**，绝对不能点
    → 点居中的那个槽：库存 -1、已喝 +1，无二次确认
    → `desc="prop_panel_close"` 关闭

**每次只用 1 个**（不是刷满）：`*_per_day` 是当天上限，-1 = 用完（每节课 1 个，
一直上到库存见底）。

本模块只做纯逻辑（账本 + 控件树解析），不碰设备，方便离线单测。
"""
from __future__ import annotations

import json
import re
from datetime import date

from .config import PROJECT_ROOT
from .jsonio import write_json

USAGE_FILE = PROJECT_ROOT / 'runs' / 'item_usage.json'

# 道具上下文 → 配置字段名（学习/打工能用的道具不一样，账也分开记）
LIMIT_FIELDS = {'study': 'study_per_day', 'work': 'work_per_day'}
KIND_NAMES = {'study': '学习', 'work': '打工'}

# 面板顶部的进度文案：「已喝 1/45 杯」。Y 是这次会话的上限（= 课时分钟数），
# 只用来判断"这一下点下去有没有生效"，不参与每日额度。
_PROGRESS_RE = re.compile(r'已[喝饮吃用]\s*(\d+)\s*/\s*(\d+)')
# 兜底：文案变了但还留着 "N/M 杯" 这种形状
_PROGRESS_FALLBACK_RE = re.compile(r'(\d+)\s*/\s*(\d+)\s*[杯瓶罐次]')

SLOT_RE = re.compile(r'prop_slot_(\d+)')


# ---------------------------------------------------------------- 每日账本
def today() -> str:
    return date.today().isoformat()


def load_usage() -> tuple[str, dict[str, int]]:
    """读当天账本：返回 (日期, {'study': n, 'work': n})。缺文件/损坏/跨天 → 空账。"""
    day = today()
    try:
        raw = json.loads(USAGE_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return day, {}
    if not isinstance(raw, dict) or raw.get('date') != day:
        return day, {}
    counts = raw.get('counts')
    if not isinstance(counts, dict):
        return day, {}
    return day, {str(k): int(v) for k, v in counts.items()
                 if isinstance(v, (int, float))}


def record_use(kind: str, item: str = '') -> dict[str, int]:
    """记一次使用（跨天自动清零）。返回记完之后的当天账本。"""
    day, counts = load_usage()
    counts[kind] = counts.get(kind, 0) + 1
    write_json(USAGE_FILE, {'date': day, 'counts': counts,
                            'last': {'kind': kind, 'item': item,
                                     'at': _now_hm()}})
    return counts


def _now_hm() -> str:
    from datetime import datetime
    return datetime.now().strftime('%H:%M')


# ---------------------------------------------------------------- 额度判断
def daily_limit(items_cfg, kind: str) -> int:
    """当天上限：-1 = 用完（不限），0 = 不用。"""
    return int(getattr(items_cfg, LIMIT_FIELDS[kind], 0) or 0)


def can_use(items_cfg, kind: str) -> tuple[bool, str]:
    """现在还能不能再用一个。返回 (可以, 原因)。原因只用于日志。"""
    if not getattr(items_cfg, 'enabled', False):
        return False, '道具使用未开启'
    limit = daily_limit(items_cfg, kind)
    if limit == 0:
        return False, f'配置为不用道具（{LIMIT_FIELDS[kind]} = 0）'
    _day, counts = load_usage()
    used = counts.get(kind, 0)
    if limit > 0 and used >= limit:
        return False, f'今天{LIMIT_FIELDS[kind].split("_")[0]}已用 {used}/{limit} 个'
    return True, ''


# ---------------------------------------------------------------- 面板解析
def parse_progress(texts) -> tuple[int, int] | None:
    """从「已喝 1/45 杯」解析 (已用, 本次上限)。解析不到返回 None。"""
    joined = ' '.join(str(t) for t in texts if t)
    for pattern in (_PROGRESS_RE, _PROGRESS_FALLBACK_RE):
        m = pattern.search(joined)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def prop_slots(source) -> list[dict]:
    """从控件树里取所有道具槽：`[{'desc', 'cx', 'cy', 'x1'...}]`，按 index 排序。"""
    if source is None:
        return []
    slots: list[dict] = []
    root = getattr(source, 'root', None)
    if root is None:
        return []
    for node in root.iter():
        desc = node.get('content-desc') or ''
        m = SLOT_RE.fullmatch(desc)
        if not m:
            continue
        b = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds') or '')
        if not b:
            continue
        x1, y1, x2, y2 = (int(b.group(i)) for i in (1, 2, 3, 4))
        slots.append({'desc': desc, 'index': int(m.group(1)),
                      'cx': (x1 + x2) // 2, 'cy': (y1 + y2) // 2,
                      'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2})
    return sorted(slots, key=lambda s: s['index'])


def pick_slot(source, screen_w: int) -> dict | None:
    """挑"居中的那个槽"——面板把当前选中项摆在屏幕正中，点它就是用它。

    只在**完整可见**的槽里挑（右边缘被切掉的那个 x2 已经越界，点它容易点偏），
    没有完整可见的就退回最近中心的那一个。
    """
    slots = prop_slots(source)
    if not slots:
        return None
    center = screen_w // 2
    visible = [s for s in slots if s['x1'] >= 0 and s['x2'] <= screen_w]
    pool = visible or slots
    return min(pool, key=lambda s: abs(s['cx'] - center))
