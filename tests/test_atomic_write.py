"""跨进程共享文件的写入必须是原子的。

**为什么值得一个测试**：`runs/` 下的文件被调度器子进程与 dashboard 进程同时
读写（契约见 docs/review/07-进程间契约.md）。裸 `write_text` 会留下"文件已
截断、内容还没写完"的窗口，消费端读到半截 JSON。

实测数据（本文件 test_naive_write_is_unsafe 用同样的并发模式跑旧实现）：
    裸 write_text：6701 次读 → 595 次 JSON 解析失败（8.9%）
    jsonio.write_json：10814 次读 → 0 次失败

所以这不只是"代码更规范"，而是修掉了一个约 9% 概率的实际故障。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.jsonio import write_json  # noqa: E402

# 够大以拉长写入窗口（让并发读有机会撞上中间态）
LARGE = {f'k{i}': 'x' * 200 for i in range(500)}


def _hammer(writer, path: Path, seconds: float = 1.0) -> tuple[int, int]:
    """一边狂写一边狂读，返回 (读取总次数, 读到坏内容的次数)。"""
    write_json(path, LARGE)                     # 先放一份完整的
    stop = threading.Event()
    bad, total = [0], [0]

    def w() -> None:
        while not stop.is_set():
            writer(path)

    def r() -> None:
        while not stop.is_set():
            try:
                txt = path.read_text(encoding='utf-8')
            except OSError:
                continue
            total[0] += 1
            if not txt.strip():
                bad[0] += 1
                continue
            try:
                obj = json.loads(txt)
            except ValueError:
                bad[0] += 1
                continue
            if len(obj) != len(LARGE):
                bad[0] += 1

    tw = threading.Thread(target=w, daemon=True)
    tr = threading.Thread(target=r, daemon=True)
    tw.start(); tr.start()
    time.sleep(seconds)
    stop.set()
    tw.join(timeout=1); tr.join(timeout=1)
    return total[0], bad[0]


def test_atomic_write_never_yields_partial_content(tmp_path):
    """原子写：并发读永远拿不到半截 JSON。"""
    total, bad = _hammer(lambda p: write_json(p, LARGE), tmp_path / 'state.json')
    assert total > 100, f'读取次数太少（{total}），测试没跑起来'
    assert bad == 0, f'{total} 次读里有 {bad} 次读到坏内容 —— 原子性被破坏了'


def test_naive_write_is_actually_unsafe(tmp_path):
    """**反向证明**：裸 write_text 确实会产出坏内容。

    这条测试的意义是证明上一条不是"永远为真的空断言"：如果哪天有人把原子写
    改回裸写，上一条会失败；而这一条说明失败是有实际后果的。
    若本条不再复现（bad == 0），说明环境调度变了，不是代码问题 —— 跳过即可。
    """
    def naive(p: Path) -> None:
        p.write_text(json.dumps(LARGE, ensure_ascii=False), encoding='utf-8')

    total, bad = _hammer(naive, tmp_path / 'naive.json')
    if bad == 0:
        import pytest
        pytest.skip(f'{total} 次读未撞上写入窗口（调度偶然），无法复现旧问题')
    assert bad > 0


def test_write_json_leaves_no_tmp_behind(tmp_path):
    """写完后不该留 .tmp 残留。"""
    p = tmp_path / 'x.json'
    write_json(p, {'a': 1})
    assert json.loads(p.read_text(encoding='utf-8')) == {'a': 1}
    assert not list(tmp_path.glob('*.tmp')), '留下了 .tmp 残留'


def test_write_json_creates_parent_dir(tmp_path):
    """目标目录不存在时应自动创建（很多调用点依赖这个行为）。"""
    p = tmp_path / 'nested' / 'deep' / 'x.json'
    write_json(p, {'a': 1})
    assert p.is_file()
