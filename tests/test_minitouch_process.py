from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src import u2dev


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_minitouch_adb_uses_windows_creation_flag_only_on_windows(monkeypatch, platform):
    command = ["/bundled/adb", "-s", "offline-device", "shell", "getprop"]
    result = subprocess.CompletedProcess(command, 0, stdout="test-value", stderr="")
    run = Mock(return_value=result)
    process_module = SimpleNamespace(run=run)
    if platform == "win32":
        process_module.CREATE_NO_WINDOW = 0x08000000
    monkeypatch.setattr(u2dev, "subprocess", process_module)
    monkeypatch.setattr(u2dev, "sys", SimpleNamespace(platform=platform))
    session = u2dev.MiniTouchSession.__new__(u2dev.MiniTouchSession)
    session.dev = SimpleNamespace(
        adb=SimpleNamespace(adb="/bundled/adb", serial="offline-device"),
    )

    assert session._adb("shell", "getprop", timeout=7) is result

    expected = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 7,
    }
    if platform == "win32":
        expected["creationflags"] = 0x08000000
    run.assert_called_once_with(command, **expected)
