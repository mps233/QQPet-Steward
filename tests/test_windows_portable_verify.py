from __future__ import annotations

import io
import json
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace

import pytest

from desktop.windows import verify_portable as verify


def pe_image(path: Path, *, machine: int = 0x8664, magic: int = 0x20B) -> Path:
    image = bytearray(256)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 60, 128)
    image[128:132] = b"PE\0\0"
    struct.pack_into("<H", image, 132, machine)
    struct.pack_into("<H", image, 152, magic)
    path.write_bytes(image)
    return path


def test_pe_accepts_amd64_and_rejects_32_bit_or_corrupt_headers(tmp_path):
    executable = pe_image(tmp_path / "app.exe")
    assert verify.check_pe(executable)["machine"] == "AMD64"
    pe_image(executable, machine=0x14C)
    with pytest.raises(verify.VerificationError, match="AMD64"):
        verify.check_pe(executable)
    pe_image(executable, magic=0x10B)
    with pytest.raises(verify.VerificationError, match="PE32"):
        verify.check_pe(executable)
    executable.write_bytes(b"MZ")
    with pytest.raises(verify.VerificationError, match="DOS"):
        verify.check_pe(executable)
    image = bytearray(64)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 60, 0xFFFFFFFF)
    executable.write_bytes(image)
    with pytest.raises(verify.VerificationError, match="offset"):
        verify.check_pe(executable)


@pytest.mark.parametrize("name", [
    "config.yaml", "_internal/config.yaml", "CONFIG.YML",
    "runs/progress.json", "_internal/runs/session_rewards.jsonl",
    "dashboard_access.token",
])
def test_package_rejects_user_data_without_reading_it(tmp_path, name):
    forbidden = tmp_path / name
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("do-not-include-this-secret", encoding="utf-8")
    with pytest.raises(verify.VerificationError, match="user data") as error:
        verify.check_package(tmp_path)
    assert "do-not-include-this-secret" not in str(error.value)


def test_package_allows_empty_runs_and_library_defaults(tmp_path):
    (tmp_path / "runs").mkdir(exist_ok=True)
    defaults = tmp_path / "_internal" / "rapidocr" / "config.yaml"
    defaults.parent.mkdir(parents=True)
    defaults.write_text("library defaults", encoding="utf-8")
    (tmp_path / "config.example.yaml").write_text("example", encoding="utf-8")
    assert verify.check_package(tmp_path) == {"files": 2, "userDataIncluded": False}


def test_child_environment_clears_inherited_data_auth_and_proxy_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("QQPET_DATA_DIR", "real-data")
    monkeypatch.setenv("QQPET_DASHBOARD_ACCESS_FILE", "real-token")
    monkeypatch.setenv("QQPET_DISABLE_TG", "0")
    monkeypatch.setenv("https_proxy", "http://bad-proxy")
    monkeypatch.setenv("ALL_PROXY", "http://bad-proxy")
    env = verify.child_environment(tmp_path)
    assert env["QQPET_DATA_DIR"] == str(tmp_path)
    assert env["QQPET_PORTABLE"] == env["QQPET_DISABLE_TG"] == "1"
    assert "QQPET_DASHBOARD_ACCESS_FILE" not in env
    assert "https_proxy" not in env
    assert env["ALL_PROXY"] == ""


@pytest.mark.parametrize("invalid", [{}, {"ok": False}, {"frozen": False},
                                     {"deviceConnected": True}, {"telegramStarted": True},
                                     {"deviceConnected": 0}, {"frozen": 1}])
def test_self_test_requires_explicit_frozen_and_safety_fields(tmp_path, monkeypatch, invalid):
    expected = {"ok": True, "frozen": True, "deviceConnected": False, "telegramStarted": False}
    report = {} if not invalid else {**expected, **invalid}

    def fake_run(command, **kwargs):
        assert command[1:3] == ["--self-test", "--self-test-output"]
        assert kwargs["cwd"] == tmp_path / "sandbox" / "data"
        assert kwargs["env"]["QQPET_DISABLE_TG"] == "1"
        Path(command[3]).write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    with pytest.raises(verify.VerificationError, match="safety checks"):
        verify.check_self_test(tmp_path / "QQPetCopilot.exe", tmp_path / "sandbox")


def test_self_test_success_only_returns_non_secret_fields(tmp_path, monkeypatch):
    def fake_run(command, **_kwargs):
        Path(command[3]).write_text(json.dumps({
            "ok": True, "frozen": True, "deviceConnected": False, "telegramStarted": False,
            "access_code": "do-not-publish",
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    result = verify.check_self_test(tmp_path / "QQPetCopilot.exe", tmp_path / "sandbox")
    assert result["exitCode"] == 0
    assert "do-not-publish" not in json.dumps(result)


class FakeResponse(io.BytesIO):
    def __init__(self, status, payload, content_type="application/json"):
        super().__init__(payload)
        self.status = status
        self.headers = {"Content-Type": content_type}


class FakeProcess:
    def __init__(self):
        self.stopped = False
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.terminated += 1
        self.stopped = True

    def wait(self, timeout):
        return 0

    def kill(self):
        self.killed += 1
        self.stopped = True


def http_harness(tmp_path, monkeypatch, *, authenticated=True, root_status=200,
                 connection_error=None, log_text=""):
    process = FakeProcess()
    routes = []
    key = "isolated-test-access-code"
    state = {"logged_in": False}

    def fake_popen(command, **kwargs):
        assert command[1:3] == ["--bind", "127.0.0.1"]
        assert int(command[4]) > 0
        assert kwargs["env"]["QQPET_DATA_DIR"] == str(kwargs["cwd"])
        runs = kwargs["cwd"] / "runs"
        runs.mkdir()
        (runs / "dashboard_access.token").write_text(key, encoding="utf-8")
        if log_text:
            kwargs["stdout"].write(log_text.encode("utf-8"))
            logs = runs / "logs"
            logs.mkdir()
            (logs / "dashboard.log").write_text(log_text, encoding="utf-8")
        return process

    class Opener:
        def open(self, request, timeout):
            path = request.full_url.split(":", 2)[2].partition("/")[2]
            routes.append("/" + path)
            if not path:
                if connection_error:
                    raise connection_error
                return FakeResponse(root_status, b'<div id="root"></div><script src="/assets/app.js"></script>',
                                    "text/html")
            if path == "assets/app.js":
                return FakeResponse(200, b"react application", "text/javascript")
            if path == "api/client/meta":
                return FakeResponse(200, b'{"ok":true,"protocol_version":1,"capabilities":[]}')
            if path == "api/auth":
                assert json.loads(request.data) == {"key": key}
                state["logged_in"] = True
                return FakeResponse(200, b'{"ok":true}')
            if path == "api/data" and not state["logged_in"]:
                return FakeResponse(401, b'{"ok":false}')
            if path == "api/data":
                data = {
                    "now": "2026-10-25 12:00:00", "scheduler": {"alive": False, "pid": None},
                    "status": {}, "progress": {}, "config": {}, "editable": {},
                }
                return FakeResponse(200 if authenticated else 500, json.dumps(data).encode())
            raise AssertionError("unexpected route")

    monkeypatch.setattr(verify.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(verify, "build_opener", lambda *_args: Opener())
    return process, routes


def test_http_uses_loopback_isolated_login_and_stops_only_its_child(tmp_path, monkeypatch):
    process, routes = http_harness(tmp_path, monkeypatch)
    result = verify.check_http(tmp_path / "QQPetCopilot.exe", tmp_path / "http")
    assert result["cookieLogin"] is True and result["schedulerStarted"] is False
    assert routes == ["/", "/assets/app.js", "/api/client/meta", "/api/data",
                      "/api/auth", "/api/data"]
    assert process.terminated == 1 and process.killed == 0
    assert "isolated-test-access-code" not in json.dumps(result)


def test_http_failure_still_terminates_its_child(tmp_path, monkeypatch):
    process, _routes = http_harness(tmp_path, monkeypatch, authenticated=False)
    with pytest.raises(verify.VerificationError, match="authenticated"):
        verify.check_http(tmp_path / "QQPetCopilot.exe", tmp_path / "http")
    assert process.terminated == 1


def test_http_timeout_reports_last_status_live_process_and_redacted_log_tails(tmp_path, monkeypatch):
    log = "\n".join([f"line {index}" for index in range(15)] + [
        "startup access code: isolated-test-access-code",
        'token="private-token" password=private-password Authorization: Bearer private-bearer',
    ])
    process, _routes = http_harness(tmp_path, monkeypatch, root_status=404, log_text=log)
    with pytest.raises(verify.VerificationError, match="timeout") as failure:
        verify.check_http(tmp_path / "QQPetCopilot.exe", tmp_path / "http", timeout=0)
    diagnostics = failure.value.diagnostics
    assert diagnostics["lastHTTP"] == {"path": "/", "status": 404}
    assert diagnostics["processRunning"] is True
    assert diagnostics["processExitCode"] is None
    assert len(diagnostics["logTails"]["processOutput"]) == 12
    assert len(diagnostics["logTails"]["dashboardAudit"]) == 12
    serialized = json.dumps(diagnostics)
    assert all(secret not in serialized for secret in
               ("isolated-test-access-code", "private-token", "private-password", "private-bearer"))
    assert "startup" in serialized and "<redacted>" in serialized
    assert process.terminated == 1


def test_http_connection_timeout_reports_exception_types_without_private_messages(tmp_path, monkeypatch):
    process, _routes = http_harness(
        tmp_path, monkeypatch,
        connection_error=verify.URLError(ConnectionRefusedError("private-connection-message")),
    )
    with pytest.raises(verify.VerificationError, match="timeout") as failure:
        verify.check_http(tmp_path / "QQPetCopilot.exe", tmp_path / "http", timeout=0)
    diagnostics = failure.value.diagnostics
    assert diagnostics["lastHTTP"] == {
        "path": "/", "status": None, "errorType": "URLError", "reasonType": "ConnectionRefusedError",
    }
    assert diagnostics["logTails"] == {"processOutput": [], "dashboardAudit": []}
    assert "private-connection-message" not in json.dumps(diagnostics)
    assert process.terminated == 1


def test_stop_process_escalates_only_the_created_process(monkeypatch):
    process = FakeProcess()

    def fake_wait(timeout):
        if not process.killed:
            raise subprocess.TimeoutExpired("child", timeout)
        return 0

    process.wait = fake_wait
    verify._stop_process(process)
    assert process.terminated == process.killed == 1


def test_cli_reports_non_windows_block_and_leaves_package_clean(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "bundle"
    directory.mkdir()
    for name in verify.EXECUTABLES:
        pe_image(directory / name)
    monkeypatch.setattr(verify.sys, "platform", "darwin")
    report_path = tmp_path / "report.json"
    assert verify.main(["--directory", str(directory), "--report", str(report_path)]) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["checks"][-1]["name"] == "native-windows"
    assert set(path.name for path in directory.iterdir()) == set(verify.EXECUTABLES)
    assert json.loads(capsys.readouterr().out) == report


def test_cli_refuses_report_inside_release_package(tmp_path):
    with pytest.raises(SystemExit):
        verify.main(["--directory", str(tmp_path), "--report", str(tmp_path / "report.json")])


@pytest.mark.parametrize("leaks_user_data", [False, True])
def test_verification_is_sequential_cleans_sandboxes_and_rechecks_bundle(
        tmp_path, monkeypatch, leaks_user_data):
    directory = tmp_path / "bundle"
    directory.mkdir()
    for name in verify.EXECUTABLES:
        pe_image(directory / name)
    calls = []
    sandboxes = []

    def fake_self_test(executable, sandbox):
        assert executable.parent == directory
        sandbox.mkdir()
        sandboxes.append(sandbox)
        calls.append(executable.name)
        return {"frozen": True}

    def fake_http(executable, sandbox):
        assert executable.name == verify.EXECUTABLES[0]
        sandbox.mkdir()
        sandboxes.append(sandbox)
        calls.append("http")
        if leaks_user_data:
            (directory / "config.yaml").write_text("secret", encoding="utf-8")
        return {"schedulerStarted": False}

    monkeypatch.setattr(verify.sys, "platform", "win32")
    monkeypatch.setattr(verify, "check_self_test", fake_self_test)
    monkeypatch.setattr(verify, "check_http", fake_http)
    report = verify.verify(directory)
    assert calls == [*verify.EXECUTABLES, "http"]
    assert report["ok"] is not leaks_user_data
    assert report["checks"][-1]["name"] == "package-still-clean"
    assert all(not path.exists() for path in sandboxes)
    assert "secret" not in json.dumps(report)


def test_failed_verification_keeps_http_diagnostics_after_sandbox_cleanup(tmp_path, monkeypatch):
    directory = tmp_path / "bundle"
    directory.mkdir()
    for name in verify.EXECUTABLES:
        pe_image(directory / name)
    sandboxes = []

    def fail_http(_executable, sandbox):
        sandbox.mkdir()
        sandboxes.append(sandbox)
        raise verify.VerificationError("HTTP timeout", diagnostics={
            "lastHTTP": {"path": "/", "status": 404}, "processRunning": True,
        })

    monkeypatch.setattr(verify.sys, "platform", "win32")
    monkeypatch.setattr(verify, "check_self_test", lambda *_args: {})
    monkeypatch.setattr(verify, "check_http", fail_http)
    report = verify.verify(directory)
    assert report["ok"] is False
    assert report["checks"][-1]["diagnostics"]["lastHTTP"]["status"] == 404
    assert not sandboxes[0].exists()
