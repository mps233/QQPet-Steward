# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller entry for the Windows hosted dashboard.

The launcher binds this frozen executable to PyInstaller's unpacked resource
root before importing ``dashboard``.  The scheduler is a sibling executable
built by QQPetCopilotScheduler.spec.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).resolve().parent.parent.parent

datas = []
binaries = []
hiddenimports = []

datas += [
    (str(ROOT / "config.example.yaml"), "."),
    (str(ROOT / "web" / "dist"), "web/dist"),
    (str(ROOT / "static"), "static"),
]
if (ROOT / "models" / "rapidocr").is_dir():
    datas.append((str(ROOT / "models" / "rapidocr"), "models/rapidocr"))
if (ROOT / "resources").is_dir():
    datas.append((str(ROOT / "resources"), "resources"))

datas += collect_data_files("uiautomator2")
rapidocr_datas, rapidocr_binaries, rapidocr_hidden = collect_all("rapidocr")
datas += [item for item in rapidocr_datas
          if not str(item[0]).lower().endswith(".onnx")]
binaries += rapidocr_binaries
hiddenimports += rapidocr_hidden

a = Analysis(
    [str(ROOT / "desktop" / "windows" / "dashboard_launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["dashboard_launcher", "src.runtime_process"],
    excludes=[
        "frida", "frida._frida", "frida.aio", "frida_tools",
        "prompt_toolkit", "pygments", "websockets", "wcwidth",
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
    ],
    noarchive=False,
    optimize=0,
)
a.binaries = [
    item for item in a.binaries
    if "opencv_videoio_ffmpeg" not in item[0].lower()
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QQPetCopilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="QQPetCopilot",
)
