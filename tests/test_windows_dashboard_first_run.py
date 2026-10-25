from __future__ import annotations

from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import yaml


REPO = Path(__file__).resolve().parents[1]


def test_cold_dashboard_seeds_config_and_supports_initial_api_and_settings_without_tg(tmp_path):
    bundle, data = tmp_path / "bundle", tmp_path / "user-data"
    bundle.mkdir()
    example = (REPO / "config.example.yaml").read_bytes()
    (bundle / "config.example.yaml").write_bytes(example)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {name: value for name, value in os.environ.items()
           if not name.upper().startswith("QQPET_")}
    env.update({
        "QQPET_DATA_DIR": str(data), "QQPET_DISABLE_TG": "1", "QQPET_PORTABLE": "1",
        "PYTHONPATH": str(REPO), "PYTHONUTF8": "1",
    })
    code = f"""
import sys
from pathlib import Path
sys.frozen = True
sys._MEIPASS = {str(bundle)!r}
sys.argv = ["QQPetCopilot.exe", "--bind", "127.0.0.1", "--port", {str(port)!r}]
from src.config import RESOURCE_ROOT
assert RESOURCE_ROOT == Path(sys._MEIPASS)
from desktop.windows.dashboard_launcher import main
raise SystemExit(main())
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code], cwd=tmp_path, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        token_file = data / "runs" / "dashboard_access.token"
        while True:
            assert process.poll() is None, "isolated dashboard exited before it became ready"
            if token_file.is_file():
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    pass
            assert time.monotonic() < deadline, "isolated dashboard startup timed out"
            time.sleep(0.05)

        config_file = data / "config.yaml"
        assert config_file.read_bytes() == example
        key = token_file.read_text(encoding="utf-8").strip()

        def request(path, payload=None):
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"Authorization": f"Bearer {key}", "Origin": f"http://127.0.0.1:{port}"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            try:
                connection.request(
                    "POST" if payload is not None else "GET", path,
                    body=json.dumps(payload).encode() if payload is not None else None,
                    headers=headers,
                )
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        status, initial = request("/api/data")
        assert status == 200
        assert initial["config"] and initial["editable"]
        assert initial["scheduler"]["alive"] is False
        assert initial["editable"]["tgbot_running"] is False

        status, saved = request("/api/settings", {"updates": {"adventure_enabled": False}})
        assert status == 200 and saved["ok"] is True
        assert yaml.safe_load(config_file.read_text(encoding="utf-8"))[
            "tasks"]["adventure"]["enabled"] is False
        status, updated = request("/api/data")
        assert status == 200 and updated["editable"]["adventure_enabled"] is False
        assert updated["scheduler"]["alive"] is False
        assert not (data / "runs" / "runner.pid").exists()
        assert (bundle / "config.example.yaml").read_bytes() == example
        assert not (bundle / "config.yaml").exists()
        assert not (bundle / "runs").exists()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
