from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop.windows import runtime_self_test as verification


REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("entrypoint", ["dashboard", "scheduler"])
def test_self_test_dispatch_precedes_application_imports_and_directory_creation(
    tmp_path, entrypoint
):
    data = tmp_path / "real-portable-data"
    launcher = REPO / "desktop/windows" / f"{entrypoint}_launcher.py"
    code = f"""
import importlib.abc
import json
import os
import runpy
import sys
import types
from pathlib import Path

class NoApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == "dashboard" or fullname.startswith(("src", "scenarios")):
            raise AssertionError("application imported before self-test dispatch: " + fullname)
sys.meta_path.insert(0, NoApplicationImports())
stub = types.ModuleType("desktop.windows.runtime_self_test")
def self_test(arguments, *, entrypoint):
    assert os.environ["QQPET_DATA_DIR"] == {str(data)!r}
    assert not Path({str(data)!r}).exists()
    assert arguments == ["--self-test", "--self-test-output", "report.json"]
    print(json.dumps({{"entrypoint": entrypoint}}))
    return 19
stub.main = self_test
sys.modules[stub.__name__] = stub
sys.argv = [{str(launcher)!r}, "--self-test", "--self-test-output", "report.json"]
runpy.run_path({str(launcher)!r}, run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO,
        env=dict(os.environ, QQPET_DATA_DIR=str(data)),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 19, result.stderr
    assert json.loads(result.stdout) == {"entrypoint": entrypoint}
    assert not data.exists()


@pytest.mark.parametrize("entrypoint", ["dashboard", "scheduler"])
def test_source_self_test_fails_with_report_without_touching_user_data(tmp_path, entrypoint):
    data = tmp_path / "portable-data"
    data.mkdir()
    config = data / "config.yaml"
    config.write_text("keep this private config", encoding="utf-8")
    report = tmp_path / f"{entrypoint}.json"
    result = subprocess.run(
        [sys.executable, str(REPO / "desktop/windows" / f"{entrypoint}_launcher.py"),
         "--self-test", "--self-test-output", str(report)],
        cwd=REPO, env=dict(os.environ, QQPET_DATA_DIR=str(data)),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1
    observed = json.loads(result.stdout)
    assert observed == json.loads(report.read_text("utf-8"))
    assert observed["ok"] is False
    assert observed["entrypoint"] == entrypoint
    assert "frozen Windows" in observed["errors"][0]
    assert config.read_text("utf-8") == "keep this private config"
    assert list(data.iterdir()) == [config]


def test_network_guard_blocks_connections_and_restores_on_failure():
    originals = (socket.create_connection, socket.socket.connect, socket.socket.connect_ex)
    with pytest.raises(ValueError, match="finish guard"):
        with verification._offline_network():
            with pytest.raises(RuntimeError, match="network access is disabled"):
                socket.create_connection(("127.0.0.1", 5037))
            with socket.socket() as connection:
                for connect in (connection.connect, connection.connect_ex):
                    with pytest.raises(RuntimeError, match="network access is disabled"):
                        connect(("127.0.0.1", 5037))
            raise ValueError("finish guard")
    assert (socket.create_connection, socket.socket.connect, socket.socket.connect_ex) == originals


def test_isolated_environment_restores_existing_overrides_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("QQPET_DATA_DIR", "original-data")
    monkeypatch.delenv("QQPET_DISABLE_TG", raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "original")
    original_bytecode = sys.dont_write_bytecode
    with pytest.raises(ValueError):
        with verification._isolated_environment(tmp_path):
            assert os.environ["QQPET_DATA_DIR"] == str(tmp_path)
            assert os.environ["QQPET_DISABLE_TG"] == "1"
            assert sys.dont_write_bytecode is True
            raise ValueError("finish isolation")
    assert os.environ["QQPET_DATA_DIR"] == "original-data"
    assert "QQPET_DISABLE_TG" not in os.environ
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "original"
    assert sys.dont_write_bytecode == original_bytecode


@pytest.mark.parametrize("succeeds", [True, False])
def test_windowed_self_test_writes_json_even_without_standard_streams(
    tmp_path, monkeypatch, succeeds
):
    def probe():
        print("dependency diagnostics")
        if not succeeds:
            raise RuntimeError("missing dependency")
        return {"ok": True, "frozen": True, "deviceConnected": False, "telegramStarted": False}

    monkeypatch.setattr(verification, "_self_test", probe)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    report = tmp_path / "verification" / "dashboard.json"
    status = verification.main(
        ["--self-test", "--self-test-output", str(report)], entrypoint="dashboard"
    )
    observed = json.loads(report.read_text("utf-8"))
    assert status == (0 if succeeds else 1)
    assert observed["ok"] is succeeds
    assert observed["entrypoint"] == "dashboard"
    if not succeeds:
        assert observed["errors"] == ["RuntimeError: missing dependency"]


def test_self_test_stdout_contains_only_json_and_report_failure_is_nonzero(
    tmp_path, monkeypatch
):
    def probe():
        print("dependency diagnostics")
        return {"ok": True}

    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(verification, "_self_test", probe)
    assert verification.main(["--self-test"], entrypoint="scheduler") == 0
    assert json.loads(stdout.getvalue()) == {"ok": True, "entrypoint": "scheduler"}
    assert stderr.getvalue() == "dependency diagnostics\n"
    stdout.truncate(0)
    stdout.seek(0)
    # A directory cannot be used as an output file. Never report success when
    # the CI-visible report could not be written.
    assert verification.main(
        ["--self-test", "--self-test-output", str(tmp_path)], entrypoint="scheduler"
    ) == 1
    assert json.loads(stdout.getvalue())["ok"] is False


def _resource_fixture(root):
    for name in verification.REQUIRED_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"bundled")
    (root / "web/dist/index.html").write_text(
        '<script type="module" src="/assets/main.js"></script>'
        '<link href="/assets/main.css" rel="stylesheet">', encoding="utf-8",
    )
    for name in ("main.js", "main.css"):
        target = root / "web/dist/assets" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"compiled")
    model = root / "models/rapidocr/test.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    return SimpleNamespace(V6_TINY_MODELS=[
        ("test.onnx", "unused", hashlib.sha256(b"model").hexdigest()),
    ])


def test_resources_fail_on_missing_frontend_or_corrupt_model(tmp_path):
    ocr = _resource_fixture(tmp_path)
    assert verification._check_resources(tmp_path, ocr) == 2
    javascript = tmp_path / "web/dist/assets/main.js"
    javascript.unlink()
    with pytest.raises(RuntimeError, match="missing React asset"):
        verification._check_resources(tmp_path, ocr)
    javascript.write_bytes(b"compiled")
    (tmp_path / "models/rapidocr/test.onnx").write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="corrupt bundled OCR model"):
        verification._check_resources(tmp_path, ocr)


def test_ocr_probe_runs_classifier_and_recognizer_when_detection_is_empty():
    calls = []

    class Engine:
        def text_det(self, image):
            assert image.shape == (96, 320, 3)
            calls.append("det")
            return SimpleNamespace(boxes=None)

        def text_cls(self, images):
            assert len(images) == 1
            calls.append("cls")
            return SimpleNamespace(img_list=images)

        def text_rec(self, request):
            assert len(request.img) == 1
            calls.append("rec")
            return SimpleNamespace(txts=("",))

    observed = verification._check_ocr(SimpleNamespace(get_engine=Engine))
    assert calls == ["det", "cls", "rec"]
    assert observed["ocrModelsExecuted"] == calls
    assert observed["ocrDetectedLines"] == 0
