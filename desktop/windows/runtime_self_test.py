"""Offline verification shared by the two frozen Windows entry points."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import lzma
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path


IMPORTS = (
    "dashboard", "scenarios.runner", "src.settings", "src.ocr", "src.u2dev",
    "src.notify", "src.opener", "src.tg_bot", "uiautomator2", "rapidocr",
    "onnxruntime", "cv2", "PIL.Image", "qrcode", "onepush", "frida", "frida_tools",
)
REQUIRED_FILES = (
    "config.example.yaml", "web/dist/index.html", "static/icon-512.png",
    "uiautomator2/assets/u2.jar", "uiautomator2/assets/app-uiautomator.apk",
    "rapidocr/config.yaml", "rapidocr/default_models.yaml",
    "frida_tools/bridges/java.js",
    "resources/platform-tools/adb.exe", "resources/platform-tools/AdbWinApi.dll",
    "resources/platform-tools/AdbWinUsbApi.dll",
    "scenarios/assets/home_bag_tied.png", "scenarios/assets/home_bag_open.png",
    "scenarios/assets/friend_icon_bag.png", "scenarios/assets/friend_icon_other.png",
    *(f"resources/minitouch/minitouch-{abi}"
      for abi in ("arm64-v8a", "armeabi-v7a", "x86", "x86_64")),
)


@contextmanager
def _offline_network():
    originals = (socket.create_connection, socket.socket.connect, socket.socket.connect_ex)

    def blocked(*_args, **_kwargs):
        raise RuntimeError("network access is disabled during the offline self-test")

    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    try:
        yield
    finally:
        socket.create_connection, socket.socket.connect, socket.socket.connect_ex = originals


@contextmanager
def _isolated_environment(data_root: Path):
    values = {
        "QQPET_DATA_DIR": str(data_root),
        "QQPET_DISABLE_TG": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    originals = {name: os.environ.get(name) for name in values}
    original_bytecode = sys.dont_write_bytecode
    os.environ.update(values)
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = original_bytecode
        for name, value in originals.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _check_resources(resource_root: Path, ocr) -> int:
    missing = [name for name in REQUIRED_FILES
               if not (resource_root / name).is_file()
               or not (resource_root / name).stat().st_size]
    if missing:
        raise RuntimeError("missing bundled resources: " + ", ".join(missing))

    html = (resource_root / "web/dist/index.html").read_text(encoding="utf-8")
    assets = re.findall(r"""(?:src|href)=["'](/assets/[^"']+)["']""", html)
    if not assets or not any(name.endswith(".js") for name in assets):
        raise RuntimeError("React index has no bundled JavaScript assets")
    for name in assets:
        asset = resource_root / "web/dist" / name.lstrip("/")
        if not asset.is_file() or not asset.stat().st_size:
            raise RuntimeError(f"missing React asset: {name}")

    for name, _url, expected in ocr.V6_TINY_MODELS:
        model = resource_root / "models/rapidocr" / name
        if (not model.is_file()
                or hashlib.sha256(model.read_bytes()).hexdigest() != expected):
            raise RuntimeError(f"missing or corrupt bundled OCR model: {name}")
    return len(assets)


def _check_ocr(ocr) -> dict:
    import numpy as np
    from rapidocr.ch_ppocr_rec.typings import TextRecInput

    engine = ocr.get_engine()
    blank = np.full((96, 320, 3), 255, dtype=np.uint8)
    # An empty detection result skips classification and recognition in the
    # combined OCR call. Exercise each model explicitly so all three run ONNX.
    detection = engine.text_det(blank)
    classification = engine.text_cls([blank])
    if classification.img_list is None or len(classification.img_list) != 1:
        raise RuntimeError("OCR classifier did not process the blank image")
    recognition = engine.text_rec(TextRecInput(img=classification.img_list))
    if recognition.txts is None or len(recognition.txts) != 1:
        raise RuntimeError("OCR recognizer did not process the blank image")
    boxes = getattr(detection, "boxes", None)
    return {
        "ocrModels": 3,
        "ocrModelsExecuted": ["det", "cls", "rec"],
        "ocrDetectedLines": len(boxes) if boxes is not None else 0,
    }


def _self_test() -> dict:
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        raise RuntimeError("self-test requires a frozen Windows executable")

    with tempfile.TemporaryDirectory(prefix="qqpet-windows-self-test-") as temporary:
        data_root = Path(temporary) / "data"
        with _isolated_environment(data_root), _offline_network():
            config = importlib.import_module("src.config")
            if (config.APP_ROOT.resolve() != data_root.resolve()
                    or config.CONFIG_FILE.resolve() != (data_root / "config.yaml").resolve()):
                raise RuntimeError("self-test must start before application module imports")
            resource_root = Path(sys._MEIPASS)
            if config.RESOURCE_ROOT.resolve() != resource_root.resolve():
                raise RuntimeError("resource root does not use the frozen bundle")
            data_root.mkdir()
            (data_root / "runs").mkdir()
            # Only the public example is read; a user's portable config is
            # neither loaded nor copied into the temporary directory.
            shutil.copyfile(resource_root / "config.example.yaml", data_root / "config.yaml")
            config.load_config()
            modules = {name: importlib.import_module(name) for name in IMPORTS}
            if modules["dashboard"].RUNS.resolve() != (data_root / "runs").resolve():
                raise RuntimeError("dashboard writes outside the isolated data root")
            ocr = modules["src.ocr"]
            if ocr.MODEL_ROOT.resolve() != (data_root / "runs/models/rapidocr").resolve():
                raise RuntimeError("OCR writes outside the isolated data root")
            asset_count = _check_resources(resource_root, ocr)

            from src.runtime_process import build_scheduler_argv

            scheduler = Path(sys.executable).with_name("QQPetCopilotScheduler.exe")
            command = build_scheduler_argv(resource_root)
            if command != [str(scheduler)] or not scheduler.is_file():
                raise RuntimeError("the sibling scheduler executable is missing")
            dashboard = Path(sys.executable).with_name("QQPetCopilot.exe")
            if not dashboard.is_file():
                raise RuntimeError("the sibling dashboard executable is missing")
            adb = resource_root / "resources/platform-tools/adb.exe"
            if Path(config.find_adb()).resolve() != adb.resolve():
                raise RuntimeError("ADB does not resolve to the bundled executable")
            version = subprocess.run(
                [str(adb), "version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if "Android Debug Bridge version" not in version.stdout:
                raise RuntimeError("bundled ADB returned an unexpected version response")
            bridge = (resource_root / "frida_tools/bridges/java.js").read_text(encoding="utf-8")
            if "globalThis.Java = bridge" not in modules["src.opener"]._wrap_java_bridge(bridge):
                raise RuntimeError("Frida Java bridge could not be prepared")
            frida_servers = []
            for arch in ("arm64", "x86_64"):
                name = f"frida-server-{modules['frida'].__version__}-android-{arch}.xz"
                with lzma.open(resource_root / "resources/frida-server" / name, "rb") as archive:
                    if archive.read(4) != b"\x7fELF":
                        raise RuntimeError(f"invalid bundled Frida server: {name}")
                frida_servers.append(name)

            probe = data_root / "runs/write-probe"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
            ocr_result = _check_ocr(ocr)
            return {
                "ok": True, "frozen": True, "platform": sys.platform,
                "configSeeded": True, "dataRootIsolated": True,
                "imports": list(IMPORTS), "reactAssets": asset_count,
                "schedulerCommand": command, "adbVersion": version.stdout.splitlines()[0],
                **ocr_result,
                "fridaBridgeReady": True, "minitouchBinaries": 4,
                "fridaServers": frida_servers,
                "networkDisabled": True, "deviceConnected": False,
                "telegramStarted": False, "schedulerStarted": False,
            }


def main(argv: list[str] | None = None, *, entrypoint: str) -> int:
    """Emit one JSON result, also supporting windowed EXEs without stdout."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    output = None
    try:
        if arguments[:1] != ["--self-test"]:
            raise ValueError("expected --self-test")
        remaining = arguments[1:]
        if remaining:
            if len(remaining) != 2 or remaining[0] != "--self-test-output":
                raise ValueError("usage: --self-test [--self-test-output PATH]")
            output = Path(remaining[1])
        diagnostics = sys.stderr if sys.stderr is not None else io.StringIO()
        with redirect_stdout(diagnostics), redirect_stderr(diagnostics):
            result = _self_test()
        status = 0
    except Exception as error:
        result = {"ok": False, "errors": [f"{type(error).__name__}: {error}"]}
        status = 1
    result["entrypoint"] = entrypoint
    text = json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n"
    if output is not None:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
        except OSError as error:
            result = {"ok": False, "entrypoint": entrypoint,
                      "errors": [f"cannot write self-test report: {error}"]}
            text = json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n"
            status = 1
    if sys.stdout is not None:
        sys.stdout.write(text)
        sys.stdout.flush()
    return status
