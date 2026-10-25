"""Exercise the native Windows app and its embedded WebView2 in an isolated run."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import re
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

if __package__:
    from . import verify_portable as portable
    from .webview_test_environment import webview_test_environment
else:
    import verify_portable as portable
    from webview_test_environment import webview_test_environment


TITLE = "QQPet-Steward"
FILETIME_EPOCH = 11644473600
BROWSER_NAMES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}
WEBVIEW2_CLIENT_KEY = (
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)


def _runtime_versions(registry=None) -> list[dict]:
    if registry is None:
        import winreg as registry
    versions = []
    seen = set()
    for scope, hive in (("machine", registry.HKEY_LOCAL_MACHINE), ("user", registry.HKEY_CURRENT_USER)):
        for view in (registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY):
            try:
                with registry.OpenKey(hive, WEBVIEW2_CLIENT_KEY, 0, registry.KEY_READ | view) as key:
                    version, _kind = registry.QueryValueEx(key, "pv")
                if (isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", version)
                        and version != "0.0.0.0" and (scope, version) not in seen):
                    versions.append({"scope": scope, "version": version})
                    seen.add((scope, version))
            except OSError:
                pass
    return versions


def _api(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


class NativeDesktop:
    """Small Win32 adapter; handles claimed for cleanup never rely on PID reuse."""

    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        _api(self.user, "EnumWindows", wintypes.BOOL, self.callback, wintypes.LPARAM)
        _api(self.user, "EnumChildWindows", wintypes.BOOL, wintypes.HWND, self.callback, wintypes.LPARAM)
        _api(self.user, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND,
             ctypes.POINTER(wintypes.DWORD))
        _api(self.user, "GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        _api(self.user, "GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        _api(self.user, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
        _api(self.user, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        _api(self.user, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND)
        _api(self.user, "GetForegroundWindow", wintypes.HWND)
        _api(self.user, "PostMessageW", wintypes.BOOL, wintypes.HWND, wintypes.UINT,
             wintypes.WPARAM, wintypes.LPARAM)
        _api(self.user, "GetProcessWindowStation", wintypes.HANDLE)
        _api(self.user, "GetUserObjectInformationW", wintypes.BOOL, wintypes.HANDLE,
             ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        _api(self.user, "OpenInputDesktop", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL,
             wintypes.DWORD)
        _api(self.user, "CloseDesktop", wintypes.BOOL, wintypes.HANDLE)
        _api(self.kernel, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL,
             wintypes.DWORD)
        _api(self.kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
        _api(self.kernel, "GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE,
             ctypes.POINTER(wintypes.DWORD))
        _api(self.kernel, "QueryFullProcessImageNameW", wintypes.BOOL, wintypes.HANDLE,
             wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        _api(self.kernel, "GetProcessTimes", wintypes.BOOL, wintypes.HANDLE,
             *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        _api(self.kernel, "TerminateProcess", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
        _api(self.kernel, "WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
        _api(self.kernel, "CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)

    def desktop_evidence(self) -> dict:
        class Flags(ctypes.Structure):
            _fields_ = [("inherit", wintypes.BOOL), ("reserved", wintypes.BOOL),
                        ("flags", wintypes.DWORD)]

        station = self.user.GetProcessWindowStation()
        flags, needed = Flags(), wintypes.DWORD()
        name = ctypes.create_unicode_buffer(256)
        if (not station or not self.user.GetUserObjectInformationW(
                station, 1, ctypes.byref(flags), ctypes.sizeof(flags), ctypes.byref(needed))
                or not self.user.GetUserObjectInformationW(
                    station, 2, name, ctypes.sizeof(name), ctypes.byref(needed))):
            raise portable.VerificationError("cannot inspect the Windows window station",
                                             diagnostics={"win32Error": ctypes.get_last_error()})
        desktop = self.user.OpenInputDesktop(0, False, 1)
        evidence = {"windowStation": name.value, "windowStationVisible": bool(flags.flags & 1),
                    "inputDesktopAvailable": bool(desktop),
                    "webView2RuntimeVersions": _runtime_versions()}
        if desktop:
            self.user.CloseDesktop(desktop)
        else:
            evidence["win32Error"] = ctypes.get_last_error()
        if not evidence["windowStationVisible"] or not evidence["inputDesktopAvailable"]:
            raise portable.VerificationError("an interactive Windows desktop is unavailable",
                                             diagnostics=evidence)
        return evidence

    def windows(self, pid: int | None = None, *, parent: int | None = None) -> list[dict]:
        result = []

        @self.callback
        def visit(hwnd, _parameter):
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if pid is None or owner.value == pid:
                title, class_name = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
                self.user.GetWindowTextW(hwnd, title, len(title))
                self.user.GetClassNameW(hwnd, class_name, len(class_name))
                result.append({"hwnd": int(hwnd), "pid": owner.value, "title": title.value,
                               "class": class_name.value,
                               "visible": bool(self.user.IsWindowVisible(hwnd))})
            return True

        if parent is None:
            self.user.EnumWindows(visit, 0)
        else:
            self.user.EnumChildWindows(parent, visit, 0)
        return result

    def visible(self, hwnd: int) -> bool:
        return bool(self.user.IsWindowVisible(hwnd))

    def close_window(self, hwnd: int) -> None:
        if not self.user.PostMessageW(hwnd, 0x0010, 0, 0):
            raise portable.VerificationError("WM_CLOSE could not be posted to the app window")

    def capture_window(self, hwnd: int, destination: Path) -> dict:
        from PIL import ImageGrab

        self.user.SetForegroundWindow(hwnd)
        time.sleep(0.25)
        if self.user.GetForegroundWindow() != hwnd:
            raise portable.VerificationError("the app window could not be made foreground")
        dpi = getattr(self.user, "SetThreadDpiAwarenessContext", None)
        previous = None
        if dpi:
            _api(self.user, "SetThreadDpiAwarenessContext", wintypes.HANDLE, wintypes.HANDLE)
            previous = dpi(ctypes.c_void_p(-4))
        try:
            bounds = wintypes.RECT()
            if not self.user.GetWindowRect(hwnd, ctypes.byref(bounds)):
                raise portable.VerificationError("cannot read the native window rectangle")
            rectangle = (bounds.left, bounds.top, bounds.right, bounds.bottom)
            if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
                raise portable.VerificationError("native window rectangle is empty")
            image = ImageGrab.grab(bbox=rectangle, all_screens=True)
            if all(extrema == (0, 0) for extrema in image.convert("RGB").getextrema()):
                raise portable.VerificationError("native window capture returned only black pixels")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.capturing")
            try:
                image.save(temporary, format="PNG")
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            return {"width": image.width, "height": image.height}
        finally:
            if dpi and previous:
                dpi(previous)

    def _image(self, handle) -> Path:
        value, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not self.kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(length)):
            raise OSError("cannot identify process executable")
        return Path(value.value).resolve()

    def browser_windows(self) -> set[int]:
        result = set()
        for window in self.windows():
            if not window["visible"]:
                continue
            handle = self.kernel.OpenProcess(0x1000, False, window["pid"])
            if handle:
                try:
                    if self._image(handle).name.casefold() in BROWSER_NAMES:
                        result.add(window["hwnd"])
                except OSError:
                    pass
                finally:
                    self.kernel.CloseHandle(handle)
        return result

    def _parents(self) -> dict[int, int]:
        class Entry(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                ("executable", wintypes.WCHAR * 260),
            ]

        first = _api(self.kernel, "Process32FirstW", wintypes.BOOL, wintypes.HANDLE,
                     ctypes.POINTER(Entry))
        next_entry = _api(self.kernel, "Process32NextW", wintypes.BOOL, wintypes.HANDLE,
                          ctypes.POINTER(Entry))
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError("cannot inspect process ownership")
        try:
            item = Entry()
            item.size = ctypes.sizeof(item)
            result = {}
            more = first(snapshot, ctypes.byref(item))
            while more:
                result[item.pid] = item.parent
                more = next_entry(snapshot, ctypes.byref(item))
            return result
        finally:
            self.kernel.CloseHandle(snapshot)

    def claim_backend(self, pid: int, gui_pid: int, executable: Path, started: int) -> dict:
        handle = self.kernel.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
        if not handle:
            raise portable.VerificationError("cannot open the test backend process")
        try:
            creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if not self.kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                               ctypes.byref(kernel_time), ctypes.byref(user_time)):
                raise OSError("cannot inspect backend creation time")
            record = {
                "pid": pid, "parent": self._parents().get(pid), "image": self._image(handle),
                "created": (creation.dwHighDateTime << 32) | creation.dwLowDateTime,
            }
            _validate_backend(record, gui_pid, executable, started)
            return {"pid": pid, "handle": handle}
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def alive(self, backend: dict) -> bool:
        code = wintypes.DWORD()
        return bool(self.kernel.GetExitCodeProcess(backend["handle"], ctypes.byref(code))
                    and code.value == 259)

    def cleanup_backend(self, backend: dict) -> None:
        try:
            if self.alive(backend):
                self.kernel.TerminateProcess(backend["handle"], 0)
                self.kernel.WaitForSingleObject(backend["handle"], 8000)
        finally:
            self.kernel.CloseHandle(backend["handle"])

    def cleanup_gui(self, process) -> None:
        children = []
        try:
            parents = self._parents()
            owned = {process.pid}
            while True:
                descendants = {pid for pid, parent in parents.items() if parent in owned}
                if descendants <= owned:
                    break
                owned.update(descendants)
            for pid in owned - {process.pid}:
                handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
                if handle:
                    try:
                        if self._image(handle).name.casefold() == "msedgewebview2.exe":
                            children.append(handle)
                            handle = None
                    except OSError:
                        pass
                    finally:
                        if handle:
                            self.kernel.CloseHandle(handle)
            # Popen retains the Windows process handle, so this PID cannot
            # be reused while taskkill enumerates this test's process tree.
            if process.poll() is None:
                if getattr(process, "_handle", None) is None:
                    raise portable.VerificationError("the test GUI has no retained process handle")
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=8, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                finally:
                    portable._stop_process(process)
            deadline = time.monotonic() + 4
            for handle in children:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                if self.kernel.WaitForSingleObject(handle, remaining) != 0:
                    raise portable.VerificationError("an owned WebView2 process did not exit")
        finally:
            for handle in children:
                self.kernel.CloseHandle(handle)


def _validate_backend(record: dict, gui_pid: int, executable: Path, started: int) -> None:
    if (record["parent"] != gui_pid or record["created"] < started
            or os.path.normcase(str(record["image"])) != os.path.normcase(str(executable.resolve()))):
        raise portable.VerificationError("refusing to claim a backend not owned by this test app")


def _pid_file(path: Path) -> int | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value = value.get("pid")
        return value if type(value) is int and value > 0 else None
    except (OSError, ValueError):
        return None


def _port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def _wait(callback, timeout: float, message: str):
    deadline = time.monotonic() + timeout
    while True:
        value = callback()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise portable.VerificationError(message)
        time.sleep(0.15)


def _dashboard_page(browser, server: str):
    target = urlsplit(server)
    return next((page for context in browser.contexts for page in context.pages
                 if urlsplit(page.url).scheme == target.scheme
                 and urlsplit(page.url).netloc == target.netloc), None)


def _normalized_url(value) -> str:
    try:
        parts = urlsplit(str(value))
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port:
            host += f":{parts.port}"
        return f"{parts.scheme}://{host}{parts.path}" if host else f"{parts.scheme}:{parts.path}"
    except ValueError:
        return "(invalid URL)"


def _safe_text(value, secrets, limit=1000) -> str:
    text = str(value or "")
    for secret in secrets:
        text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s'\"<>]+", "<auth-redacted>", text)
    text = re.sub(
        r"""(?i)(["']?[\w.-]*(?:token|secret|password|credential|key|access[_ -]?code)"""
        r"""["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)""",
        r"\1<redacted>", text,
    )
    return text[:limit]


def _targets(payload, secrets) -> list[dict]:
    if not isinstance(payload, list):
        return []
    return [{
        "url": _safe_text(_normalized_url(item.get("url", "")), secrets, 400),
        "title": _safe_text(item.get("title", ""), secrets, 200),
        "type": _safe_text(item.get("type", ""), secrets, 40),
    } for item in payload[:12] if isinstance(item, dict)]


def _page_observations(browser, secrets) -> list[dict]:
    observations = []
    for index, context in enumerate(browser.contexts):
        for page in context.pages:
            item = {"context": index, "url": _safe_text(_normalized_url(page.url), secrets, 400)}
            parsed = urlsplit(page.url)
            if (parsed.hostname in {"localhost", "tauri.localhost"}
                    and parsed.path in {"", "/", "/index.html"}):
                try:
                    state = page.evaluate("""() => ({
                        connectionError: window.__qpetConnectionError || '',
                        bodyText: document.body?.innerText || ''
                    })""")
                    item["connectionError"] = _safe_text(state.get("connectionError"), secrets)
                    item["bodyText"] = _safe_text(state.get("bodyText"), secrets, 1500)
                except Exception as exc:
                    item["observationError"] = type(exc).__name__
            observations.append(item)
            if len(observations) >= 12:
                return observations
    return observations


def _wait_dashboard_page(browser, server, observations, secrets, *, timeout=45):
    session = browser.new_browser_cdp_session()

    def probe():
        # A real RPC pumps Playwright's sync event dispatcher; sleeping while
        # reading only cached contexts/pages can miss a newly created target.
        response = session.send("Target.getTargets")
        observations["targets"] = _targets(response.get("targetInfos"), secrets)
        observations["pages"] = _page_observations(browser, secrets)
        return _dashboard_page(browser, server)

    return _wait(probe, timeout, "WebView2 did not navigate to the local dashboard")


def _fingerprint(bundled: Path) -> str:
    value = (bundled / ".build-fingerprint").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise portable.VerificationError("the bundled backend fingerprint is invalid")
    return value.lower()


def _check_cached_backend(bundled: Path, installed: Path) -> dict:
    if not (installed / ".ready").is_file() or _fingerprint(bundled) != _fingerprint(installed):
        raise portable.VerificationError("the app did not finalize its backend cache installation")
    for name in portable.EXECUTABLES:
        source, cached = bundled / name, installed / name
        if not source.is_file() or not cached.is_file() or not source.stat().st_size:
            raise portable.VerificationError(f"backend cache is missing {name}")
        with source.open("rb") as left, cached.open("rb") as right:
            if (source.stat().st_size != cached.stat().st_size
                    or hashlib.file_digest(left, "sha256").digest()
                    != hashlib.file_digest(right, "sha256").digest()):
                raise portable.VerificationError(f"backend cache contents do not match {name}")
    return {"cacheInstalled": True, "verifiedExecutables": len(portable.EXECUTABLES),
            "sha256Matched": True}


def check_app(executable: Path, sandbox: Path, desktop, playwright_factory,
              screenshot: Path | None = None) -> list[dict]:
    data = sandbox / "data"
    runs = data / "runs"
    runs.mkdir(parents=True)
    (runs / "onboarding.json").write_text('{"version":1,"completed":true}\n', encoding="utf-8")
    port, debug = _port(), _port()
    while debug == port:
        debug = _port()
    server = f"http://127.0.0.1:{port}/"
    debug_url = f"http://127.0.0.1:{debug}"
    env = portable.child_environment(data)
    bundled = executable.parent / "backend"
    runtime_store = sandbox / "runtime-cache"
    installed = runtime_store / _fingerprint(bundled)
    env.update({
        "QQPET_SERVER": server, "QQPET_TRANSIENT": "0",
        "QQPET_RUNTIME_STORE": str(runtime_store),
        "QQPET_SOURCE_ROOT": str(sandbox / "no-source"),
        "WEBVIEW2_USER_DATA_FOLDER": str(sandbox / "webview-data"),
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS":
            f"--remote-debugging-port={debug} --remote-debugging-address=127.0.0.1",
    })
    expected_backend = installed / "QQPetCopilot.exe"
    pid_path = runs / "dashboard.pid"
    checks, backend, gui, second = [], None, None, None
    stage = "launch"
    started = int((time.time() + FILETIME_EPOCH - 2) * 10_000_000)
    browsers_before = desktop.browser_windows()
    opener = build_opener(ProxyHandler({}))
    last_http = {}
    policy = ExitStack()
    policy_evidence = {}
    browser_observations = {}
    secrets = []

    def http_status(base, path):
        last_http.clear()
        last_http.update({"path": path, "status": None})
        try:
            status = portable._request(opener, base.rstrip("/"), path)[0]
            last_http["status"] = status
            return status
        except (URLError, OSError, TimeoutError) as exc:
            last_http["errorType"] = type(exc).__name__
            reason = exc.reason if isinstance(exc, URLError) else exc
            last_http["reasonType"] = type(reason).__name__
            for field in ("errno", "winerror"):
                value = getattr(reason, field, None)
                if isinstance(value, int):
                    last_http[field] = value
            return None

    def main_window():
        candidates = [window for window in desktop.windows(gui.pid)
                      if window["visible"] and window["title"] == TITLE]
        return candidates[0] if len(candidates) == 1 else None

    try:
        stage = "webview-test-environment"
        policy_evidence = policy.enter_context(webview_test_environment(
            executable=executable, user_data_folder=sandbox / "webview-data",
            browser_arguments=env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"], environ=env,
        ))
        with (sandbox / "gui.log").open("wb") as output, (
                sandbox / "gui-secondary.log").open("wb") as secondary_output:
            gui = subprocess.Popen([str(executable)], cwd=sandbox, env=env,
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=output)
            stage = "backend-startup"
            pid = _wait(lambda: _pid_file(pid_path), 75, "app did not record its isolated backend PID")
            backend = desktop.claim_backend(pid, gui.pid, expected_backend, started)
            cache = _check_cached_backend(bundled, installed)
            _wait(lambda: http_status(server, "/api/client/meta") == 200, 60,
                  "the app backend did not serve client metadata")
            if http_status(server, "/api/data") != 401:
                raise portable.VerificationError("native app backend did not require authentication")
            checks.append({"name": stage, "ok": True, "backendOwned": True,
                           "defaultMode": True, "webviewEnvironment": policy_evidence, **cache})
            for path in runs.glob("*.token"):
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    secrets.append(value)
            stage = "embedded-webview"
            _wait(lambda: http_status(debug_url, "/json/version") == 200, 45,
                  "WebView2 did not expose its test-only CDP endpoint")
            with playwright_factory() as playwright:
                browser = playwright.chromium.connect_over_cdp(debug_url, timeout=20000)
                page = _wait_dashboard_page(browser, server, browser_observations, secrets)
                page.wait_for_selector("#taskList [data-k]", state="visible", timeout=45000)
                window = _wait(main_window, 15, "the app did not show a single native main window")
                classes = [child["class"] for child in desktop.windows(parent=window["hwnd"])]
                if not any(name.startswith("Chrome_") for name in classes):
                    raise portable.VerificationError("native window has no embedded WebView2 child")
                checks.append({"name": stage, "ok": True, "nativeWindow": True,
                               "webView2Child": True, "taskList": True})
                stage = "automatic-cookie-login"
                state = page.evaluate("""async () => {
                    const response = await fetch('/api/data', {credentials: 'same-origin'});
                    const data = await response.json();
                    const list = document.querySelector('#taskList');
                    return {status: response.status, schedulerAlive: data.scheduler?.alive,
                        schedulerPid: data.scheduler?.pid,
                        taskRows: list?.querySelectorAll('[data-k]').length || 0,
                        school: list?.textContent.includes('\\u5b66\\u4e60'),
                        work: list?.textContent.includes('\\u6253\\u5de5')};
                }""")
                if (state.get("status") != 200 or state.get("schedulerAlive") is not False
                        or state.get("schedulerPid") is not None or state.get("taskRows", 0) < 2
                        or state.get("school") is not True or state.get("work") is not True
                        or (runs / "runner.pid").exists()):
                    raise portable.VerificationError("embedded dashboard login or task state is invalid")
                checks.append({"name": stage, "ok": True, "dataStatus": 200,
                               "schedulerStarted": False, "taskRows": state["taskRows"]})
                if screenshot is not None:
                    capture = {"name": "native-window-screenshot", "ok": True}
                    try:
                        capture.update({"captured": True,
                                        **desktop.capture_window(window["hwnd"], screenshot)})
                    except Exception as exc:
                        capture.update({
                            "captured": False,
                            "screenshotError": (str(exc) if isinstance(
                                exc, portable.VerificationError) else type(exc).__name__),
                        })
                    checks.append(capture)
                stage = "close-to-tray"
                desktop.close_window(window["hwnd"])
                _wait(lambda: not desktop.visible(window["hwnd"]), 15,
                      "WM_CLOSE did not hide the main window")
                if (gui.poll() is not None or not desktop.alive(backend)
                        or _pid_file(pid_path) != pid
                        or http_status(server, "/api/client/meta") != 200):
                    raise portable.VerificationError("closing the window stopped the app or backend")
                checks.append({"name": stage, "ok": True, "windowHidden": True,
                               "guiRunning": True, "backendRunning": True})
                stage = "single-instance-reopen"
                second = subprocess.Popen([str(executable)], cwd=sandbox, env=env,
                                          stdin=subprocess.DEVNULL,
                                          stdout=secondary_output, stderr=secondary_output)
                if second.wait(timeout=20) != 0:
                    raise portable.VerificationError("second app invocation did not exit successfully")
                _wait(lambda: desktop.visible(window["hwnd"]), 20,
                      "single-instance invocation did not restore the original window")
                if (gui.poll() is not None or not desktop.alive(backend)
                        or _pid_file(pid_path) != pid):
                    raise portable.VerificationError("reopening the app replaced its backend process")
                checks.append({"name": stage, "ok": True, "originalWindowRestored": True,
                               "backendReused": True, "secondInstanceExited": True})
                stage = "external-browser-windows"
                if desktop.browser_windows() - browsers_before:
                    raise portable.VerificationError("the app opened an external browser window")
                checks.append({"name": stage, "ok": True, "newBrowserWindows": 0})
        return checks
    except Exception as exc:
        for path in runs.glob("*.token"):
            try:
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    secrets.append(value)
            except (OSError, UnicodeError):
                pass
        diagnostics = {
            "stage": stage, "errorType": type(exc).__name__,
            "guiExitCode": gui.poll() if gui is not None else None,
            "backendPIDRecorded": _pid_file(pid_path) is not None,
            "lastHTTP": last_http,
            "webviewEnvironment": policy_evidence,
            "browser": browser_observations,
            "logTails": {
                "gui": portable._log_tail(sandbox / "gui.log", secrets),
                "backend": portable._log_tail(runs / "dashboard_console.log", secrets),
                "audit": portable._log_tail(runs / "logs" / "dashboard.log", secrets),
            },
        }
        try:
            windows = desktop.windows(gui.pid) if gui is not None else []
            diagnostics["nativeWindows"] = [{
                "title": _safe_text(window["title"], secrets, 200),
                "class": _safe_text(window["class"], secrets, 100),
                "visible": window["visible"],
            } for window in windows[:12]]
            if screenshot is not None:
                visible = next((window for window in windows if window["visible"]), None)
                destination = screenshot.with_name(screenshot.stem + "-failure.png")
                if visible and not destination.exists():
                    dimensions = desktop.capture_window(visible["hwnd"], destination)
                    diagnostics["failureScreenshot"] = {
                        "file": destination.name, "captured": True, **dimensions,
                    }
        except Exception as observation_error:
            diagnostics["nativeObservationError"] = type(observation_error).__name__
        try:
            status, _headers, payload = portable._request(opener, debug_url, "/json/list")
            diagnostics["debugTargetList"] = {
                "status": status, "targets": _targets(json.loads(payload), secrets),
            }
        except Exception as observation_error:
            diagnostics["debugTargetList"] = {"errorType": type(observation_error).__name__}
        error = str(exc) if isinstance(exc, portable.VerificationError) else type(exc).__name__
        raise portable.VerificationError(error, diagnostics=diagnostics) from None
    finally:
        # Claim only a backend with this GUI as parent and this build's image.
        # Retained process handles ensure cleanup cannot target a reused PID.
        try:
            if backend is None and gui is not None:
                pid = _pid_file(pid_path)
                if pid:
                    try:
                        backend = desktop.claim_backend(pid, gui.pid, expected_backend, started)
                    except (OSError, RuntimeError):
                        pass
            try:
                if second is not None:
                    portable._stop_process(second)
                if gui is not None:
                    desktop.cleanup_gui(gui)
            finally:
                if backend is not None:
                    desktop.cleanup_backend(backend)
        finally:
            policy.close()


def _cleanup_temporary(temporary, *, timeout: float = 5) -> int:
    deadline = time.monotonic() + min(timeout, 5)
    attempts = 0
    while True:
        attempts += 1
        try:
            temporary.cleanup()
            return attempts
        except PermissionError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                diagnostics = {
                    "stage": "temp-cleanup", "path": str(exc.filename or temporary.name),
                    "errorType": type(exc).__name__, "attempts": attempts,
                }
                for field in ("winerror", "errno"):
                    value = getattr(exc, field, None)
                    if isinstance(value, int):
                        diagnostics[field] = value
                raise portable.VerificationError(
                    "the isolated native test directory could not be removed",
                    diagnostics=diagnostics,
                ) from None
            time.sleep(min(0.1, remaining))


def verify(executable: Path, screenshot: Path | None = None) -> dict:
    report = {"ok": False, "platform": sys.platform, "checks": []}
    stage = "native-windows"
    try:
        if sys.platform != "win32":
            raise portable.VerificationError("native GUI verification must run on Windows")
        stage = "gui-executable"
        report["checks"].append({"name": stage, "ok": True, **portable.check_pe(executable)})
        stage = "interactive-desktop"
        desktop = NativeDesktop()
        report["checks"].append({"name": stage, "ok": True, **desktop.desktop_evidence()})
        stage = "playwright"
        from playwright.sync_api import sync_playwright
        temporary = tempfile.TemporaryDirectory(prefix="qqpet-native-verify-")
        try:
            stage = "native-app"
            report["checks"].extend(check_app(
                executable, Path(temporary.name), desktop, sync_playwright, screenshot))
        finally:
            try:
                attempts = _cleanup_temporary(temporary)
                report["checks"].append({"name": "temp-cleanup", "ok": True, "attempts": attempts})
            except Exception:
                stage = "temp-cleanup"
                raise
        report["ok"] = True
    except Exception as exc:
        failure = {"name": stage, "ok": False,
                   "error": str(exc) if isinstance(exc, portable.VerificationError) else type(exc).__name__}
        if isinstance(exc, portable.VerificationError) and exc.diagnostics is not None:
            failure["diagnostics"] = exc.diagnostics
        report["checks"].append(failure)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--screenshot", type=Path, help="optional real native-window PNG capture")
    args = parser.parse_args(argv)
    executable, report_path = args.executable.resolve(), args.report.resolve()
    if report_path.is_relative_to(executable.parent):
        parser.error("--report must be outside the native app directory")
    screenshot = args.screenshot.resolve() if args.screenshot else None
    if screenshot is not None and (
            screenshot.is_relative_to(executable.parent) or screenshot == report_path
            or screenshot.suffix.lower() != ".png"):
        parser.error("--screenshot must be a separate PNG outside the native app directory")
    report = verify(executable, screenshot)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
