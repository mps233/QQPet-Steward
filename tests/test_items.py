"""src/items.py 的离线单测：每日账本、额度判断、道具面板解析。

不连设备：控件树用 xml.etree 造的假 root（和 u2 的 PageSource.root 一样支持
`.iter()` / `.get()`），道具面板的结构抄真机 dump（prop_slot_2 / prop_slot_3
+ 右侧 prop_exchange_add）。
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import pytest

from src import items
from src.config import ItemsConfig


class _FakeSource:
    """最小化的 PageSource：只需要 .root 支持 iter()/get()。"""

    def __init__(self, xml: str):
        self.root = ET.fromstring(xml)


def _panel(centered: str = 'prop_slot_2') -> _FakeSource:
    """真机 1080x2412 的分布：slot_2 居中(378..702)，slot_3 被右边缘切掉(743..864)。"""
    return _FakeSource(f'''
    <hierarchy>
      <node content-desc="" bounds="[0,1813][1080,2288]">
        <node content-desc="{centered}" bounds="[378,1813][702,2137]" />
        <node content-desc="prop_slot_3" bounds="[743,1854][864,2097]" />
        <node content-desc="prop_exchange_add" bounds="[907,1910][1037,2040]" />
        <node content-desc="prop_panel_close" bounds="[956,1680][1037,1761]" />
      </node>
    </hierarchy>''')


# ---------------------------------------------------------------- 面板解析
def test_parse_progress_reads_cups():
    assert items.parse_progress(['已喝 1/45 杯']) == (1, 45)
    # OCR 常常不带空格；中文和数字混排时也可能被拆成两个 token
    assert items.parse_progress(['已喝0/45杯']) == (0, 45)
    assert items.parse_progress(['道具', '已喝12/45杯']) == (12, 45)


def test_parse_progress_falls_back_to_ratio():
    # 文案被改掉时，只要还有 "N/M 杯" 的形状也能读
    assert items.parse_progress(['咕噜酸奶', '3/45 杯']) == (3, 45)


def test_parse_progress_returns_none_without_number():
    assert items.parse_progress(['高钙奶', '每次一杯']) is None
    assert items.parse_progress([]) is None


def test_prop_slots_lists_only_slots():
    slots = items.prop_slots(_panel())
    assert [s['desc'] for s in slots] == ['prop_slot_2', 'prop_slot_3']
    # 购买入口不是槽位，永远不能被当成道具
    assert all('exchange' not in s['desc'] for s in slots)


def test_pick_slot_chooses_centered_and_fully_visible():
    slot = items.pick_slot(_panel(), 1080)
    assert slot['desc'] == 'prop_slot_2'
    assert (slot['cx'], slot['cy']) == (540, 1975)


def test_pick_slot_none_when_panel_has_no_slot():
    empty = _FakeSource('<hierarchy><node content-desc="prop_exchange_add" '
                        'bounds="[907,1910][1037,2040]" /></hierarchy>')
    assert items.pick_slot(empty, 1080) is None
    assert items.pick_slot(None, 1080) is None


# ---------------------------------------------------------------- 每日账本
def test_usage_starts_empty_when_file_missing(runs_dir):
    day, counts = items.load_usage()
    assert day == date.today().isoformat()
    assert counts == {}


def test_record_use_increments_and_persists(runs_dir):
    items.record_use('study', 'prop_slot_2')
    items.record_use('study')
    items.record_use('work')
    _day, counts = items.load_usage()
    assert counts == {'study': 2, 'work': 1}
    raw = json.loads(items.USAGE_FILE.read_text('utf-8'))
    assert raw['date'] == date.today().isoformat()
    assert raw['last']['kind'] == 'work'


def test_usage_resets_across_days(runs_dir):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    items.USAGE_FILE.write_text(
        json.dumps({'date': yesterday, 'counts': {'study': 9}}), 'utf-8')
    assert items.load_usage()[1] == {}
    # 跨天后继续记，从 0 开始，不会接着昨天的 9
    assert items.record_use('study') == {'study': 1}


def test_usage_tolerates_corrupt_file(runs_dir):
    items.USAGE_FILE.write_text('{not json', 'utf-8')
    assert items.load_usage()[1] == {}


# ---------------------------------------------------------------- 额度判断
def test_can_use_respects_switch_and_zero():
    assert items.can_use(ItemsConfig(enabled=False, study_per_day=1), 'study')[0] is False
    assert items.can_use(ItemsConfig(enabled=True, study_per_day=0), 'study')[0] is False


def test_can_use_respects_daily_limit(runs_dir):
    cfg = ItemsConfig(enabled=True, study_per_day=2)
    assert items.can_use(cfg, 'study')[0] is True
    items.record_use('study')
    items.record_use('study')
    ok, why = items.can_use(cfg, 'study')
    assert ok is False
    assert '2/2' in why
    # 学习用满了不影响打工的额度（两边账分开）
    assert items.can_use(ItemsConfig(enabled=True, work_per_day=2), 'work')[0] is True


def test_use_up_means_no_daily_cap(runs_dir):
    cfg = ItemsConfig(enabled=True, study_per_day=-1)
    for _ in range(30):
        items.record_use('study')
    assert items.can_use(cfg, 'study')[0] is True


@pytest.mark.parametrize('kind,limit', [('study', 3), ('work', 7)])
def test_daily_limit_reads_the_right_field(kind, limit):
    cfg = ItemsConfig(enabled=True, study_per_day=3, work_per_day=7)
    assert items.daily_limit(cfg, kind) == limit
