# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller entry for the hosted scheduler.

The dashboard is a separate executable.  A frozen dashboard cannot append
``scenarios/runner.py`` to its own ``sys.executable`` without relaunching the
dashboard, so the portable package ships this sibling executable.  The launcher
binds QQPET_DATA_DIR before executing scenarios.runner as ``__main__``.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).resolve().parent.parent.parent
WINDOWS_DIR = Path(SPECPATH).resolve().parent

datas = []
binaries = []
hiddenimports = ["scenarios.runner", "src.runtime_process"]

# Runtime data used by the scheduler and shared by the dashboard package.
datas.append((str(ROOT / "config.example.yaml"), "."))
if (ROOT / "models" / "rapidocr").is_dir():
    datas.append((str(ROOT / "models" / "rapidocr"), "models/rapidocr"))
if (ROOT / "resources").is_dir():
    datas.append((str(ROOT / "resources"), "resources"))

datas += collect_data_files("uiautomator2")
rapidocr_datas, rapidocr_binaries, rapidocr_hidden = collect_all("rapidocr")
# The project uses the PP-OCRv6 tiny models copied through RESOURCE_ROOT, not
# the larger v4/v5 models shipped inside the rapidocr wheel.
datas += [item for item in rapidocr_datas
          if not str(item[0]).lower().endswith(".onnx")]
binaries += rapidocr_binaries
hiddenimports += rapidocr_hidden

a = Analysis(
    [str(WINDOWS_DIR / "scheduler_launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "frida", "frida._frida", "frida.aio", "frida_tools",
        "prompt_toolkit", "pygments", "websockets", "wcwidth",
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
    ],
    noarchive=False,
    optimize=0,
)

# Screenshot OCR does not read video; excluding this DLL saves ~31 MB.
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
    name="QQPetCopilotScheduler",
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
    name="QQPetCopilotScheduler",
)
