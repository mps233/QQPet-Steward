from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from desktop.windows import verify_native as native


def test_backend_pid_file_accepts_only_positive_integer_records(tmp_path):
    record = tmp_path / "dashboard.pid"
    for payload, expected in [(123, 123), ({"pid": 456}, 456), (True, None),
                              ({"pid": "123"}, None), (-1, None), ({}, None)]:
        record.write_text(json.dumps(payload), encoding="utf-8")
        assert native._pid_file(record) == expected
    record.write_text("invalid JSON", encoding="utf-8")
    assert native._pid_file(record) is None


@pytest.mark.parametrize("replacement", [
    {"parent": 999}, {"image": Path("/other/backend.exe")}, {"created": 99},
])
def test_backend_cleanup_rejects_other_parent_image_or_old_process(tmp_path, replacement):
    executable = tmp_path / "QQPetCopilot.exe"
    record = {"pid": 23, "parent": 12, "image": executable.resolve(), "created": 101}
    native._validate_backend(record, 12, executable, 100)
    with pytest.raises(native.portable.VerificationError, match="refusing"):
        native._validate_backend({**record, **replacement}, 12, executable, 100)


def test_dashboard_page_selection_requires_the_isolated_origin():
    good = SimpleNamespace(url="http://127.0.0.1:12345/?desktop=1")
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[
        SimpleNamespace(url="tauri://localhost/index.html"),
        SimpleNamespace(url="http://127.0.0.1:8787/"),
        SimpleNamespace(url="http://127.0.0.1.evil:12345/"), good,
    ])])
    assert native._dashboard_page(browser, "http://127.0.0.1:12345/") is good


class FakeProcess:
    def __init__(self, pid, secondary=False):
        self.pid = pid
        self.stopped = secondary
        self.terminated = 0

    def poll(self):
        return 0 if self.stopped else None

    def wait(self, timeout):
        return self.poll()

    def terminate(self):
        self.stopped = True
        self.terminated += 1


class FakeDesktop:
    def __init__(self):
        self.shown = True
        self.backend_alive = True
        self.claims = []
        self.cleaned = []
        self.external_browser = False
        self.webview_child = True

    def browser_windows(self):
        return {777} if self.external_browser else set()

    def windows(self, pid=None, *, parent=None):
        if parent is not None:
            return [{"class": "Chrome_WidgetWin_0" if self.webview_child else "Other"}]
        return [{"hwnd": 100, "pid": pid, "title": native.TITLE,
                 "visible": self.shown, "class": "TauriWindow"}]

    def claim_backend(self, pid, gui_pid, executable, started):
        self.claims.append((pid, gui_pid, executable))
        return {"pid": pid, "handle": "owned-handle"}

    def close_window(self, hwnd):
        assert hwnd == 100
        self.shown = False

    def visible(self, hwnd):
        assert hwnd == 100
        return self.shown

    def alive(self, backend):
        assert backend["handle"] == "owned-handle"
        return self.backend_alive

    def cleanup_backend(self, backend):
        self.cleaned.append(backend["pid"])
        self.backend_alive = False

    def cleanup_gui(self, process):
        native.portable._stop_process(process)


def harness(tmp_path, monkeypatch, *, login=True, restore=True, different_backend=False):
    bundled = tmp_path / "backend"
    bundled.mkdir()
    (bundled / ".build-fingerprint").write_text("a" * 64 + "\n", encoding="ascii")
    for name in native.portable.EXECUTABLES:
        (bundled / name).write_bytes(f"bundled {name}".encode())
    desktop = FakeDesktop()
    children, environments, routes = [], [], []
    page = SimpleNamespace(url="")
    task_state = {"status": 200 if login else 401, "schedulerAlive": False, "schedulerPid": None,
                  "taskRows": 7, "school": True, "work": True}
    page.wait_for_selector = lambda selector, **kwargs: None
    page.evaluate = lambda script: task_state

    def popen(command, **kwargs):
        environments.append(kwargs["env"])
        assert command == [str(tmp_path / "QQPet-Steward.exe")]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["QQPET_DISABLE_TG"] == "1"
        assert kwargs["env"]["QQPET_TRANSIENT"] == "0"
        assert kwargs["env"]["QQPET_RUNTIME_STORE"] == str(tmp_path / "runtime-cache")
        assert kwargs["env"]["QQPET_SOURCE_ROOT"] == str(tmp_path / "no-source")
        assert kwargs["env"]["WEBVIEW2_USER_DATA_FOLDER"] == str(tmp_path / "webview-data")
        pid_file = tmp_path / "data" / "runs" / "dashboard.pid"
        if not children:
            installed = tmp_path / "runtime-cache" / ("a" * 64)
            shutil.copytree(bundled, installed)
            (installed / ".ready").write_text("ready", encoding="ascii")
            pid_file.write_text('{"pid":23}', encoding="utf-8")
            page.url = kwargs["env"]["QQPET_SERVER"]
            child = FakeProcess(12)
        else:
            desktop.shown = restore
            if different_backend:
                pid_file.write_text('{"pid":24}', encoding="utf-8")
            child = FakeProcess(13, secondary=True)
        children.append(child)
        return child

    def request(opener, base, path):
        routes.append(path)
        return (401 if path == "/api/data" else 200), {}, b"{}"

    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])

    class Playwright:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda *a, **k: browser))

        def __exit__(self, *args):
            pass

    real_wait = native._wait

    @contextmanager
    def no_host_policy(**_kwargs):
        yield {"policyApplied": False, "elevated": False, "policyScope": "QQPet-Steward.exe"}

    monkeypatch.setattr(native, "webview_test_environment", no_host_policy)
    monkeypatch.setattr(native, "_wait", lambda callback, timeout, message:
                        real_wait(callback, 0, message))
    monkeypatch.setattr(native.subprocess, "Popen", popen)
    monkeypatch.setattr(native.portable, "_request", request)
    return desktop, Playwright, children, environments, routes


def test_native_smoke_checks_embedded_login_close_and_single_instance_without_starting_tasks(
        tmp_path, monkeypatch):
    desktop, playwright, children, environments, routes = harness(tmp_path, monkeypatch)
    checks = native.check_app(tmp_path / "QQPet-Steward.exe", tmp_path, desktop, playwright)
    assert all(check["ok"] is True for check in checks)
    assert [check["name"] for check in checks] == [
        "backend-startup", "embedded-webview", "automatic-cookie-login",
        "close-to-tray", "single-instance-reopen", "external-browser-windows",
    ]
    assert checks[2]["schedulerStarted"] is False
    assert checks[0]["defaultMode"] is True
    assert checks[0]["verifiedExecutables"] == 2 and checks[0]["sha256Matched"] is True
    assert desktop.claims[0][2] == (
        tmp_path / "runtime-cache" / ("a" * 64) / "QQPetCopilot.exe")
    assert checks[4]["backendReused"] is True
    assert desktop.cleaned == [23]
    assert children[0].terminated == 1
    assert children[1].terminated == 0
    assert environments[0] == environments[1]
    assert "--remote-debugging-address=127.0.0.1" in (
        environments[0]["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"])
    assert all(path in {"/api/client/meta", "/api/data", "/json/version"} for path in routes)
    assert not (tmp_path / "data" / "runs" / "runner.pid").exists()


@pytest.mark.parametrize("options, message", [
    ({"login": False}, "login"),
    ({"restore": False}, "restore"),
    ({"different_backend": True}, "replaced"),
])
def test_native_smoke_failure_cleans_only_claimed_backend_and_created_gui(
        tmp_path, monkeypatch, options, message):
    desktop, playwright, children, _environments, _routes = harness(tmp_path, monkeypatch, **options)
    with pytest.raises(native.portable.VerificationError, match=message) as failure:
        native.check_app(tmp_path / "QQPet-Steward.exe", tmp_path, desktop, playwright)
    assert failure.value.diagnostics["stage"] in {"automatic-cookie-login", "single-instance-reopen"}
    assert desktop.cleaned == [23]
    assert children[0].terminated == 1


def test_native_smoke_requires_an_embedded_webview_child(tmp_path, monkeypatch):
    desktop, playwright, children, _environments, _routes = harness(tmp_path, monkeypatch)
    desktop.webview_child = False
    with pytest.raises(native.portable.VerificationError, match="WebView2 child"):
        native.check_app(tmp_path / "QQPet-Steward.exe", tmp_path, desktop, playwright)
    assert desktop.cleaned == [23] and children[0].terminated == 1


def test_native_smoke_rejects_a_new_system_browser_window(tmp_path, monkeypatch):
    desktop, playwright, children, _environments, _routes = harness(tmp_path, monkeypatch)
    snapshots = iter([set(), {777}])
    desktop.browser_windows = lambda: next(snapshots)
    with pytest.raises(native.portable.VerificationError, match="external browser"):
        native.check_app(tmp_path / "QQPet-Steward.exe", tmp_path, desktop, playwright)
    assert desktop.cleaned == [23] and children[0].terminated == 1


def test_native_smoke_reports_playwright_errors_and_still_cleans_processes(tmp_path, monkeypatch):
    desktop, playwright, children, _environments, _routes = harness(tmp_path, monkeypatch)

    class BrowserError(Exception):
        pass

    class FailingPlaywright:
        def __enter__(self):
            raise BrowserError("private protocol details")

        def __exit__(self, *_args):
            pass

    with pytest.raises(native.portable.VerificationError, match="BrowserError") as failure:
        native.check_app(tmp_path / "QQPet-Steward.exe", tmp_path, desktop, FailingPlaywright)
    assert "private protocol details" not in json.dumps(failure.value.diagnostics)
    assert desktop.cleaned == [23] and children[0].terminated == 1


def test_non_windows_cli_reports_block_without_launching_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(native.sys, "platform", "darwin")
    report = tmp_path / "report.json"
    assert native.main(["--executable", str(tmp_path / "app" / "app.exe"),
                        "--report", str(report)]) == 1
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["checks"] == [{"name": "native-windows", "ok": False,
                                "error": "native GUI verification must run on Windows"}]
    assert json.loads(capsys.readouterr().out) == result


def test_cli_refuses_to_write_report_into_native_app_directory(tmp_path):
    with pytest.raises(SystemExit):
        native.main(["--executable", str(tmp_path / "app.exe"),
                     "--report", str(tmp_path / "report.json")])


@pytest.mark.parametrize("corrupt", ["QQPetCopilot.exe", "QQPetCopilotScheduler.exe", ".ready"])
def test_cached_backend_check_rejects_changed_or_incomplete_installation(tmp_path, corrupt):
    bundled, installed = tmp_path / "backend", tmp_path / "cache"
    bundled.mkdir()
    (bundled / ".build-fingerprint").write_text("a" * 64 + "\n", encoding="ascii")
    for name in native.portable.EXECUTABLES:
        (bundled / name).write_bytes(b"original-executable")
    shutil.copytree(bundled, installed)
    (installed / ".ready").write_text("ready", encoding="ascii")
    assert native._check_cached_backend(bundled, installed)["verifiedExecutables"] == 2
    if corrupt == ".ready":
        (installed / corrupt).unlink()
    else:
        (installed / corrupt).write_bytes(b"tampered-executable")
    with pytest.raises(native.portable.VerificationError):
        native._check_cached_backend(bundled, installed)


@pytest.mark.parametrize("supported", [True, False])
def test_optional_screenshot_is_real_capture_or_explicit_nonblocking_error(
        tmp_path, monkeypatch, supported):
    desktop, playwright, _children, _environments, _routes = harness(tmp_path, monkeypatch)
    screenshot = tmp_path / "output" / "native.png"

    def capture(hwnd, destination):
        assert hwnd == 100 and destination == screenshot
        if not supported:
            raise OSError("capture unavailable")
        from PIL import Image
        destination.parent.mkdir()
        Image.new("RGB", (32, 24), "blue").save(destination)
        return {"width": 32, "height": 24}

    desktop.capture_window = capture
    checks = native.check_app(
        tmp_path / "QQPet-Steward.exe", tmp_path, desktop, playwright, screenshot)
    capture = next(check for check in checks if check["name"] == "native-window-screenshot")
    assert all(check["ok"] for check in checks)
    assert capture["captured"] is supported
    if supported:
        assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert capture["width"] == 32
    else:
        assert capture["screenshotError"] == "OSError"
        assert not screenshot.exists()


def test_native_capture_uses_full_win32_bounds_and_imagegrab(tmp_path, monkeypatch):
    from PIL import Image, ImageGrab

    def rectangle(_hwnd, output):
        output._obj.left, output._obj.top = -10, 20
        output._obj.right, output._obj.bottom = 90, 100
        return True

    def grab(*, bbox, all_screens):
        assert bbox == (-10, 20, 90, 100) and all_screens is True
        return Image.new("RGB", (100, 80), "blue")

    desktop = native.NativeDesktop.__new__(native.NativeDesktop)
    desktop.user = SimpleNamespace(
        SetForegroundWindow=lambda _hwnd: True, GetForegroundWindow=lambda: 100,
        GetWindowRect=rectangle,
    )
    monkeypatch.setattr(ImageGrab, "grab", grab)
    monkeypatch.setattr(native.time, "sleep", lambda _seconds: None)
    screenshot = tmp_path / "real-capture.png"
    assert desktop.capture_window(100, screenshot) == {"width": 100, "height": 80}
    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_cli_refuses_screenshot_in_native_app_directory(tmp_path):
    with pytest.raises(SystemExit):
        native.main(["--executable", str(tmp_path / "app" / "app.exe"),
                     "--report", str(tmp_path / "report.json"),
                     "--screenshot", str(tmp_path / "app" / "image.png")])


@pytest.mark.parametrize("login", [True, False])
def test_scoped_webview_policy_is_restored_after_owned_process_cleanup(tmp_path, monkeypatch, login):
    desktop, playwright, children, _environments, _routes = harness(
        tmp_path, monkeypatch, login=login)
    events = []

    @contextmanager
    def policy(**kwargs):
        assert kwargs["executable"] == tmp_path / "QQPet-Steward.exe"
        assert kwargs["user_data_folder"] == tmp_path / "webview-data"
        assert kwargs["browser_arguments"] == kwargs["environ"][
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
        events.append("entered")
        try:
            yield {"policyApplied": True, "elevated": True, "policyScope": "QQPet-Steward.exe"}
        finally:
            assert children[0].stopped and desktop.cleaned == [23]
            events.append("restored")

    monkeypatch.setattr(native, "webview_test_environment", policy)
    if login:
        checks = native.check_app(tmp_path / "QQPet-Steward.exe", tmp_path, desktop, playwright)
        assert checks[0]["webviewEnvironment"]["policyApplied"] is True
    else:
        with pytest.raises(native.portable.VerificationError) as error:
            native.check_app(tmp_path / "QQPet-Steward.exe", tmp_path, desktop, playwright)
        assert error.value.diagnostics["webviewEnvironment"]["elevated"] is True
    assert events == ["entered", "restored"]


def test_runtime_version_evidence_only_reads_documented_keys_and_filters_values():
    class Registry:
        HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "machine", "user"
        KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY = 1, 2, 4

        @staticmethod
        @contextmanager
        def OpenKey(hive, path, reserved, access):
            assert path == native.WEBVIEW2_CLIENT_KEY and reserved == 0
            assert access in (3, 5)
            yield hive

        @staticmethod
        def QueryValueEx(hive, name):
            assert name == "pv"
            return ("150.0.1.2" if hive == "machine" else "0.0.0.0"), 1

    assert native._runtime_versions(Registry) == [{"scope": "machine", "version": "150.0.1.2"}]


def test_gui_tree_is_killed_before_root_and_waits_only_for_owned_webviews(monkeypatch):
    events = []
    process = FakeProcess(12)
    process._handle = 999
    desktop = native.NativeDesktop.__new__(native.NativeDesktop)
    desktop._parents = lambda: {12: 1, 20: 12, 21: 20, 77: 1}
    desktop._image = lambda handle: Path("msedgewebview2.exe")
    desktop.kernel = SimpleNamespace(
        OpenProcess=lambda access, inherit, pid: pid,
        CloseHandle=lambda handle: events.append(("closed", handle)),
        WaitForSingleObject=lambda handle, timeout: events.append(("wait", handle)) or 0,
    )

    def taskkill(command, **kwargs):
        assert command == ["taskkill", "/PID", "12", "/T", "/F"]
        assert process.poll() is None
        events.append(("tree-killed", 12))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(native.subprocess, "run", taskkill)
    monkeypatch.setattr(native.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    desktop.cleanup_gui(process)
    assert events[0] == ("tree-killed", 12)
    assert {value for action, value in events if action == "wait"} == {20, 21}
    assert {value for action, value in events if action == "closed"} == {20, 21}
    assert process.stopped and process.terminated == 1


def test_temporary_cleanup_retries_permission_error_and_eventually_succeeds(monkeypatch, tmp_path):
    clock = [0.0]
    calls = []

    def cleanup():
        calls.append(True)
        if len(calls) < 3:
            raise PermissionError(13, "profile still locked", str(tmp_path / "profile"))

    monkeypatch.setattr(native.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(native.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    temporary = SimpleNamespace(name=str(tmp_path), cleanup=cleanup)
    assert native._cleanup_temporary(temporary) == 3
    assert clock[0] == pytest.approx(0.2)


def test_temporary_cleanup_final_failure_is_bounded_and_has_safe_diagnostics(monkeypatch, tmp_path):
    clock = [0.0]
    locked = tmp_path / "webview-data" / "Cookies"

    def cleanup():
        raise PermissionError(13, "private error text", str(locked))

    monkeypatch.setattr(native.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(native.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    with pytest.raises(native.portable.VerificationError) as failure:
        native._cleanup_temporary(SimpleNamespace(name=str(tmp_path), cleanup=cleanup), timeout=99)
    assert clock[0] == pytest.approx(5)
    assert failure.value.diagnostics["stage"] == "temp-cleanup"
    assert failure.value.diagnostics["path"] == str(locked)
    assert failure.value.diagnostics["errno"] == 13
    assert "private error text" not in json.dumps(failure.value.diagnostics)
