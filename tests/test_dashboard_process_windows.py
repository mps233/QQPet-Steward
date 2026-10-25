from __future__ import annotations

import io
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

import dashboard
from src import runtime_process


def test_dashboard_console_flag_is_windows_only():
    expected = runtime_process.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    assert dashboard._NO_WINDOW == expected


@pytest.fixture(params=[0, runtime_process.CREATE_NO_WINDOW], ids=["posix", "windows"])
def commands(monkeypatch, request):
    calls = []
    flag = request.param
    monkeypatch.setattr(dashboard, "_NO_WINDOW", flag)
    monkeypatch.setattr(dashboard, "_adb_settings", lambda: ("configured-adb", "test-device"))
    monkeypatch.setattr(dashboard, "_resolve_adb", lambda _configured: ("adb.exe", ""))
    monkeypatch.setattr(dashboard, "_shot_cache", {"ts": 0.0, "data": b"", "at": "", "source": None})
    monkeypatch.setattr(dashboard, "_SYNC", {"busy": False})

    def install(result=None, error=None):
        def run(argv, **kwargs):
            assert kwargs["creationflags"] == flag
            assert kwargs["capture_output"] is True
            calls.append((argv, kwargs))
            if error is not None:
                raise error
            return result
        monkeypatch.setattr(dashboard.subprocess, "run", run)
        return calls

    return install


def test_phone_screenshot_stays_binary_resizes_and_reuses_cache(commands):
    png = io.BytesIO()
    Image.new("RGB", (800, 400), "blue").save(png, format="PNG")
    calls = commands(SimpleNamespace(returncode=0, stdout=png.getvalue(), stderr=b""))
    jpeg, at, cached = dashboard.capture_phone()
    assert cached is False and at
    assert Image.open(io.BytesIO(jpeg)).size == (390, 195)
    assert calls[0][0] == ["adb.exe", "-s", "test-device", "exec-out", "screencap", "-p"]
    assert calls[0][1]["timeout"] == 30
    assert "text" not in calls[0][1] and "encoding" not in calls[0][1]
    assert dashboard.capture_phone() == (jpeg, at, True)
    assert len(calls) == 1


def test_phone_screenshot_preserves_adb_error_details(commands):
    commands(SimpleNamespace(returncode=1, stdout=b"", stderr=b"device offline"))
    with pytest.raises(RuntimeError, match="device offline"):
        dashboard.capture_phone()


def test_device_detection_keeps_text_parsing_and_timeout(commands):
    calls = commands(SimpleNamespace(
        returncode=0,
        stdout="List of devices attached\nserial-1 device product:pet model:Pixel_9 transport_id:1\n",
        stderr="",
    ))
    state = dashboard.adb_info()
    assert state["error"] == ""
    assert state["devices"] == [{
        "serial": "serial-1", "state": "device", "model": "Pixel 9", "product": "pet",
    }]
    assert calls[0][0] == ["adb.exe", "devices", "-l"]
    assert calls[0][1]["text"] is True and calls[0][1]["timeout"] == 10


def test_device_connection_keeps_normalized_address_and_result(commands):
    calls = commands(SimpleNamespace(
        returncode=0, stdout="connected to 192.0.2.44:5555\n", stderr="",
    ))
    state = dashboard.adb_connect("192.0.2.44")
    assert state["ok"] is True and state["addr"] == "192.0.2.44:5555"
    assert calls[0][0] == ["adb.exe", "connect", "192.0.2.44:5555"]
    assert calls[0][1]["text"] is True and calls[0][1]["timeout"] == 20


def test_career_sync_keeps_json_output_working_directory_and_busy_cleanup(commands, monkeypatch):
    monkeypatch.setattr(
        dashboard.runtime_process, "build_career_sync_argv",
        lambda _base: ["runtime.exe", "career-sync"],
    )
    calls = commands(SimpleNamespace(
        returncode=0, stdout='startup log\n{"ok":true,"points":42}\n', stderr="",
    ))
    assert dashboard.career_sync() == {"ok": True, "points": 42}
    assert calls[0][0] == ["runtime.exe", "career-sync"]
    assert calls[0][1]["cwd"] == str(dashboard.APP_ROOT)
    assert calls[0][1]["text"] is True and calls[0][1]["timeout"] == 150
    assert dashboard._SYNC["busy"] is False


@pytest.mark.parametrize("operation, timeout", [
    ("devices", 10), ("connect", 20), ("career", 150),
])
def test_command_timeouts_preserve_existing_error_responses(commands, monkeypatch, operation, timeout):
    commands(error=subprocess.TimeoutExpired(["test-command"], timeout))
    if operation == "devices":
        state = dashboard.adb_info()
        assert state["devices"] == [] and "10s" in state["error"]
    elif operation == "connect":
        state = dashboard.adb_connect("192.0.2.44")
        assert state["ok"] is False and "20s" in state["msg"]
    else:
        monkeypatch.setattr(dashboard.runtime_process, "build_career_sync_argv", lambda _base: ["sync"])
        state = dashboard.career_sync()
        assert state["ok"] is False and "150" in state["reason"]
        assert dashboard._SYNC["busy"] is False


def test_legacy_uptime_fallback_also_hides_console(commands, monkeypatch):
    monkeypatch.setattr(dashboard, "_runner_pids", lambda: [999])
    monkeypatch.setattr(dashboard.instance, "read_pidfile", lambda _path: None)
    calls = commands(SimpleNamespace(returncode=0, stdout="01:23\n", stderr=""))
    assert dashboard.scheduler_info() == {"alive": True, "pid": 999, "uptime": "01:23"}
    assert calls[0][0] == ["ps", "-o", "etime=", "-p", "999"]
    assert calls[0][1]["text"] is True and calls[0][1]["timeout"] == 5
