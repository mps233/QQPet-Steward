"""Build a complete Windows x64 portable ZIP on a Windows host.

``--check`` validates the existing inputs without downloading or building.
``--no-fetch`` builds with existing resources and rejects missing assets.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINDOWS_DIR = Path(__file__).resolve().parent
BUILD_ROOT = ROOT / "build"
DIST_ROOT = ROOT / "dist"
RESOURCE_DATA_ROOT = BUILD_ROOT / "windows-resource-data"
DASHBOARD_SPEC = WINDOWS_DIR / "QQPetCopilotDashboard.spec"
SCHEDULER_SPEC = WINDOWS_DIR / "QQPetCopilotScheduler.spec"
MINITOUCH_ABIS = ("armeabi-v7a", "arm64-v8a", "x86", "x86_64")
FRIDA_ARCHES = ("arm64", "x86_64")
ADB_FILES = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
PORTABLE_FILES = (
    "start_portable.bat", "start_portable.ps1", "stop_portable.ps1", "README.md",
)
UNUSED_BACKENDS = ("pytorch", "paddle", "tensorrt", "openvino", "mnn")


def read_version() -> str:
    version = json.loads(
        (ROOT / "desktop" / "macos" / "tauri.conf.json").read_text("utf-8")
    )["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version
    ):
        raise ValueError(f"invalid release version: {version!r}")
    return version


def portable_name(version: str) -> str:
    return f"QQPet-Steward_{version}_windows_x64"


def model_manifest() -> list[tuple[str, str, str]]:
    """Read model names and trusted hashes without importing runtime code."""
    tree = ast.parse((ROOT / "src" / "ocr.py").read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "V6_TINY_MODELS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("src/ocr.py does not define V6_TINY_MODELS")


def frida_version() -> str:
    requirement = (ROOT / "requirements.txt").read_text("utf-8")
    match = re.search(r"(?m)^frida==([0-9.]+)\s*$", requirement)
    if not match:
        raise ValueError("requirements.txt must pin the frida client version")
    return match.group(1)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_model(name: str, expected: str) -> Path | None:
    roots = (
        RESOURCE_DATA_ROOT / "runs" / "models" / "rapidocr",
        ROOT / "models" / "rapidocr",
        ROOT / "runs" / "models" / "rapidocr",
    )
    return next(
        (root / name for root in roots
         if (root / name).is_file() and file_sha256(root / name) == expected),
        None,
    )


def adb_directory() -> Path | None:
    configured = os.environ.get("QQPET_BUILD_ADB")
    candidates = (
        [Path(configured).expanduser().resolve().parent] if configured else []
    ) + [ROOT / "resources" / "platform-tools", ROOT / "resources" / "scrcpy-win64"]
    return next(
        (path for path in candidates if all((path / name).is_file() for name in ADB_FILES)),
        None,
    )


def java_bridge() -> Path | None:
    spec = importlib.util.find_spec("frida_tools")
    if spec is None or not spec.origin:
        return None
    path = Path(spec.origin).parent / "bridges" / "java.js"
    return path if path.is_file() and path.stat().st_size else None


def validate_inputs() -> list[str]:
    """Return blocking source problems without writing or launching anything."""
    required = (
        ROOT / "dashboard.py", ROOT / "scenarios" / "runner.py",
        ROOT / "web" / "dist" / "index.html", ROOT / "config.example.yaml",
        WINDOWS_DIR / "dashboard_launcher.py", WINDOWS_DIR / "scheduler_launcher.py",
        WINDOWS_DIR / "runtime_self_test.py", DASHBOARD_SPEC, SCHEDULER_SPEC,
        *(WINDOWS_DIR / name for name in PORTABLE_FILES),
    )
    problems = [f"{path.relative_to(ROOT)} is missing" for path in required
                if not path.is_file()]
    for relative in ("static", "scenarios/assets"):
        if not (ROOT / relative).is_dir():
            problems.append(f"{relative}/ is missing")
    try:
        read_version()
        model_manifest()
        frida_version()
    except (KeyError, OSError, ValueError, SyntaxError) as exc:
        problems.append(str(exc))
    return problems


def validate_resources() -> list[str]:
    """Reject incomplete downloads, mismatched OCR models and missing bridges."""
    problems = []
    for name, _url, expected in model_manifest():
        if find_model(name, expected) is None:
            problems.append(f"missing or invalid OCR model: {name}; run tools/fetch_ocr_models.py")
    for abi in MINITOUCH_ABIS:
        path = ROOT / "resources" / "minitouch" / f"minitouch-{abi}"
        try:
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    raise ValueError("not an Android ELF binary")
        except (OSError, ValueError) as exc:
            problems.append(f"invalid minitouch {abi}: {exc}")
    version = frida_version()
    for arch in FRIDA_ARCHES:
        path = ROOT / "resources" / "frida-server" / f"frida-server-{version}-android-{arch}.xz"
        try:
            with lzma.open(path, "rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    raise ValueError("not an Android ELF binary")
                # Read the footer so a truncated archive cannot ship.
                while stream.read(1024 * 1024):
                    pass
        except (OSError, EOFError, lzma.LZMAError, ValueError) as exc:
            problems.append(f"invalid frida-server {arch}: {exc}")
    adb = adb_directory()
    if adb is None:
        problems.append("Windows ADB and both DLLs are missing; run tools/fetch_scrcpy.py")
    else:
        for name in ADB_FILES:
            with (adb / name).open("rb") as stream:
                if stream.read(2) != b"MZ":
                    problems.append(f"invalid Windows ADB binary: {adb / name}")
    if java_bridge() is None:
        problems.append("frida_tools/bridges/java.js is missing; install requirements.txt")
    try:
        installed = importlib.metadata.version("frida")
        if installed != version:
            problems.append(f"frida {installed} does not match required server version {version}")
    except importlib.metadata.PackageNotFoundError:
        problems.append("frida is not installed; install requirements.txt")
    return problems


def check_pyinstaller() -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"cannot run PyInstaller check: {exc}"
    if result.returncode != 0:
        return f"PyInstaller is not installed; run {sys.executable} -m pip install pyinstaller"
    print(f"PyInstaller {result.stdout.strip()}")
    return None


def prepare_resources(*, fetch: bool) -> None:
    if not fetch:
        return
    commands = [
        [sys.executable, str(ROOT / "tools" / "fetch_ocr_models.py")],
        [sys.executable, str(ROOT / "tools" / "fetch_minitouch.py"), "--arch", *MINITOUCH_ABIS],
        [sys.executable, str(ROOT / "tools" / "fetch_frida_server.py"),
         "--version", frida_version(), "--arch", *FRIDA_ARCHES],
        [sys.executable, str(ROOT / "tools" / "fetch_scrcpy.py")],
    ]
    # fetch_ocr_models imports log/config; isolate their writes from real runs/.
    env = dict(os.environ, QQPET_DATA_DIR=str(RESOURCE_DATA_ROOT), PYTHONUTF8="1")
    for command in commands:
        print("prepare:", " ".join(command), flush=True)
        subprocess.run(command, cwd=ROOT, env=env, check=True)


def runtime_spec_inputs() -> tuple[list, list, list]:
    """Use the same complete dependency/resource graph for both executables."""
    from PyInstaller.utils.hooks import collect_all, collect_data_files

    from desktop.windows.runtime_self_test import IMPORTS

    problems = validate_resources()
    if problems:
        raise SystemExit("\n".join(problems))
    datas = [
        (str(ROOT / "config.example.yaml"), "."),
        (str(ROOT / "web" / "dist"), "web/dist"),
        (str(ROOT / "static"), "static"),
        (str(ROOT / "scenarios" / "assets"), "scenarios/assets"),
    ]
    for name, _url, expected in model_manifest():
        datas.append((str(find_model(name, expected)), "models/rapidocr"))
    for abi in MINITOUCH_ABIS:
        datas.append((str(ROOT / "resources" / "minitouch" / f"minitouch-{abi}"),
                      "resources/minitouch"))
    for arch in FRIDA_ARCHES:
        name = f"frida-server-{frida_version()}-android-{arch}.xz"
        datas.append((str(ROOT / "resources" / "frida-server" / name), "resources/frida-server"))
    adb = adb_directory()
    binaries = [(str(adb / name), "resources/platform-tools") for name in ADB_FILES]
    for name in ("NOTICE.txt", "source.properties", "LICENSE"):
        if (adb / name).is_file():
            datas.append((str(adb / name), "resources/platform-tools"))
    datas += collect_data_files("uiautomator2")
    rapid_datas, rapid_binaries, rapid_hidden = collect_all(
        "rapidocr", include_py_files=False, exclude_datas=["**/*.onnx"],
        filter_submodules=lambda name: not any(
            part in name.split(".") for part in UNUSED_BACKENDS
        ),
    )
    datas += rapid_datas
    binaries += rapid_binaries
    frida_datas, frida_binaries, frida_hidden = collect_all("frida", include_py_files=False)
    datas += frida_datas
    binaries += frida_binaries
    datas += collect_data_files("frida_tools", includes=["bridges/java.js"])
    hiddenimports = [
        "dashboard", "scenarios.runner", "tools.career_sync", "src.runtime_process",
        "desktop.windows.runtime_self_test", "qrcode", "onepush", "frida_tools",
        "onnxruntime", "rapidocr.inference_engine.onnxruntime.main",
        *IMPORTS, *rapid_hidden, *frida_hidden,
    ]
    return datas, binaries, hiddenimports


def entry_scripts(scripts: list, launcher: str) -> list:
    """Keep runtime hooks and one launcher from the shared two-entry analysis."""
    other = "scheduler_launcher.py" if launcher == "dashboard_launcher.py" else "dashboard_launcher.py"
    return [item for item in scripts if Path(item[1]).name != other]


def run_pyinstaller(spec: Path) -> None:
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(DIST_ROOT), "--workpath", str(BUILD_ROOT / "pyinstaller"),
        str(spec),
    ]
    print("build:", " ".join(command), flush=True)
    subprocess.run(
        command, cwd=ROOT, check=True,
        env=dict(os.environ, PYTHONHASHSEED="0", PYTHONUTF8="1"),
    )


def merge_onedir_outputs(portable_dir: Path) -> None:
    """Check shared files before copying; never overwrite different runtimes."""
    sources = (DIST_ROOT / "QQPetCopilot", DIST_ROOT / "QQPetCopilotScheduler")
    for directory, exe in zip(sources, ("QQPetCopilot.exe", "QQPetCopilotScheduler.exe")):
        if not (directory / exe).is_file():
            raise ValueError(f"missing executable: {directory / exe}")
    entries: dict[str, tuple[Path, Path]] = {}
    for directory in sources:
        for source in sorted(directory.rglob("*")):
            relative = source.relative_to(directory)
            parts = tuple(part.casefold() for part in relative.parts)
            if source.is_symlink():
                raise ValueError(f"portable output contains a symlink: {relative}")
            if ("runs" in parts or relative.suffix.casefold() == ".token"
                    or (relative.name.casefold() in {"config.yaml", "config.yml"}
                        and parts != ("_internal", "rapidocr", "config.yaml"))):
                raise ValueError(f"portable output contains private data: {relative}")
            key = relative.as_posix().casefold()
            previous = entries.get(key)
            if previous:
                old, old_relative = previous
                if (relative != old_relative or old.is_dir() != source.is_dir()
                        or (source.is_file() and file_sha256(old) != file_sha256(source))):
                    raise ValueError(f"cannot merge different runtime payloads: {relative}")
            else:
                entries[key] = source, relative
    staging = portable_dir.with_name(portable_dir.name + ".building")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        for source, relative in entries.values():
            destination = staging / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        (staging / "runs").mkdir()
        shutil.copy2(ROOT / "config.example.yaml", staging / "config.example.yaml")
        for name in PORTABLE_FILES:
            shutil.copy2(WINDOWS_DIR / name, staging / name)
        if portable_dir.exists():
            shutil.rmtree(portable_dir)
        staging.replace(portable_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def make_zip(portable_dir: Path) -> Path:
    archive = shutil.make_archive(
        str(portable_dir), "zip", root_dir=portable_dir.parent, base_dir=portable_dir.name
    )
    path = Path(archive)
    checksum = file_sha256(path)
    path.with_suffix(".zip.sha256").write_text(f"{checksum}  {path.name}\n", encoding="ascii")
    print(f"SHA256: {checksum}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate existing inputs without writing")
    parser.add_argument("--no-fetch", action="store_true", help="require already prepared resources")
    parser.add_argument("--clean", action="store_true", help="remove this version's previous build outputs")
    parser.add_argument("--no-zip", action="store_true", help="leave the portable directory without a ZIP")
    args = parser.parse_args(argv)
    try:
        problems = validate_inputs()
        if problems:
            raise ValueError("\n".join(problems))
        if os.name != "nt" and not args.check:
            raise ValueError("Windows packages must be built on Windows; use --check to inspect inputs")
        if os.name == "nt" and sysconfig.get_platform() != "win-amd64":
            raise ValueError("use Windows x64 Python to build the windows_x64 package")
        missing = check_pyinstaller()
        if missing:
            raise ValueError(missing)
        if not args.check:
            prepare_resources(fetch=not args.no_fetch)
        problems = validate_resources()
        if problems:
            raise ValueError("\n".join(problems))
        if args.check:
            print("Windows portable input and resource validation passed.")
            return 0
        portable_dir = DIST_ROOT / portable_name(read_version())
        if args.clean:
            shutil.rmtree(BUILD_ROOT / "pyinstaller", ignore_errors=True)
            shutil.rmtree(portable_dir, ignore_errors=True)
            Path(str(portable_dir) + ".zip").unlink(missing_ok=True)
            Path(str(portable_dir) + ".zip.sha256").unlink(missing_ok=True)
        run_pyinstaller(DASHBOARD_SPEC)
        run_pyinstaller(SCHEDULER_SPEC)
        merge_onedir_outputs(portable_dir)
        final = portable_dir if args.no_zip else make_zip(portable_dir)
        print(f"portable package ready: {final}")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
