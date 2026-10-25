"""Build the native Windows App and its per-user NSIS installer.

Build on Windows x64 with the frontend and desktop Node dependencies installed.
``--prepare-only`` builds and verifies Python, stages the backend and generates
the Windows icon. ``--skip-runtime-build`` reuses an existing Python build.
``--check`` only inspects the source configuration and required tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop.windows import build_portable
from desktop.windows.verify_portable import VerificationError, check_package


WINDOWS_DIR = Path(__file__).resolve().parent
DESKTOP_DIR = ROOT / "desktop" / "macos"
CONFIG = WINDOWS_DIR / "tauri.windows.conf.json"
BUILD_ROOT = ROOT / "build"
DIST_ROOT = ROOT / "dist"
RUNTIME_STAGE = DESKTOP_DIR / "target" / "runtime" / "QQPetRuntime"
ICON_PATH = BUILD_ROOT / "windows-native" / "QQPet-Steward.ico"
ICON_SOURCE = DESKTOP_DIR / "icons" / "icon.png"
TAURI_CLI = DESKTOP_DIR / "node_modules" / "@tauri-apps" / "cli" / "tauri.js"
BACKEND_EXECUTABLES = ("QQPetCopilot.exe", "QQPetCopilotScheduler.exe")
MAIN_EXECUTABLE = "QQPet-Steward.exe"


def release_name(version: str) -> str:
    return f"QQPet-Steward_{version}_windows_x64"


def portable_directory() -> Path:
    return DIST_ROOT / build_portable.portable_name(build_portable.read_version())


def target_directory() -> Path:
    configured = os.environ.get("CARGO_TARGET_DIR")
    if not configured:
        return DESKTOP_DIR / "target"
    path = Path(configured).expanduser()
    return path.resolve() if path.is_absolute() else (DESKTOP_DIR / path).resolve()


def validate_inputs() -> list[str]:
    problems = []
    for path in (
        CONFIG, DESKTOP_DIR / "tauri.conf.json", DESKTOP_DIR / "Cargo.toml",
        DESKTOP_DIR / "Cargo.lock", DESKTOP_DIR / "app" / "index.html",
        DESKTOP_DIR / "app" / "panel.html", ICON_SOURCE, TAURI_CLI,
        ROOT / "web" / "dist" / "index.html",
    ):
        if not path.is_file():
            problems.append(f"required input is missing: {path}")
    for tool in ("node", "cargo"):
        if not shutil.which(tool):
            problems.append(f"{tool} is not on PATH")
    if problems:
        return problems
    try:
        base = json.loads((DESKTOP_DIR / "tauri.conf.json").read_text("utf-8"))
        windows = json.loads(CONFIG.read_text("utf-8"))
        if base["bundle"]["resources"] != {"target/runtime/QQPetRuntime/": "backend/"}:
            problems.append("the base Tauri configuration must map only the staged backend resource")
        if "resources" in windows["bundle"]:
            problems.append("Windows override must inherit the base backend resource mapping")
        if windows["mainBinaryName"] + ".exe" != MAIN_EXECUTABLE:
            problems.append("the Windows executable name does not match the native entry point")
        if windows["bundle"]["targets"] != ["nsis"]:
            problems.append("the Windows release target must be NSIS")
        if windows["bundle"]["windows"]["nsis"]["installMode"] != "currentUser":
            problems.append("the Windows installer must install for the current user")
        build_portable.read_version()
    except (OSError, KeyError, TypeError, ValueError) as exc:
        problems.append(f"invalid desktop configuration: {exc}")
    return problems


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("run:", " ".join(command), flush=True)
    subprocess.run(
        command, cwd=cwd, check=True,
        env=dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1"),
    )


def prepare_python(*, build: bool, fetch: bool) -> Path:
    if build:
        command = [
            sys.executable, str(WINDOWS_DIR / "build_portable.py"), "--no-zip",
        ]
        if not fetch:
            command.append("--no-fetch")
        run(command)
    directory = portable_directory()
    run([
        sys.executable, str(WINDOWS_DIR / "verify_portable.py"),
        "--directory", str(directory),
        "--report", str(BUILD_ROOT / "windows-native-backend-verification.json"),
    ])
    return directory


def backend_fingerprint(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name == ".build-fingerprint":
            continue
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(build_portable.file_sha256(path)))
    return digest.hexdigest()


def stage_backend(source: Path) -> str:
    """Stage only the internal runtime; browser launch scripts stay out."""
    check_package(source)
    for name in BACKEND_EXECUTABLES:
        if not (source / name).is_file():
            raise ValueError(f"missing verified Python executable: {name}")
    if not (source / "_internal").is_dir():
        raise ValueError("the Python _internal runtime directory is missing")
    temporary = RUNTIME_STAGE.with_name(RUNTIME_STAGE.name + ".windows-building")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    try:
        shutil.copytree(source / "_internal", temporary / "_internal")
        for name in BACKEND_EXECUTABLES:
            shutil.copy2(source / name, temporary / name)
        fingerprint = backend_fingerprint(temporary)
        (temporary / ".build-fingerprint").write_text(fingerprint + "\n", encoding="ascii")
        if RUNTIME_STAGE.exists():
            shutil.rmtree(RUNTIME_STAGE)
        temporary.replace(RUNTIME_STAGE)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    print(f"staged backend fingerprint: {fingerprint}")
    return fingerprint


def create_windows_icon() -> Path:
    from PIL import Image

    ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(ICON_SOURCE) as image:
        image.convert("RGBA").save(
            ICON_PATH, format="ICO",
            sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)],
        )
    return ICON_PATH


def build_tauri() -> Path:
    nsis_directory = target_directory() / "release" / "bundle" / "nsis"
    expected = nsis_directory / f"QQPet-Steward_{build_portable.read_version()}_x64-setup.exe"
    expected.unlink(missing_ok=True)
    run([
        shutil.which("node") or "node", str(TAURI_CLI), "build", "--ci",
        "--config", str(CONFIG), "--bundles", "nsis", "--", "--locked",
    ], cwd=DESKTOP_DIR)
    if not expected.is_file():
        raise ValueError(f"Tauri did not produce the expected installer: {expected}")
    return expected


def checksum_file(path: Path) -> Path:
    checksum = path.with_name(path.name + ".sha256")
    checksum.write_text(
        f"{build_portable.file_sha256(path)}  {path.name}\n", encoding="ascii",
    )
    return checksum


def prepare_native_directory() -> Path:
    """Keep the same App/backend layout available for native GUI verification."""
    release = target_directory() / "release"
    executable = release / MAIN_EXECUTABLE
    if not executable.is_file():
        raise ValueError(f"the native App executable is missing: {executable}")
    backend = release / "backend"
    shutil.rmtree(backend, ignore_errors=True)
    shutil.copytree(RUNTIME_STAGE, backend)
    return release


def publish_outputs(installer: Path, release: Path, *, native_zip: bool) -> list[Path]:
    with installer.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("the generated NSIS installer is not a Windows executable")
    name = release_name(build_portable.read_version())
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    destination = DIST_ROOT / f"{name}-setup.exe"
    shutil.copy2(installer, destination)
    outputs = [destination, checksum_file(destination)]
    if native_zip:
        directory = DIST_ROOT / f"{name}-native"
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir()
        shutil.copy2(release / MAIN_EXECUTABLE, directory / MAIN_EXECUTABLE)
        shutil.copytree(release / "backend", directory / "backend")
        archive = Path(shutil.make_archive(
            str(directory), "zip", root_dir=DIST_ROOT, base_dir=directory.name,
        ))
        outputs.extend((archive, checksum_file(archive)))
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="inspect source configuration without writing")
    parser.add_argument("--prepare-only", action="store_true", help="prepare and verify Python resources and icon")
    parser.add_argument("--skip-runtime-build", action="store_true", help="verify and reuse an existing Python build")
    parser.add_argument("--no-fetch", action="store_true", help="require existing Python runtime resources")
    parser.add_argument("--no-native-zip", action="store_true", help="produce only the NSIS installer download")
    args = parser.parse_args(argv)
    try:
        problems = validate_inputs()
        if problems:
            raise ValueError("\n".join(problems))
        if args.check:
            print("Windows native build input validation passed.")
            return 0
        if os.name != "nt" or sysconfig.get_platform() != "win-amd64":
            raise ValueError("the native Windows release must be built with Windows x64 Python")
        directory = prepare_python(build=not args.skip_runtime_build, fetch=not args.no_fetch)
        stage_backend(directory)
        create_windows_icon()
        if args.prepare_only:
            print(f"native Windows resources ready: {RUNTIME_STAGE}")
            return 0
        installer = build_tauri()
        release = prepare_native_directory()
        for output in publish_outputs(installer, release, native_zip=not args.no_native_zip):
            print(f"native Windows output: {output}")
        return 0
    except (OSError, ValueError, VerificationError, subprocess.SubprocessError) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
