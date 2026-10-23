"""Bundled macOS backend, scheduler, and maintenance entry points."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import runpy
import socket
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

if not getattr(sys, "frozen", False):
    source_root = Path(__file__).resolve().parents[2]
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))


def _data_paths():
    from src.runtime_process import user_data_paths

    root = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[2])
    return user_data_paths(root)


def _prepare_data_root() -> Path:
    paths = _data_paths().ensure()
    os.environ["QQPET_DATA_DIR"] = str(paths.root)
    if not paths.config.is_file():
        from src.config import load_config

        load_config(paths.config)
    return paths.root


def _session_cookie() -> dict[str, str]:
    from src.dashboard_auth import COOKIE_NAME, MIN_KEY_LENGTH, session_value

    key_path = _data_paths().runs / "dashboard_access.token"
    key = key_path.read_text(encoding="utf-8").strip()
    if len(key) < MIN_KEY_LENGTH:
        raise ValueError("existing dashboard access key is invalid")
    return {"cookieName": COOKIE_NAME, "cookieValue": session_value(key)}


def _run_module(name: str, arguments: list[str]) -> int:
    original_argv = sys.argv
    sys.argv = [original_argv[0], *arguments]
    try:
        runpy.run_module(name, run_name="__main__", alter_sys=True)
    except SystemExit as error:
        if error.code is None:
            return 0
        if isinstance(error.code, int):
            return error.code
        print(str(error.code), file=sys.stderr)
        return 1
    finally:
        sys.argv = original_argv
    return 0


@contextmanager
def _offline_network():
    original = (socket.create_connection, socket.socket.connect, socket.socket.connect_ex)

    def blocked(*_args, **_kwargs):
        raise RuntimeError("network access is disabled during the offline self-test")

    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    try:
        yield
    finally:
        socket.create_connection, socket.socket.connect, socket.socket.connect_ex = original


def _self_test() -> dict:
    # Set isolation before importing any module that binds a runs/config path.
    with tempfile.TemporaryDirectory(prefix="qqpet-runtime-self-test-") as temporary, _offline_network():
        data_root = Path(temporary) / "data"
        os.environ["QQPET_DATA_DIR"] = str(data_root)
        os.environ["QQPET_DISABLE_TG"] = "1"
        _prepare_data_root()
        if not (data_root / "config.yaml").is_file():
            raise RuntimeError("default configuration was not created before backend imports")

        module_names = (
            "dashboard", "scenarios.runner", "tools.career_sync",
            "src.settings", "src.ocr", "src.u2dev", "src.notify",
            "uiautomator2", "rapidocr", "onnxruntime", "cv2", "PIL.Image",
            "qrcode", "onepush", "frida", "frida_tools",
        )
        modules = {name: importlib.import_module(name) for name in module_names}
        from src import config, runtime_process
        from src.dashboard_auth import session_value

        if config.APP_ROOT.resolve() != data_root.resolve():
            raise RuntimeError("APP_ROOT does not use the isolated writable data root")
        if config.CONFIG_FILE.resolve() != (data_root / "config.yaml").resolve():
            raise RuntimeError("CONFIG_FILE does not use the isolated writable data root")
        if modules["dashboard"].RUNS.resolve() != (data_root / "runs").resolve():
            raise RuntimeError("dashboard RUNS points outside the isolated data root")
        resource_root = config.RESOURCE_ROOT
        required_files = [
            "config.example.yaml", "web/dist/index.html", "static/icon-512.png",
            "uiautomator2/assets/u2.jar", "uiautomator2/assets/app-uiautomator.apk",
            "rapidocr/config.yaml", "rapidocr/default_models.yaml",
            "frida_tools/bridges/java.js", "resources/platform-tools/adb",
        ]
        required_files.extend(
            f"scenarios/assets/{name}" for name in (
                "home_bag_tied.png", "home_bag_open.png",
                "friend_icon_bag.png", "friend_icon_other.png",
            )
        )
        required_files.extend(
            f"resources/minitouch/minitouch-{abi}"
            for abi in ("arm64-v8a", "armeabi-v7a", "x86", "x86_64")
        )
        missing = [name for name in required_files
                   if not (resource_root / name).is_file()]
        if missing:
            raise RuntimeError("missing bundled resources: " + ", ".join(missing))
        for name, _url, expected in modules["src.ocr"].V6_TINY_MODELS:
            model = resource_root / "models" / "rapidocr" / name
            if not model.is_file() or hashlib.sha256(model.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"missing or corrupt bundled OCR model: {name}")

        probe = data_root / "runs" / "write-probe"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        scheduler = runtime_process.build_scheduler_argv(resource_root)
        if getattr(sys, "frozen", False) and scheduler != [sys.executable, "scheduler"]:
            raise RuntimeError(f"unexpected frozen scheduler command: {scheduler}")
        adb = Path(config.find_adb())
        bundled_adb = resource_root / "resources" / "platform-tools" / "adb"
        if adb.resolve() != bundled_adb.resolve() or not os.access(adb, os.X_OK):
            raise RuntimeError("ADB does not resolve to the bundled executable")

        import numpy as np

        engine = modules["src.ocr"].get_engine()
        output = engine(
            np.full((96, 320, 3), 255, dtype=np.uint8),
            use_det=True, use_cls=True, use_rec=True,
        )
        texts = getattr(output, "txts", None)

        test_key = "qqpet-isolated-self-test-key"
        (data_root / "runs" / "dashboard_access.token").write_text(test_key, encoding="ascii")
        cookie = _session_cookie()
        if cookie["cookieValue"] != session_value(test_key):
            raise RuntimeError("session-cookie does not match the browser session contract")

        return {
            "ok": True,
            "frozen": bool(getattr(sys, "frozen", False)),
            "dataRoot": str(data_root),
            "configSeeded": True,
            "resourceRoot": str(resource_root),
            "imports": list(module_names),
            "schedulerCommand": scheduler,
            "adb": str(adb),
            "ocrModels": 3,
            "ocrEngineReady": True,
            "ocrDetectedLines": len(texts) if texts is not None else 0,
            "fridaServers": [
                path.name for path in
                (resource_root / "resources" / "frida-server").glob("*.xz")
            ],
            "deviceConnected": False,
            "telegramStarted": False,
        }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments.pop(0) if arguments else ""
    if command == "session-cookie":
        try:
            print(json.dumps(_session_cookie(), separators=(",", ":")), flush=True)
            return 0
        except Exception as error:
            print(f"session-cookie failed: {error}", file=sys.stderr)
            return 1
    if command == "self-test":
        try:
            result = _self_test()
        except Exception as error:
            print(json.dumps({"ok": False, "error": str(error)}), flush=True)
            return 1
        print(json.dumps(result, separators=(",", ":")), flush=True)
        return 0
    entrypoints = {
        "dashboard": "dashboard",
        "scheduler": "scenarios.runner",
        "career-sync": "tools.career_sync",
    }
    if command not in entrypoints:
        print("usage: QQPetRuntime {dashboard|scheduler|career-sync|session-cookie|self-test}",
              file=sys.stderr)
        return 2
    _prepare_data_root()
    return _run_module(entrypoints[command], arguments)


if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    raise SystemExit(main())
