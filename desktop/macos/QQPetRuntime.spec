# -*- mode: python ; coding: utf-8 -*-
"""One shared runtime for the macOS app's local services."""
import os
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

DESKTOP = Path(SPECPATH).resolve()
ROOT = DESKTOP.parent.parent
MODEL_NAMES = (
    "PP-OCRv6_det_tiny.onnx",
    "PP-OCRv6_rec_tiny.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
)

datas = [
    (str(ROOT / "config.example.yaml"), "."),
    (str(ROOT / "web" / "dist"), "web/dist"),
    (str(ROOT / "static"), "static"),
    (str(ROOT / "scenarios" / "assets"), "scenarios/assets"),
]
binaries = []
hiddenimports = [
    "dashboard", "scenarios.runner", "tools.career_sync", "src.runtime_process",
    "onnxruntime", "rapidocr.inference_engine.onnxruntime.main",
    "qrcode", "onepush", "frida_tools",
]

for name in MODEL_NAMES:
    candidates = (ROOT / "models" / "rapidocr" / name,
                  ROOT / "runs" / "models" / "rapidocr" / name)
    model = next((path for path in candidates if path.is_file()), None)
    if model is None:
        raise SystemExit(f"Missing OCR model {name}; run tools/fetch_ocr_models.py first")
    datas.append((str(model), "models/rapidocr"))

datas += collect_data_files("uiautomator2")
rapid_datas, rapid_binaries, rapid_hidden = collect_all(
    "rapidocr",
    include_py_files=False,
    filter_submodules=lambda name: not any(
        part in name.split(".") for part in ("pytorch", "paddle", "tensorrt", "openvino", "mnn")
    ),
)
datas += [item for item in rapid_datas if not item[0].lower().endswith(".onnx")]
binaries += rapid_binaries
hiddenimports += rapid_hidden

frida_datas, frida_binaries, frida_hidden = collect_all("frida", include_py_files=False)
datas += frida_datas
binaries += frida_binaries
hiddenimports += frida_hidden
datas += collect_data_files("frida_tools", includes=["bridges/java.js"])

for relative in ("resources/minitouch", "resources/frida-server"):
    source = ROOT / relative
    if source.is_dir():
        datas.append((str(source), relative))

adb_path = os.environ.get("QQPET_BUILD_ADB") or shutil.which("adb")
if not adb_path:
    raise SystemExit("ADB is required; set QQPET_BUILD_ADB to an existing macOS platform-tools/adb")
adb = Path(adb_path).expanduser().resolve()
if not adb.is_file():
    raise SystemExit(f"ADB does not exist: {adb}")
# Collect as a binary so PyInstaller relocates any non-system dylib dependencies.
binaries.append((str(adb), "resources/platform-tools"))
for name in ("NOTICE.txt", "source.properties"):
    notice = adb.parent / name
    if notice.is_file():
        datas.append((str(notice), "resources/platform-tools"))

a = Analysis(
    [str(DESKTOP / "runtime_launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "PyQt6", "PyQt5", "tkinter", "torch", "paddle", "openvino",
        "tensorrt", "MNN", "prompt_toolkit", "pygments", "websockets",
        "matplotlib", "kiwisolver",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QQPetRuntime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="QQPetRuntime",
)
