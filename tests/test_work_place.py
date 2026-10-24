"""打工地点确认的离线回归：属性条下的中央地点不能被地图背景抢走。

云朵梦舍和竹影武馆的地点/属性坐标来自截图；工作详情文字只用于代表面板特征。
所有识别输入均为固定 OCR 结果，不连接设备、不加载 OCR 模型。
"""
from __future__ import annotations

import numpy as np
import pytest

from scenarios import work
from src.ocr import parse_panel_location


CLOUD_STATS = [
    ('力量', 256, 285, 0.99),
    ('智力', 562, 285, 0.99),
    ('魅力', 874, 285, 0.99),
]
CLOUD_LOCATION = ('云朵梦舍', 466, 570, 0.999)
CLOUD_ROWS = [
    *CLOUD_STATS,
    ('闪耀星屋', 908, 344, 0.999),
    CLOUD_LOCATION,
    ('浅梦行者', 540, 1000, 0.99),
    ('工分+3', 540, 1300, 0.99),
    ('用时10分钟', 540, 1400, 0.99),
    ('雇佣有额外加成', 540, 1500, 0.99),
    ('去打工', 540, 1800, 0.99),
]
BAMBOO_ROWS = [
    ('力量', 304, 212, 0.99),
    ('智力', 561, 212, 0.99),
    ('魅力', 818, 212, 0.99),
    ('竹影武馆', 476, 474, 0.99),
    ('武馆教学助理', 540, 900, 0.99),
    ('去打工', 540, 1600, 0.99),
]


def _scenario(monkeypatch, rows, location='云朵梦舍'):
    scenario = work.WorkScenario.__new__(work.WorkScenario)
    scenario.location = location
    screen = np.zeros((2412, 1080, 3), dtype=np.uint8)
    seen = []

    def fake_ocr(frame):
        seen.append(frame)
        return rows

    monkeypatch.setattr(work, 'ocr_fullscreen', fake_ocr)
    scenario.screen = lambda: screen
    return scenario, screen, seen


@pytest.mark.parametrize('screen_width', [None, 1080])
def test_cloud_location_beats_higher_background_building(screen_width):
    """2026-10-24 告警：右侧闪耀星屋更高，真实面板地点仍是云朵梦舍。"""
    assert parse_panel_location(CLOUD_ROWS, screen_width=screen_width) == '云朵梦舍'


@pytest.mark.parametrize('screen_width', [None, 1080])
def test_old_bamboo_layout_is_still_recognized(screen_width):
    assert parse_panel_location(BAMBOO_ROWS, screen_width=screen_width) == '竹影武馆'


@pytest.mark.parametrize('scale', [0.5, 2 / 3, 1.0, 1.5])
def test_location_region_scales_with_screen_width(scale):
    rows = [
        (text, round(x * scale), round(y * scale), score)
        for text, x, y, score in CLOUD_ROWS
    ]
    assert parse_panel_location(rows, screen_width=round(1080 * scale)) == '云朵梦舍'
    assert parse_panel_location(rows) == '云朵梦舍'


@pytest.mark.parametrize('side_x', [100, 908])
def test_side_building_cannot_override_central_location(side_x):
    rows = [
        *CLOUD_STATS,
        ('云朵梦舍', side_x, 344, 0.99),
        ('闪耀星屋', 540, 570, 0.99),
    ]
    assert parse_panel_location(rows, screen_width=1080) == '闪耀星屋'


@pytest.mark.parametrize('rows', [
    [],
    [CLOUD_LOCATION],
    [('力量', 256, 285, 0.1), ('智力', 562, 285, 0.1), CLOUD_LOCATION],
    [*CLOUD_STATS, ('云朵梦舍', 466, 570, 0.49)],
    [*CLOUD_STATS, ('1080', 540, 344, 0.99), ('X', 540, 380, 0.99)],
    [*CLOUD_STATS, ('浅梦行者', 540, 1000, 0.99)],
])
def test_unreliable_location_returns_none(rows):
    assert parse_panel_location(rows, screen_width=1080) is None


def test_missing_width_needs_multiple_reliable_attribute_blocks():
    rows = [('力量', 256, 285, 0.99), CLOUD_LOCATION]
    assert parse_panel_location(rows) is None


@pytest.mark.parametrize('missing', ['力量', '智力', '魅力'])
def test_missing_attribute_does_not_shift_inferred_screen_center(missing):
    rows = [row for row in CLOUD_ROWS if row[0] != missing]
    assert parse_panel_location(rows) == '云朵梦舍'


def test_parser_ignores_input_order_and_normalizes_spaces():
    rows = [*CLOUD_STATS, ('云 朵 梦 舍', 466, 570, 0.99)]
    assert parse_panel_location(list(reversed(rows)), screen_width=1080) == '云朵梦舍'


def test_work_panel_uses_single_ocr_frame(monkeypatch):
    scenario, screen, seen = _scenario(monkeypatch, CLOUD_ROWS)
    assert scenario.panel_location() == '云朵梦舍'
    assert len(seen) == 1
    assert seen[0] is screen


def test_configured_place_is_confirmed(monkeypatch):
    scenario, screen, _ = _scenario(monkeypatch, CLOUD_ROWS, location='云 朵梦舍')
    assert scenario.is_correct_place_panel(screen) is True


def test_visible_configured_place_cannot_hide_different_panel(monkeypatch):
    rows = [
        *CLOUD_STATS,
        ('云朵梦舍', 908, 344, 0.99),
        ('闪耀星屋', 540, 570, 0.99),
        ('去打工', 540, 1800, 0.99),
    ]
    scenario, screen, _ = _scenario(monkeypatch, rows)
    assert scenario.panel_location(screen) == '闪耀星屋'
    assert scenario.is_correct_place_panel(screen) is False


@pytest.mark.parametrize('details', [
    [('去 打工', 540, 1800, 0.99)],
    [('工分+3', 540, 1300, 0.99), ('用时10分钟', 540, 1400, 0.99)],
    [('工分+3', 540, 1300, 0.99), ('雇佣有额外加成', 540, 1500, 0.99)],
])
def test_work_details_confirm_panel_when_start_button_is_replaced(monkeypatch, details):
    """低体力提示替换去打工按钮时，详情仍能证明工作面板已经打开。"""
    scenario, screen, _ = _scenario(monkeypatch, [*CLOUD_STATS, CLOUD_LOCATION, *details])
    assert scenario.panel_location(screen) == '云朵梦舍'
    assert scenario.is_correct_place_panel(screen) is True


@pytest.mark.parametrize('details', [
    [],
    [('浅梦行者', 540, 1000, 0.99)],
    [('工分+3', 540, 1300, 0.99)],
    [('用时10分钟', 540, 1400, 0.99)],
    [('雇佣有额外加成', 540, 1500, 0.99)],
    [('去打工', 540, 1800, 0.49)],
    [('工分+3', 540, 1300, 0.49), ('用时10分钟', 540, 1400, 0.99)],
])
def test_map_or_incomplete_details_are_not_a_work_panel(monkeypatch, details):
    scenario, screen, _ = _scenario(monkeypatch, [*CLOUD_STATS, CLOUD_LOCATION, *details])
    assert scenario.panel_location(screen) is None
    assert scenario.is_correct_place_panel(screen) is False
