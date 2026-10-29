"""力量 / 智力 / 魅力读数的数值解析。

游戏在数值达到 1 万后会显示缩写：截图实测 `力量 1.18w`、`智力 878`、`魅力 595`。
旧正则 `(力量|智力|魅力)\\s*([0-9]{1,6})` 只抓前导整数，把 `1.18w` 读成 `1` ——
职业树页和仪表盘因此显示「力量 1」。

这里统一处理：
  · 1.18w / 1.2W / 1.18万 → 11800 / 12000 / 11800
  · 1.18（OCR 把 w 吞了）→ 按小数推断为 11800（游戏属性都是整数，
    出现小数只可能是 w 缩写；这是给 OCR 漏字留的兜底）
  · 878 / 11800 → 原值

`src/career_watch.py`（职业树哨兵）与 `tools/career_sync.py`（手动同步）
共用这一份，别再各写一个正则。
"""
from __future__ import annotations

import re

# 标签 + 数字（允许小数/逗号）+ 可选 w/W/万。空格可有可无（OCR 常在中间插空格）。
ATTR_RE = re.compile(r'(力量|智力|魅力)\s*([0-9]{1,6}(?:[.,，．][0-9]{1,3})?)\s*([wW万]?)')

# 游戏里的 w = 万 = 10000
_ABBR_SCALE = 10000


def parse_attr(text: str) -> tuple[str, int] | None:
    """从一段 OCR 文本里解析「标签, 数值」；不匹配返回 None。

    数值含小数点或后缀（w/W/万）时按 ×10000 处理；否则按普通整数。
    """
    m = ATTR_RE.search(text or '')
    if not m:
        return None
    raw = m.group(2).replace(',', '.').replace('，', '.').replace('．', '.')
    label, suffix = m.group(1), m.group(3)
    if suffix or '.' in raw:
        return label, int(round(float(raw) * _ABBR_SCALE))
    return label, int(raw)
