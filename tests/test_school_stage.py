"""学园阶段识别必须认"选课面板标题"，不能被同屏的旧学园文案带偏。

2026-10-17 真实事故（runs/logs/2026-10-17.log 05:41 / 12:02 两段，附图
runs/alert_20261017_120401.png）：高级学园毕业升进修学院后，选课页面同屏有

    y=  24  "高级学园"            ← 顶部状态栏，**已毕业的旧学园**
    y= 549  "进修学院"            ← 地图上的建筑名
    y=1071  "进修学院研修生I"      ← 选课面板标题 = 当前真正的学园

进修学院没有"年级"后缀，原实现的第一级判据（带年级标题）匹配不到，落到第二级
"取第一个命中的文本块" —— 命中状态栏的"高级学园"，于是按高级学园的课程顺序
（魅力/力量/智力）去点"力量"，实际点到"魅力"课，详情核对 '奖励魅力+32' 与配置
科目不符 → 归位重试 ×2 → 回主页面重试 → 重启恢复 → 仍失败 → 告警退出调度器。

修法见 `SchoolScenario._detect_stage`：① 带年级标题 → ② 带"研修生"的进修学院
标题 → ③ 兜底**取 y 最大**的命中（面板标题在下方，状态栏在最上方）。
本文件锁住这三条，防止回退成"取第一个匹配"。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scenarios.school import (  # noqa: E402
    ADVANCED_ATTRIBUTE_COURSES,
    INSTITUTE_ATTRIBUTE_COURSES,
    SchoolScenario,
)


def _ocr(*rows: tuple[str, int, int]) -> list[tuple[str, int, int, float]]:
    """按 (文字, x, y) 造 OCR 结果，置信度固定。**顺序即 OCR 返回顺序**。"""
    return [(t, x, y, 0.9) for t, x, y in rows]


def test_institute_beats_stale_status_bar() -> None:
    """真实截图形态：旧学园状态栏排在 OCR 结果第一位时仍要认出进修学院。"""
    rows = _ocr(
        ('高级学园', 359, 24),          # 顶部状态栏（已毕业）
        ('进修学院', 431, 549),          # 地图建筑名（无"研修生"后缀）
        ('国进修学院研修生', 343, 1071),  # 面板标题（OCR 前面多认出一个字）
    )
    assert SchoolScenario._detect_stage(rows) == '进修学院'


def test_grade_title_wins_over_everything() -> None:
    """带年级的标题是最高判据：面板标题在下、旧学园在上，都要认标题。"""
    rows = _ocr(('初级学园', 359, 50), ('中级学园', 431, 527), ('中级学园1年级', 343, 1071))
    assert SchoolScenario._detect_stage(rows) == '中级学园'
    rows = _ocr(('中级学园', 300, 527), ('高级学园1年级', 343, 1071))
    assert SchoolScenario._detect_stage(rows) == '高级学园'


def test_loose_fallback_takes_lowest_hit() -> None:
    """没有任何标题时兜底取最靠下的命中（状态栏永远在最上方）。"""
    rows = _ocr(('高级学园', 359, 24), ('进修学院', 431, 549))
    assert SchoolScenario._detect_stage(rows) == '进修学院'
    # 反过来排也要取下方那个，证明判据是坐标不是顺序
    rows = _ocr(('进修学院', 431, 549), ('高级学园', 359, 24))
    assert SchoolScenario._detect_stage(rows) == '进修学院'


def test_no_stage_returns_none() -> None:
    assert SchoolScenario._detect_stage(_ocr()) is None
    assert SchoolScenario._detect_stage(_ocr(('宠物学园', 552, 163))) is None


def test_stage_maps_to_different_box_for_power() -> None:
    """同一科目"力量"在两级学园落到不同卡位——这正是不认错阶段的意义。"""
    assert ADVANCED_ATTRIBUTE_COURSES['力量'] == 'select_box_2'   # 魅力/力量/智力
    assert INSTITUTE_ATTRIBUTE_COURSES['力量'] == 'select_box_1'  # 力量/魅力/智力
