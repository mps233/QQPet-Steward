"""摸摸任务的接线单测：任务键、默认顺序、配置读写。"""
from __future__ import annotations

import dashboard
import src.settings as S
from scenarios.runner import SIDE_TASK_KEYS, TASK_NAMES
from src.config import TASK_KEYS, TasksConfig, load_config


def test_touch_is_a_queue_task():
    assert 'touch' in TASK_KEYS
    assert TASK_NAMES['touch'] == '摸摸'
    assert 'touch' in SIDE_TASK_KEYS
    assert 'touch' in TasksConfig().order.split('>')


def test_load_config_touch(tmp_path):
    p = tmp_path / 'config.yaml'
    p.write_text(
        'touch:\n'
        '  pet_seconds: 5\n'
        'tasks:\n'
        '  order: care>touch>work\n'
        '  touch:\n'
        '    enabled: true\n',
        encoding='utf-8',
    )
    cfg = load_config(p)
    assert cfg.touch.pet_seconds == 5
    assert cfg.tasks.touch.enabled is True
    assert cfg.tasks.order == 'care>touch>work'


def test_dashboard_apply_touch_settings(monkeypatch):
    fake: dict = {}
    monkeypatch.setattr(S, 'load_raw', lambda: fake)
    monkeypatch.setattr(S, 'save_raw', lambda data: None)
    res = dashboard.apply_settings({'touch_enabled': True, 'touch_seconds': 5})
    assert res['ok'], res
    assert fake['tasks']['touch']['enabled'] is True
    assert fake['touch']['pet_seconds'] == 5


def test_enabling_touch_appends_it_to_existing_order(monkeypatch):
    # 老 config 的 order 没有 touch：设置页开关打开时必须自动补进 order，
    # 否则 runner 只按 order 建队列，任务永远不跑。
    fake = {'tasks': {'order': 'care>work'}}
    monkeypatch.setattr(S, 'load_raw', lambda: fake)
    monkeypatch.setattr(S, 'save_raw', lambda data: None)
    res = dashboard.apply_settings({'touch_enabled': True})
    assert res['ok'], res
    assert fake['tasks']['order'] == 'care>work>touch'


def test_dashboard_rejects_bad_touch_seconds(monkeypatch):
    fake: dict = {}
    monkeypatch.setattr(S, 'load_raw', lambda: fake)
    monkeypatch.setattr(S, 'save_raw', lambda data: None)
    res = dashboard.apply_settings({'touch_seconds': 0})
    assert not res['ok']
    assert fake == {}


def test_editable_snapshot_touch(monkeypatch, tmp_path):
    (tmp_path / 'config.yaml').write_text(
        'tasks:\n'
        '  order: care>touch>work\n'
        '  touch:\n'
        '    enabled: true\n'
        'touch:\n'
        '  pet_seconds: 4\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(dashboard, 'APP_ROOT', tmp_path)
    ed = dashboard.editable_snapshot()
    assert ed['touch_enabled'] is True
    assert ed['touch_seconds'] == 4


def test_editable_snapshot_touch_hidden_when_not_in_order(monkeypatch, tmp_path):
    (tmp_path / 'config.yaml').write_text(
        'tasks:\n'
        '  order: care>work\n'
        '  touch:\n'
        '    enabled: true\n'
        'touch:\n'
        '  pet_seconds: 3\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(dashboard, 'APP_ROOT', tmp_path)
    ed = dashboard.editable_snapshot()
    # order 里没有 touch = runner 不会调度，设置页也不该显示成"已启用"
    assert ed['touch_enabled'] is False


def test_alt_preset_order_is_valid_and_includes_touch():
    from src.presets import ALT_TASK_ORDER
    ok, fixed = S.validate_field('tasks.order', ALT_TASK_ORDER)
    assert ok
    assert 'touch' in fixed.split('>')
    # 后加的任务也要在预设 order 里，否则应用 alt 预设后它会静默停摆
    assert 'daily_gift' in fixed.split('>')
    assert 'friend_care' not in fixed.split('>')