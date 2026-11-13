"""DeviceScenario.apply_session_items 的离线测试（不连设备）。

用 `DeviceScenario.__new__` 跳掉设备连接，把 see/click/screen 换成桩，
再用 `scenario.ocr_screen` 的返回值模拟「已喝 N/M 杯」的读数。

真机实测的两个关键事实都固化成用例：
  - 「限1杯」的道具在本次会话喝过以后再点，游戏只弹
    「本次学习该道具仅能喝1杯」，`已喝` 数字不变 —— 这时**不能记账**；
  - 面板最右边的 prop_exchange_add 是钻石购买入口，任何情况下都不能点。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

import src.scenario as scenario
from src import items
from src.config import Config, ItemsConfig
from src.scenario import DeviceScenario


class _FakeSource:
    """和 u2 的 PageSource 一样：.root 是 ElementTree 的根节点。"""

    def __init__(self, xml):
        self.root = ET.fromstring(xml)


class _FakeDev:
    def __init__(self):
        self.clicks: list[tuple[int, int]] = []
        self.panel_slots = True

    def hierarchy(self):
        slots = ('<node content-desc="prop_slot_2" bounds="[378,1813][702,2137]" />'
                 '<node content-desc="prop_slot_3" bounds="[743,1854][864,2097]" />'
                 if self.panel_slots else '')
        return _FakeSource(
            f'<hierarchy><node content-desc="" bounds="[0,0][1080,2412]">'
            f'{slots}'
            f'<node content-desc="prop_exchange_add" bounds="[907,1910][1037,2040]" />'
            f'</node></hierarchy>')

    def window_size(self):
        return 1080, 2412

    def click(self, x, y):
        self.clicks.append((x, y))


def _make_scenario(monkeypatch, *, visible, progress, enabled=True, per_day=1,
                   slots=True):
    """visible: 哪些 locator 名字出现在当前屏幕；progress: [(已用, 上限), ...]"""
    s = DeviceScenario.__new__(DeviceScenario)
    s.cfg = Config(items=ItemsConfig(enabled=enabled, study_per_day=per_day,
                                     work_per_day=per_day))
    s.dev = _FakeDev()
    s.dev.panel_slots = slots
    s.pending = None

    s.ensure_main_page = lambda: None       # 不真的回主页面
    s.leave_home = lambda: None
    s.screen = lambda: object()
    s.click = lambda x, y: s.dev.click(x, y)
    s.see = lambda name, screen=None, source=None: (
        {'prop_card_add': (583, 1696, 1.0),
         'prop_panel_close': (996, 1720, 1.0),
         'prop_purchase_close': (996, 1537, 1.0)}.get(name) if name in visible else None)

    readings = iter(progress)

    def _ocr(_screen):
        # ocr_screen 的真实返回是 [(文字, x, y, score), ...] —— 这里必须同构，
        # 否则调用方 `t, *_` 会把字符串按字符拆开
        value = next(readings, None)
        if value is None:
            return [('高钙奶', 100, 100, 1.0)]
        return [(f'已喝 {value[0]}/{value[1]} 杯', 100, 100, 1.0)]

    monkeypatch.setattr(scenario, 'ocr_screen', _ocr)
    return s


def test_uses_one_item_and_records_it(monkeypatch, runs_dir):
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_panel_close'},
                       progress=[(0, 45), (1, 45)])
    assert s.apply_session_items('study') is True
    # 点的是居中的道具槽，不是右边的钻石购买入口
    assert (540, 1975) in s.dev.clicks
    assert (972, 1975) not in s.dev.clicks, '绝不能点 prop_exchange_add（钻石购买）'
    assert items.load_usage()[1] == {'study': 1}


def test_limited_item_that_cannot_be_used_again_is_not_recorded(monkeypatch, runs_dir):
    """真机：限1杯的道具再点只弹提示，已喝 1 → 1。这种情况不能记一笔。"""
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_panel_close'},
                       progress=[(1, 45), (1, 45)])
    assert s.apply_session_items('study') is False
    assert items.load_usage()[1] == {}


def test_unreadable_progress_counts_as_used(monkeypatch, runs_dir):
    """OCR 读不到「已喝 N/M」时按已使用记账（点下去就生效），不能反复重试。"""
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_panel_close'},
                       progress=[None, None])
    assert s.apply_session_items('study') is True
    assert items.load_usage()[1] == {'study': 1}


def test_missing_plus_on_card_skips(monkeypatch, runs_dir):
    s = _make_scenario(monkeypatch, visible=set(), progress=[])
    assert s.apply_session_items('work') is False
    assert items.load_usage()[1] == {}


def test_panel_not_opening_skips(monkeypatch, runs_dir):
    s = _make_scenario(monkeypatch, visible={'prop_card_add'}, progress=[])
    assert s.apply_session_items('work') is False
    assert items.load_usage()[1] == {}


def test_empty_panel_skips(monkeypatch, runs_dir):
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_panel_close'},
                       progress=[(0, 45), (0, 45)], slots=False)
    assert s.apply_session_items('study') is False
    assert items.load_usage()[1] == {}


def test_no_items_goes_to_purchase_page_and_is_closed(monkeypatch, runs_dir):
    """打工实测：没有可用道具时点「+」直接弹「购买道具」。

    程序只能关掉走人 —— 不能点商品，更不能点「购买」。
    """
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_purchase_close'},
                       progress=[])
    assert s.apply_session_items('work') is False
    assert (996, 1537) in s.dev.clicks, '必须点购买页右上角的 ×'
    assert (540, 2278) not in s.dev.clicks, '绝不能点「购买」按钮'
    assert (155, 1831) not in s.dev.clicks, '绝不能点商品（钻石）'
    assert items.load_usage()[1] == {}


def test_disabled_config_never_touches_the_app(monkeypatch, runs_dir):
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_panel_close'},
                       progress=[(0, 45), (1, 45)], enabled=False)
    assert s.apply_session_items('study') is False
    assert s.dev.clicks == []


def test_daily_quota_stops_after_limit(monkeypatch, runs_dir):
    items.record_use('study')
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_panel_close'},
                       progress=[(0, 45), (1, 45)], per_day=1)
    assert s.apply_session_items('study') is False
    assert s.dev.clicks == []


def test_exceptions_never_escape(monkeypatch, runs_dir):
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_panel_close'},
                       progress=[(0, 45), (1, 45)])

    def boom(*_a, **_k):
        raise RuntimeError('页面炸了')

    s.leave_home = boom
    assert s.apply_session_items('study') is False


def test_use_up_mode_ignores_daily_count(monkeypatch, runs_dir):
    for _ in range(20):
        items.record_use('study')
    s = _make_scenario(monkeypatch, visible={'prop_card_add', 'prop_panel_close'},
                       progress=[(0, 45), (1, 45)], per_day=-1)
    assert s.apply_session_items('study') is True
