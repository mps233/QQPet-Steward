# -*- mode: python ; coding: utf-8 -*-
"""Windows scheduler with the same dependency graph as its sibling dashboard."""
import sys
from pathlib import Path

WINDOWS_DIR = Path(SPECPATH).resolve()
ROOT = WINDOWS_DIR.parent.parent
sys.path.insert(0, str(ROOT))
from desktop.windows.build_portable import entry_scripts, runtime_spec_inputs

datas, binaries, hiddenimports = runtime_spec_inputs()

a = Analysis(
    [str(WINDOWS_DIR / "dashboard_launcher.py"),
     str(WINDOWS_DIR / "scheduler_launcher.py")],
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
a.binaries = [
    item for item in a.binaries
    if "opencv_videoio_ffmpeg" not in item[0].lower()
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    entry_scripts(a.scripts, "scheduler_launcher.py"),
    [],
    exclude_binaries=True,
    name="QQPetCopilotScheduler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=False, name="QQPetCopilotScheduler",
)
