from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "desktop" / "macos" / "runtime_launcher.py"


@pytest.mark.parametrize("existing", [False, True])
def test_dashboard_entry_prepares_config_before_backend_dispatch(tmp_path, existing):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    default = "adb: {}\ntasks:\n  order: care>school\n"
    (bundle / "config.example.yaml").write_text(default, encoding="utf-8")
    data = tmp_path / "new-user-data"
    destination = data / "config.yaml"
    preserved = "# existing user configuration\nadb:\n  device_serial: keep-this-device\n"
    if existing:
        data.mkdir()
        destination.write_text(preserved, encoding="utf-8")
    environment = dict(os.environ, QQPET_DATA_DIR=str(data))
    code = f"""
import importlib.util
import json
import socket
import sys
from pathlib import Path

sys.frozen = True
sys.platform = "darwin"
sys._MEIPASS = {str(bundle)!r}

def blocked(*args, **kwargs):
    raise AssertionError("dashboard preparation must stay offline")

socket.create_connection = blocked
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
spec = importlib.util.spec_from_file_location("qqpet_runtime_launcher", {str(LAUNCHER)!r})
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
observed = {{}}

def dispatch(name, arguments):
    destination = Path({str(destination)!r})
    assert destination.is_file(), "configuration must exist before dashboard starts"
    observed.update(module=name, arguments=arguments, config=destination.read_text("utf-8"))
    return 23

launcher._run_module = dispatch
assert launcher.main(["dashboard", "--bind", "127.0.0.1"]) == 23
print(json.dumps(observed))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    observed = json.loads(completed.stdout)
    assert observed == {
        "module": "dashboard",
        "arguments": ["--bind", "127.0.0.1"],
        "config": preserved if existing else default,
    }
    assert destination.read_text("utf-8") == (preserved if existing else default)
    assert (bundle / "config.example.yaml").read_text("utf-8") == default
    assert not (bundle / "config.yaml").exists()
