from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    import desktop.windows.scheduler_launcher as module

    data = tmp_path / "user-data"

    class FakeConfig:
        APP_ROOT = Path("/bundle/app")
        PROJECT_ROOT = Path("/bundle/project")
        CONFIG_FILE = Path("/bundle/config.yaml")
        RESOURCE_ROOT = Path("/bundle")

    monkeypatch.setitem(sys.modules, "src.config", FakeConfig)
    monkeypatch.setenv("QQPET_DATA_DIR", str(data))
    # Reload because data_root is deliberately resolved at import time.
    module = importlib.reload(module)
    return module, FakeConfig, data


def test_frozen_scheduler_launcher_sets_roots_and_main_namespace(launcher, monkeypatch):
    module, fake_config, data = launcher
    calls = {}

    def fake_run_module(name, *, run_name, alter_sys):
        calls.update(name=name, run_name=run_name, alter_sys=alter_sys)

    monkeypatch.setattr(module.runpy, "run_module", fake_run_module)
    monkeypatch.setattr(sys, "argv", ["QQPetCopilotScheduler.exe", "--test", "coins"])
    assert module.main() == 0
    assert calls == {
        "name": "scenarios.runner",
        "run_name": "__main__",
        "alter_sys": True,
    }
    assert fake_config.APP_ROOT == data
    assert fake_config.PROJECT_ROOT == data
    assert fake_config.CONFIG_FILE == data / "config.yaml"
    assert (data / "runs").is_dir()


def test_frozen_scheduler_launcher_forwards_arguments_and_exit_code(launcher, monkeypatch):
    module, _fake_config, _data = launcher
    seen = {}

    def fake_run_module(_name, *, run_name, alter_sys):
        assert run_name == "__main__"
        assert alter_sys is True
        seen["argv"] = list(sys.argv)
        raise SystemExit(2)

    monkeypatch.setattr(module.runpy, "run_module", fake_run_module)
    assert module.main(["--emulator"]) == 2
    assert seen["argv"][1:] == ["--emulator"]


def test_frozen_scheduler_launcher_keeps_resource_root(launcher, monkeypatch):
    module, fake_config, _data = launcher
    resource_root = Path("/bundle")
    monkeypatch.setattr(module.runpy, "run_module", lambda *a, **k: None)
    module.main([])
    assert fake_config.RESOURCE_ROOT == resource_root


@pytest.mark.parametrize(("code", "expected"), [(None, 0), (7, 7)])
def test_frozen_scheduler_launcher_exit_codes(launcher, monkeypatch, code, expected):
    module, _fake_config, _data = launcher

    def failed(*_args, **_kwargs):
        raise SystemExit(code)

    monkeypatch.setattr(module.runpy, "run_module", failed)
    assert module.main([]) == expected
