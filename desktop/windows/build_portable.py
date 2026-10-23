"""Build a portable Windows onedir package without touching the dev checkout.

Usage (Windows):
    .venv\\Scripts\\python.exe desktop\\windows\\build_portable.py
    .venv\\Scripts\\python.exe desktop\\windows\\build_portable.py --check

The first milestone is deliberately uninstalled/portable.  MSI, shortcuts,
uninstall, and autostart belong to the second milestone.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINDOWS_DIR = Path(__file__).resolve().parent
PORTABLE_NAME = "QQPetCopilot-Windows-x64"
BUILD_ROOT = ROOT / "build"
DIST_ROOT = ROOT / "dist"
PORTABLE_DIR = DIST_ROOT / PORTABLE_NAME
ZIP_PATH = DIST_ROOT / f"{PORTABLE_NAME}.zip"
DASHBOARD_SPEC = WINDOWS_DIR / "QQPetCopilotDashboard.spec"
SCHEDULER_SPEC = WINDOWS_DIR / "QQPetCopilotScheduler.spec"


def validate_inputs() -> list[str]:
    """Return blocking problems without writing or launching anything."""
    problems: list[str] = []
    if not (ROOT / "dashboard.py").is_file():
        problems.append("dashboard.py is missing")
    if not (ROOT / "scenarios" / "runner.py").is_file():
        problems.append("scenarios/runner.py is missing")
    if not (WINDOWS_DIR / "dashboard_launcher.py").is_file():
        problems.append("desktop/windows/dashboard_launcher.py is missing")
    if not (WINDOWS_DIR / "scheduler_launcher.py").is_file():
        problems.append("desktop/windows/scheduler_launcher.py is missing")
    if not (ROOT / "web" / "dist" / "index.html").is_file():
        problems.append("web/dist/index.html is missing; run the frontend build first")
    if not (ROOT / "static").is_dir():
        problems.append("static/ is missing")
    if not (ROOT / "config.example.yaml").is_file():
        problems.append("config.example.yaml is missing")
    for spec in (DASHBOARD_SPEC, SCHEDULER_SPEC):
        if not spec.is_file():
            problems.append(f"{spec.relative_to(ROOT)} is missing")
    return problems


def check_pyinstaller() -> str | None:
    """Return an actionable message when PyInstaller is unavailable."""
    code = (
        "import PyInstaller; "
        "print(PyInstaller.__version__)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except OSError as exc:
        return f"cannot run PyInstaller check: {exc}"
    if result.returncode != 0:
        return (
            "PyInstaller is not installed in this interpreter; run "
            f"{sys.executable} -m pip install pyinstaller"
        )
    print(f"PyInstaller {result.stdout.strip()}")
    return None


def prepare_resources(*, fetch: bool) -> None:
    """Populate optional runtime assets without failing the whole build."""
    if not fetch:
        return
    commands = [
        [sys.executable, str(ROOT / "tools" / "fetch_ocr_models.py")],
        [sys.executable, str(ROOT / "tools" / "fetch_minitouch.py"),
         "--arch", "x86_64", "arm64-v8a"],
        [sys.executable, str(ROOT / "tools" / "fetch_scrcpy.py")],
    ]
    for command in commands:
        script = Path(command[1])
        if not script.is_file():
            print(f"skip missing prep script: {script}", flush=True)
            continue
        print("prepare:", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            print(f"warning: prep command exited {result.returncode}", flush=True)


def run_pyinstaller(spec: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_ROOT / "pyinstaller"),
        str(spec),
    ]
    print("build:", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def merge_onedir_outputs() -> None:
    """Combine the two one-dir outputs into one portable directory."""
    dashboard_dir = DIST_ROOT / "QQPetCopilot"
    scheduler_dir = DIST_ROOT / "QQPetCopilotScheduler"
    if not (dashboard_dir / "QQPetCopilot.exe").is_file():
        raise SystemExit(f"missing dashboard build: {dashboard_dir}")
    if not (scheduler_dir / "QQPetCopilotScheduler.exe").is_file():
        raise SystemExit(f"missing scheduler build: {scheduler_dir}")
    if PORTABLE_DIR.exists():
        shutil.rmtree(PORTABLE_DIR)
    shutil.copytree(dashboard_dir, PORTABLE_DIR)
    for item in scheduler_dir.iterdir():
        destination = PORTABLE_DIR / item.name
        if item.is_dir():
            if destination.exists():
                # Colliding payloads come from the scheduler spec; copy them
                # recursively so a future spec change cannot silently lose files.
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)

    # Keep mutable portable data beside the executables, not inside a temp dir.
    (PORTABLE_DIR / "runs").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "config.example.yaml", PORTABLE_DIR / "config.example.yaml")
    for name in ("start_portable.bat", "start_portable.ps1", "stop_portable.ps1"):
        shutil.copy2(WINDOWS_DIR / name, PORTABLE_DIR / name)
    shutil.copy2(WINDOWS_DIR / "README.md", PORTABLE_DIR / "README.md")


def make_zip() -> Path:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    archive = shutil.make_archive(str(ZIP_PATH.with_suffix("")), "zip",
                                  root_dir=DIST_ROOT, base_dir=PORTABLE_NAME)
    return Path(archive)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="validate inputs and PyInstaller; do not build")
    parser.add_argument("--no-fetch", action="store_true",
                        help="skip OCR/model/resource download preparation")
    parser.add_argument("--clean", action="store_true",
                        help="remove build/pyinstaller and portable output first")
    parser.add_argument("--no-zip", action="store_true",
                        help="leave the portable directory without creating a zip")
    args = parser.parse_args(argv)

    problems = validate_inputs()
    if problems:
        print("blocked:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    if os.name != "nt":
        print("Windows builder selected; macOS/Linux only performs --check.",
              file=sys.stderr)
        if not args.check:
            return 1

    missing = check_pyinstaller()
    if missing:
        print(f"blocked: {missing}", file=sys.stderr)
        return 2
    if args.check:
        print("Windows portable input validation passed.")
        return 0

    if args.clean:
        shutil.rmtree(BUILD_ROOT / "pyinstaller", ignore_errors=True)
        shutil.rmtree(PORTABLE_DIR, ignore_errors=True)
        ZIP_PATH.unlink(missing_ok=True)
    prepare_resources(fetch=not args.no_fetch)
    run_pyinstaller(DASHBOARD_SPEC)
    run_pyinstaller(SCHEDULER_SPEC)
    merge_onedir_outputs()
    final = PORTABLE_DIR if args.no_zip else make_zip()
    print(f"portable package ready: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
