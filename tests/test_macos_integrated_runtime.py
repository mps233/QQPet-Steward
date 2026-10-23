from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import dashboard
from src import config, instance, runtime_process


CONTROL_KEY = "isolated-desktop-control-key-0123456789"
ACCESS_KEY = "isolated-browser-access-key-0123456789"
REPO = Path(__file__).resolve().parents[1]


def import_paths(env, prelude=""):
    code = prelude + """
import json
from src import config
import dashboard
print(json.dumps({
    'app': str(config.APP_ROOT),
    'resources': str(config.RESOURCE_ROOT),
    'config': str(config.CONFIG_FILE),
    'dashboard_resources': str(dashboard.BASE),
    'runs': str(dashboard.RUNS),
    'control': str(dashboard.LOCAL_CONTROL_FILE),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env,
        capture_output=True, text=True, check=True, timeout=20,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_explicit_data_root_does_not_move_resources(tmp_path):
    data = tmp_path / "user-data"
    env = dict(os.environ, QQPET_DATA_DIR=str(data))
    paths = import_paths(env)
    assert paths["app"] == str(data)
    assert paths["config"] == str(data / "config.yaml")
    assert paths["runs"] == str(data / "runs")
    assert paths["control"] == str(data / "runs" / "desktop_control.token")
    assert paths["resources"] == str(REPO)
    assert paths["dashboard_resources"] == str(REPO)
    assert not data.exists()


def test_source_paths_remain_in_checkout_without_override():
    env = dict(os.environ)
    env.pop("QQPET_DATA_DIR", None)
    paths = import_paths(env)
    assert paths["app"] == str(REPO)
    assert paths["resources"] == str(REPO)
    assert paths["runs"] == str(REPO / "runs")


def test_frozen_macos_uses_user_data_and_bundle_resources(tmp_path):
    home = tmp_path / "home"
    bundle = tmp_path / "bundle"
    env = dict(os.environ, HOME=str(home))
    env.pop("QQPET_DATA_DIR", None)
    paths = import_paths(
        env,
        "import sys\n"
        "sys.frozen = True\n"
        "sys.platform = 'darwin'\n"
        f"sys._MEIPASS = {str(bundle)!r}\n",
    )
    data = home / "Library" / "Application Support" / "QQPetCopilot"
    assert paths["app"] == str(data)
    assert paths["resources"] == str(bundle)
    assert paths["dashboard_resources"] == str(bundle)
    assert paths["control"] == str(data / "runs" / "desktop_control.token")


def test_first_config_is_seeded_in_writable_data(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "config.example.yaml").write_text("adb: {}\n", encoding="utf-8")
    destination = tmp_path / "new-data" / "config.yaml"
    monkeypatch.setattr(config, "RESOURCE_ROOT", bundle)
    config.load_config(destination)
    assert destination.read_text("utf-8") == "adb: {}\n"
    assert not (bundle / "config.yaml").exists()


def test_bundled_platform_tools_is_selected_before_system_adb(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    adb = bundle / "resources" / "platform-tools" / "adb"
    adb.parent.mkdir(parents=True)
    adb.write_bytes(b"test-adb")
    monkeypatch.setattr(config, "APP_ROOT", tmp_path / "data")
    monkeypatch.setattr(config, "RESOURCE_ROOT", bundle)
    monkeypatch.setattr(config.shutil, "which", lambda _name: "/system/adb")
    assert config.find_adb() == str(adb)


def test_frozen_macos_scheduler_uses_same_runtime_with_flags():
    argv = runtime_process.build_scheduler_argv(
        "/bundle/resources", frozen=True, platform="darwin",
        executable="/bundle/QQPetRuntime", extra_args=["--emulator"],
        exists=lambda _path: False,
    )
    assert argv == ["/bundle/QQPetRuntime", "scheduler", "--emulator"]


def test_frozen_macos_career_sync_does_not_require_source_script():
    assert runtime_process.build_career_sync_argv(
        "/bundle/resources", frozen=True, platform="darwin",
        executable="/bundle/QQPetRuntime",
    ) == ["/bundle/QQPetRuntime", "career-sync"]


def test_source_career_sync_keeps_script_command(tmp_path):
    script = tmp_path / "tools" / "career_sync.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    assert runtime_process.build_career_sync_argv(
        tmp_path, frozen=False, platform="darwin", executable="/venv/python",
    ) == ["/venv/python", str(script)]


@pytest.mark.parametrize("mode", ["frozen", "explicit-data"])
def test_isolated_runtime_never_discovers_other_checkouts(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(sys, "frozen", mode == "frozen", raising=False)
    if mode == "explicit-data":
        monkeypatch.setenv("QQPET_DATA_DIR", str(tmp_path))
    else:
        monkeypatch.delenv("QQPET_DATA_DIR", raising=False)
    monkeypatch.setattr(instance, "read_pidfile", lambda _path: None)
    monkeypatch.setattr(instance, "pgrep_runners", lambda *_a: pytest.fail("global runner lookup"))
    assert instance.find_running(tmp_path / "runner.pid") == []


def test_isolated_runtime_can_adopt_its_own_registered_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("QQPET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(instance, "read_pidfile", lambda _path: {
        "pid": 2147483646, "started": 123, "argv": ["bundled-runtime", "scheduler"],
    })
    monkeypatch.setattr(instance, "pgrep_runners", lambda *_a: pytest.fail("global runner lookup"))
    assert instance.find_running(tmp_path / "runner.pid") == [{
        "pid": 2147483646, "via": "pidfile", "started": 123,
    }]


def test_unscoped_source_keeps_legacy_runner_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("QQPET_DATA_DIR", raising=False)
    monkeypatch.setattr(instance, "read_pidfile", lambda _path: None)
    monkeypatch.setattr(instance, "pgrep_runners", lambda *_a: [2147483646])
    assert instance.find_running(tmp_path / "runner.pid") == [{
        "pid": 2147483646, "via": "pgrep", "started": None,
    }]


@contextmanager
def running_server(handler=dashboard.Handler):
    service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    service.access_key = ACCESS_KEY
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{service.server_port}"
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


def request(url, *, method="POST", headers=None):
    try:
        response = urlopen(Request(url, method=method, headers=headers or {}), timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        return response.code, response.headers, json.loads(response.read())


def test_desktop_session_returns_usable_cookie_only_to_local_control(monkeypatch):
    monkeypatch.setattr(dashboard, "_local_control_key", lambda: CONTROL_KEY)
    monkeypatch.setattr(dashboard, "build_data", lambda: {"safe": True})
    with running_server() as base:
        code, headers, payload = request(
            base + "/api/desktop/session",
            headers={dashboard.LOCAL_CONTROL_HEADER: CONTROL_KEY},
        )
        assert code == 200
        assert headers["Cache-Control"] == "no-store"
        assert payload == {
            "cookieName": dashboard.COOKIE_NAME,
            "cookieValue": dashboard.session_value(ACCESS_KEY),
        }
        assert ACCESS_KEY not in json.dumps(payload)
        code, _, data = request(
            base + "/api/data", method="GET",
            headers={"Cookie": f"{payload['cookieName']}={payload['cookieValue']}"},
        )
        assert code == 200 and data == {"safe": True}


@pytest.mark.parametrize("headers", [
    {},
    {dashboard.LOCAL_CONTROL_HEADER: "wrong"},
    {"Cookie": f"{dashboard.COOKIE_NAME}={dashboard.session_value(ACCESS_KEY)}"},
    {"Authorization": f"Bearer {ACCESS_KEY}"},
    {dashboard.LOCAL_CONTROL_HEADER: CONTROL_KEY, "Origin": "https://attacker.invalid"},
])
def test_desktop_session_rejects_other_credentials_and_origins(monkeypatch, headers):
    monkeypatch.setattr(dashboard, "_local_control_key", lambda: CONTROL_KEY)
    with running_server() as base:
        code, _, payload = request(base + "/api/desktop/session", headers=headers)
        assert code in (401, 403)
        assert "cookieValue" not in payload
        assert dashboard.session_value(ACCESS_KEY) not in json.dumps(payload)


def test_desktop_session_rejects_non_loopback_even_with_control_token(monkeypatch):
    class RemoteHandler(dashboard.Handler):
        def do_POST(self):
            self.client_address = ("192.0.2.5", self.client_address[1])
            super().do_POST()

    monkeypatch.setattr(dashboard, "_local_control_key", lambda: CONTROL_KEY)
    with running_server(RemoteHandler) as base:
        code, _, payload = request(
            base + "/api/desktop/session",
            headers={dashboard.LOCAL_CONTROL_HEADER: CONTROL_KEY},
        )
        assert code in (401, 403)
        assert "cookieValue" not in payload


@pytest.mark.parametrize("key", ["", "short"])
def test_empty_or_short_control_key_never_authorizes(monkeypatch, key):
    class FakeHandler:
        client_address = ("127.0.0.1", 1234)
        headers = {dashboard.LOCAL_CONTROL_HEADER: key}

    monkeypatch.setattr(dashboard, "_local_control_key", lambda: key)
    assert dashboard._local_control_authorized(FakeHandler()) is False


def test_runtime_metadata_has_no_local_paths(monkeypatch):
    with running_server() as base:
        code, _, payload = request(base + "/api/client/meta", method="GET")
    assert code == 200
    assert payload["runtime"]["desktop_session"] is True
    assert str(REPO) not in json.dumps(payload)
    assert str(Path.home()) not in json.dumps(payload)


@pytest.mark.parametrize("arguments, expected_bind", [
    (["--port", "0"], "0.0.0.0"),
    (["--bind", "127.0.0.1", "--port", "0"], "127.0.0.1"),
])
def test_main_bind_and_disabled_tg_are_explicit(monkeypatch, arguments, expected_bind):
    observed = {}

    class FakeServer:
        server_port = 57311
        handle_error = staticmethod(lambda *_a: None)

        def __init__(self, address, _handler):
            observed["address"] = address

        def serve_forever(self, *, poll_interval):
            observed["served"] = poll_interval

        def server_close(self):
            observed["closed"] = True

    monkeypatch.setattr(sys, "argv", ["dashboard", *arguments])
    monkeypatch.setenv("QQPET_DISABLE_TG", "1")
    monkeypatch.setattr(dashboard, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(dashboard, "load_access_key", lambda _path: ACCESS_KEY)
    monkeypatch.setattr(dashboard, "_local_control_key", lambda: CONTROL_KEY)
    monkeypatch.setattr(dashboard, "ClientProtocolStore", lambda _path: object())
    monkeypatch.setattr(dashboard, "set_client_protocol_store", lambda _store: None)
    monkeypatch.setattr(dashboard, "audit", lambda _message: None)
    monkeypatch.setattr(dashboard.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(dashboard, "start_tg_bot", lambda: pytest.fail("TG must stay disabled"))
    assert dashboard.main() == 0
    assert observed["address"] == (expected_bind, 0)
    assert observed["served"] == 0.5
    assert observed["closed"] is True
