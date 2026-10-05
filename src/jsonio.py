"""跨进程共享 JSON 文件的统一读写：默认原子写。

**为什么需要它**：`runs/` 下的文件被调度器子进程与 dashboard 进程同时读写
（契约清单见 docs/review/07-进程间契约.md）。裸 `write_text(json.dumps(...))`
在内核里是 `open(truncate)` + `write`，中间存在"文件已被截断、内容还没写完"的
窗口 —— 消费端恰好此时读，会拿到空文件或半截 JSON。虽然 read_json 有兜底不会崩，
但界面会闪现"队列空了"。实测有 7 处这样的写入。

原子写 = 先写同目录 `<name>.tmp` 再 `os.replace()`。POSIX 下同一文件系统内的
rename 是原子的，所以消费端要么看到旧内容、要么看到新内容，不会看到半截。

**本模块刻意不 import src.config**：`src/queue_status.py` 的
`from .config import PROJECT_ROOT` 会连带拉起 yaml；而 dashboard 的兜底写入
（`mark_queue_stopped`）需要在最小环境下也能工作。所以路径判断就地完成。

用法：
    from src.jsonio import write_json, read_json, append_jsonl
    write_json(QUEUE_STATUS_FILE, state)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any, *, indent: int | None = None) -> None:
    """原子写 JSON：先写同目录 .tmp，再 os.replace 覆盖。

    注意：
    - tmp 必须与目标**同目录**（同文件系统），否则 os.replace 会退化成复制+删除，
      失去原子性。
    - 固定 `.tmp` 后缀而不是加时间戳：并发写同一文件时不会堆积多个临时文件；
      本项目每个文件都是单写者（调度器写、dashboard 读，唯一例外是
      queue_status.json，见 07 文档），固定后缀够用。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=indent), encoding='utf-8')
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    """读 JSON；不存在/解析失败返回 default（不抛）。

    消费端（dashboard）需要这个语义：文件损坏或正在被写时不该让整个页面挂掉。
    """
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def append_jsonl(path: Path, record: dict) -> None:
    """追加一行 JSON（事件流，如 session_rewards.jsonl）。

    单次 write 在 POSIX 下对以 append 模式打开的文件是原子的（小于 PIPE_BUF），
    所以跨进程不会交错。**前提是一次写入包含整行**（含换行），不要分多次 write。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + '\n'
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(line)


def write_text_atomic(path: Path, text: str) -> None:
    """原子写纯文本（信号文件等单值场景）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)
