"""调度与显示必须用同一份"今日完成"口径。

背景（docs/review/06-后端架构拆分.md B/G 节）：这个判断原先有两份实现，口径还不一致 ——
调度侧 `_main_finished()` 算了 `_dead(tasks, key)`，而**显示侧**
`_write_queue_status()` 内联的那份漏了它。后果是哑火：场景当天被标记 dead 后，
调度器不再执行，界面却仍把它算作"待执行/等待中"，用户看到"该动却一直不动"。

现在两边都走 `TaskQueueRunner._task_done_today()`，本文件锁住这个事实：
  ① `_task_done_today` 必须存在且被 `_write_queue_status` 调用；
  ② 源码里不允许再出现第二份内联的配额判定。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope='module')
def runner_src() -> str:
    return (ROOT / 'scenarios' / 'runner.py').read_text(encoding='utf-8')


def _fn_body(src: str, name: str) -> str:
    """取函数体，**并剥掉 docstring**。

    为什么必须剥：docstring 里会提到被断言的关键字（比如说明"这里原先漏了 _dead"），
    直接对整段断言会被自己的注释满足 —— 测试就永远为真了。
    （这是本文件第一版真实踩到的坑：删掉 `_dead` 判断后测试仍通过。）
    """
    body = src.split(f'def {name}(', 1)[1].split('\n    def ', 1)[0]
    # 去掉最前面的 docstring（三引号块）
    parts = body.split('"""')
    if len(parts) >= 3:
        body = parts[0] + '"""'.join(parts[2:])
    return body


def test_task_done_today_exists(runner_src):
    """单一判定入口存在。"""
    assert 'def _task_done_today(' in runner_src


def test_task_done_today_checks_dead(runner_src):
    """判定里必须包含 _dead 的实际调用 —— 这正是旧显示口径漏掉的那一项（哑火根因）。"""
    body = _fn_body(runner_src, '_task_done_today')
    assert '_dead(tasks, key)' in body, (
        '_task_done_today 丢了 _dead 判断 —— 显示与调度会再次不一致')


def test_write_queue_status_uses_single_decision(runner_src):
    """_write_queue_status 必须调用单一判定，而不是自己内联一份。"""
    body = _fn_body(runner_src, '_write_queue_status')
    assert 'self._task_done_today(' in body, (
        '_write_queue_status 没有走单一判定 —— 又会分叉出第二份口径')


def test_no_duplicate_inline_quota_logic(runner_src):
    """源码里不该再有内联的配额判定块。

    旧实现在 _write_queue_status 里逐任务写了 if/elif 链，特征串是
    `quota_done = (self._fatigue_skip(...)`。收敛后这个模式应当消失。
    """
    assert 'quota_done = (self._fatigue_skip' not in runner_src, (
        '仍存在内联的配额判定 —— 口径会再次漂移')


def test_main_finished_still_uses_shared_pieces(runner_src):
    """_main_finished 保留原有语义（整组完成），且仍复用 _adventure_done/_hire_friend_done。"""
    body = _fn_body(runner_src, '_main_finished')
    assert '_adventure_done(' in body
    assert '_hire_friend_done(' in body
    assert '_dead(tasks,' in body
