import ast
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.config import NotifyConfig
from src import notification_outbox as outbox


def cfg():
    return NotifyConfig(win_toast=False, telegram_enabled=True,
                        telegram_token='test-token', telegram_chat_id='test-chat')


def test_failed_event_retries_with_backoff_and_survives_restart(monkeypatch):
    send = Mock(side_effect=[{'ok': False, 'results': {'Telegram': 'failed'}},
                             {'ok': True, 'results': {'Telegram': 'success'}}])
    monkeypatch.setattr(outbox, 'send_detailed', send)
    outbox.enqueue('career:test', 'career', 'title', 'original body')
    assert not outbox.flush(cfg(), now=100)
    assert not outbox.delivered_at('career:test')
    assert not outbox.flush(cfg(), now=159)
    outbox.enqueue('career:test', 'career', 'title', 'replacement body')
    assert outbox.flush(cfg(), now=160)['career:test']
    assert outbox.delivered_at('career:test')
    outbox.flush(cfg(), now=1000)
    assert send.call_count == 2
    assert send.call_args.args[1] == 'original body'


@pytest.mark.parametrize('kind, disabled', [
    ('career', {'career_notify': False}),
    ('quota', {'event_notify': False}),
    ('quota', {'quota_done': False}),
])
def test_disabled_event_waits_without_attempting(monkeypatch, kind, disabled):
    send = Mock(side_effect=AssertionError('Disabled event must not send'))
    monkeypatch.setattr(outbox, 'send_detailed', send)
    config = cfg()
    for key, value in disabled.items():
        setattr(config, key, value)
    outbox.enqueue('test', kind, 'title', 'body')
    assert not outbox.flush(config, now=100)
    assert outbox._load()['events']['test']['attempts'] == 0


def test_yesterdays_quota_is_not_sent_today(monkeypatch):
    send = Mock(side_effect=AssertionError('Expired quota must not send'))
    monkeypatch.setattr(outbox, 'send_detailed', send)
    outbox.enqueue('quota:old', 'quota', 'title', 'body', expires_at=100)
    assert not outbox.flush(cfg(), now=100)


def test_all_channels_disabled_do_not_consume_attempts(monkeypatch):
    monkeypatch.setattr(outbox, 'send_detailed', Mock(side_effect=AssertionError('No channels')))
    outbox.enqueue('test', 'career', 'title', 'body')
    outbox.flush(NotifyConfig(win_toast=False), now=100)
    assert outbox._load()['events']['test']['attempts'] == 0


def test_send_exception_does_not_break_retry_state(monkeypatch):
    monkeypatch.setattr(outbox, 'send_detailed', Mock(side_effect=RuntimeError('offline')))
    outbox.enqueue('test', 'career', 'title', 'body')
    assert not outbox.flush(cfg(), now=100)
    assert outbox._load()['events']['test']['attempts'] == 1
    assert outbox._load()['events']['test']['next_attempt'] == 160


def test_corrupted_outbox_is_not_overwritten_or_resent(monkeypatch):
    outbox.OUTBOX_FILE.write_text('broken')
    with pytest.raises(ValueError):
        outbox.enqueue('test', 'career', 'title', 'body')
    assert outbox.OUTBOX_FILE.read_text() == 'broken'


def runner_method(name, **globals_):
    """把 runner 里某个方法摘出来单独执行（不构造整个 Runner）。

    模块级的**字面量常量**一并注入：方法体里会用到它们（如汇总通知的
    `SUMMARY_TTL_HOUR`），漏注入就是在被测方法里抛 NameError —— 而这类
    异常会被方法自身的兜底 `except` 吞掉，测试表现为"什么都没发生"，
    排查成本很高。这里按源码真值注入，不手抄一份免得漂移。
    """
    source = Path(__file__).resolve().parents[1] / 'scenarios' / 'runner.py'
    tree = ast.parse(source.read_text())
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value
    method = next(fn for cls in tree.body if isinstance(cls, ast.ClassDef)
                  for fn in cls.body if isinstance(fn, ast.FunctionDef) and fn.name == name)
    namespace = dict(constants, date=date, datetime=datetime, timedelta=timedelta,
                     log=lambda *args: None, notification_outbox=outbox, **globals_)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


def test_existing_unnotified_career_event_is_retried(monkeypatch):
    store = {'events': [{'career': 'test', 'name': 'name', 'notified_at': None,
                         'attrs': {}, 'stopped': False}]}
    def save(data):
        store.clear()
        store.update(deepcopy(data))
    method = runner_method('_career_on_unlock', load_unlock_data=lambda: deepcopy(store),
                           save_unlock_data=save)
    monkeypatch.setattr(outbox, 'delivered_at', lambda key: '2026-10-08 12:00:00')
    send = Mock(side_effect=[False, True])
    runner = SimpleNamespace(_last_cfg=SimpleNamespace(career=SimpleNamespace(stop_study_on_unlock=False)),
                             _notify_career_unlock=send)
    method(runner, {'test': 'name'}, {}, {})
    method(runner, {'test': 'name'}, {}, {})
    assert send.call_count == 2
    assert store['events'][0]['notified_at'] == '2026-10-08 12:00:00'


def test_daily_summary_is_not_requeued_after_delivery(monkeypatch):
    """已送达的收工总账不再重复入队（幂等靠 outbox 的 daily:* key）。"""
    method = runner_method('_notify_daily_summary')
    monkeypatch.setattr(outbox, 'delivered_at', lambda key: 'delivered')
    enqueue = Mock(side_effect=AssertionError('Delivered summary must not re-enqueue'))
    monkeypatch.setattr(outbox, 'enqueue', enqueue)
    runner = SimpleNamespace(_last_cfg=SimpleNamespace(notify=cfg()),
                             _retry_notifications=lambda: None)
    method(runner, 'daily:work:2026-10-08', 'title', 'body')
    assert enqueue.call_count == 0


def test_daily_summary_respects_quota_done_switch(monkeypatch):
    """收工总账与冒险日报受同一个开关控制（关掉后不入队、不发送）。"""
    method = runner_method('_notify_daily_summary')
    enqueue = Mock(side_effect=AssertionError('Disabled summary must not enqueue'))
    monkeypatch.setattr(outbox, 'enqueue', enqueue)
    runner = SimpleNamespace(
        _last_cfg=SimpleNamespace(notify=NotifyConfig(quota_done=False)),
        _retry_notifications=lambda: None)
    method(runner, 'daily:work:2026-10-08', 'title', 'body')
    assert enqueue.call_count == 0


def test_daily_summary_queues_with_retry_expiry(monkeypatch):
    """未送达的汇总进队列，且存活期留到次日中午（冒险日报 23:59 才入队）。"""
    method = runner_method('_notify_daily_summary')
    runner = SimpleNamespace(_last_cfg=SimpleNamespace(notify=cfg()),
                             _retry_notifications=lambda: None)
    method(runner, 'daily:adventure:2026-10-08', 'title', 'body')
    event = outbox._load()['events']['daily:adventure:2026-10-08']
    assert event['kind'] == 'daily'
    assert event['expires_at'] > datetime.now().timestamp()


def _task(enabled=True):
    return SimpleNamespace(cfg=SimpleNamespace(enabled=enabled))


def _finished_check(durations, done=True, school_on=True, work_on=True):
    method = runner_method('_study_work_finished')
    runner = SimpleNamespace(_load_durations=lambda: durations,
                             _task_done_today=lambda key, tasks: done)
    return method(runner, {'school': _task(school_on), 'work': _task(work_on)})


@pytest.mark.parametrize('durations, done, school_on, work_on, expected', [
    ((7 * 3600, 5 * 3600), True, True, True, True),      # 正常收工
    ((0, 0), True, True, True, False),                  # 深夜启动：别发 0 小时空账
    ((7 * 3600, 0), True, True, True, True),            # 只有学习
    ((7 * 3600, 0), True, True, False, True),           # 只开了学习
    ((7 * 3600, 5 * 3600), False, True, True, False),   # 还没跑完
    ((7 * 3600, 5 * 3600), True, False, False, False),  # 两项都关：没有账可报
])
def test_study_work_finished_requires_actual_running_time(
        monkeypatch, durations, done, school_on, work_on, expected):
    assert _finished_check(durations, done, school_on, work_on) is expected


def test_background_sender_does_not_block_scheduler_file_operations(monkeypatch):
    started, release = threading.Event(), threading.Event()
    def send(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return {'ok': True, 'results': {}}
    monkeypatch.setattr(outbox, 'send_detailed', send)
    outbox.enqueue('first', 'career', 'title', 'body')
    outbox.start_flush(cfg())
    assert started.wait(1)
    before = time.monotonic()
    assert outbox.contains('first')
    outbox.enqueue('second', 'career', 'title', 'body')
    assert time.monotonic() - before < 0.5
    release.set()
    outbox._WORKER.join(3)
    assert outbox.delivered_at('first')
    assert not outbox.delivered_at('second')
