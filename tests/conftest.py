"""pytest 公共配置与隔离夹具。

**为什么需要这个文件**：本项目所有进度/状态都写在 `runs/` 下（见
docs/review/07-进程间契约.md），而 `tools/test_state_freshness.py` 的注释里
记着一条历史教训——"测试时不要污染真实进度文件：涉及计数时把进度文件重定向
到临时目录"。

这里把这件事自动化：任何测试运行期间，各模块里指向 `runs/` 的文件常量都会被
重定向到 tmp_path。这样测试永远不会写到真实进度。

约定：
- 测试需要文件系统时用 `repo_root`（只读）或 `runs_dir`（可写的临时 runs/）。
- 新增模块若在模块级持有 `runs/` 下的 Path 常量，请把模块名加进 `_PATCH_MODULES`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 模块级持有 runs/ 路径常量的模块（import 后逐个 monkeypatch）
_PATCH_MODULES = (
    'src.progress',
    'src.progress_store',
    'src.queue_status',
    'src.status_cache',
    'src.instance',
    'src.hire_quota',
    'src.fatigue',
)


@pytest.fixture(autouse=True)
def isolate_runs(tmp_path, monkeypatch):
    """把各模块里指向真实 runs/ 的 Path 常量重定向到 tmp_path/runs。

    做法：import 目标模块，扫描其模块级大写属性，凡是值里含 'runs' 的 Path，
    替换为 `tmp_path/runs/<原文件名>`。这样不需要每个测试自己处理，
    也不会因为某个模块漏改而写坏真实数据。
    """
    fake_runs = tmp_path / 'runs'
    fake_runs.mkdir()

    for name in _PATCH_MODULES:
        try:
            mod = __import__(name, fromlist=['*'])
        except Exception:                      # noqa: BLE001
            # 模块不存在或导入失败（例如可选依赖缺失）不该让所有测试失败
            continue
        for attr in dir(mod):
            if not (attr.isupper() and isinstance(getattr(mod, attr, None), Path)):
                continue
            original = getattr(mod, attr)
            if 'runs' not in original.parts:
                continue
            monkeypatch.setattr(mod, attr, fake_runs / original.name, raising=False)

    yield fake_runs


@pytest.fixture
def repo_root() -> Path:
    """仓库根（只读用途——读取源码/文档做断言）。"""
    return ROOT


@pytest.fixture
def runs_dir(isolate_runs) -> Path:
    """隔离后的 runs/ 目录（可写）。"""
    return isolate_runs
