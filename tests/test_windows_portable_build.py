from __future__ import annotations

import hashlib
import importlib.util
import lzma
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop.windows import build_portable as builder


REPO = Path(__file__).resolve().parents[1]
MODEL_NAMES = (
    "PP-OCRv6_det_tiny.onnx",
    "PP-OCRv6_rec_tiny.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
)


def write(path: Path, content: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def package_inputs(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    windows = root / "desktop" / "windows"
    resource_data = root / "build" / "windows-resource-data"
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "WINDOWS_DIR", windows)
    monkeypatch.setattr(builder, "BUILD_ROOT", root / "build")
    monkeypatch.setattr(builder, "DIST_ROOT", root / "dist")
    monkeypatch.setattr(builder, "RESOURCE_DATA_ROOT", resource_data)
    monkeypatch.setattr(builder, "DASHBOARD_SPEC", windows / "QQPetCopilotDashboard.spec")
    monkeypatch.setattr(builder, "SCHEDULER_SPEC", windows / "QQPetCopilotScheduler.spec")
    monkeypatch.delenv("QQPET_BUILD_ADB", raising=False)
    for relative in (
        "dashboard.py", "scenarios/runner.py", "web/dist/index.html",
        "static/icon.png", "scenarios/assets/home_bag_tied.png", "config.example.yaml",
        *(f"desktop/windows/{name}" for name in builder.PORTABLE_FILES),
        "desktop/windows/dashboard_launcher.py", "desktop/windows/scheduler_launcher.py",
        "desktop/windows/runtime_self_test.py", "desktop/windows/QQPetCopilotDashboard.spec",
        "desktop/windows/QQPetCopilotScheduler.spec",
    ):
        write(root / relative)
    write(root / "desktop" / "macos" / "tauri.conf.json", b'{"version":"0.8.1"}')
    write(root / "requirements.txt", b"frida==17.17.0\n")
    models = []
    for name in MODEL_NAMES:
        content = name.encode("ascii")
        models.append((name, "https://invalid.test/model", hashlib.sha256(content).hexdigest()))
        write(resource_data / "runs" / "models" / "rapidocr" / name, content)
    write(root / "src" / "ocr.py", f"V6_TINY_MODELS = {models!r}\n".encode("ascii"))
    for abi in builder.MINITOUCH_ABIS:
        write(root / "resources" / "minitouch" / f"minitouch-{abi}", b"\x7fELFminitouch")
    for arch in builder.FRIDA_ARCHES:
        write(root / "resources" / "frida-server" / f"frida-server-17.17.0-android-{arch}.xz",
              lzma.compress(b"\x7fELFfrida"))
    for name in builder.ADB_FILES:
        write(root / "resources" / "scrcpy-win64" / name, b"MZwindows-adb")
    bridge = write(root / "bridge" / "java.js", b"var bridge = {};")
    monkeypatch.setattr(builder, "java_bridge", lambda: bridge)
    monkeypatch.setattr(builder.importlib.metadata, "version", lambda _name: "17.17.0")
    return root


def test_existing_inputs_include_all_offline_assets(package_inputs):
    assert builder.read_version() == "0.8.1"
    assert builder.portable_name(builder.read_version()) == "QQPet-Steward_0.8.1_windows_x64"
    assert builder.validate_inputs() == []
    assert builder.validate_resources() == []


def test_check_is_read_only_and_never_prepares_resources(package_inputs, monkeypatch):
    before = {p: p.read_bytes() for p in package_inputs.rglob("*") if p.is_file()}
    monkeypatch.setattr(builder, "check_pyinstaller", lambda: None)
    monkeypatch.setattr(builder, "prepare_resources", lambda **_kw: pytest.fail("unexpected download"))
    monkeypatch.setattr(builder, "run_pyinstaller", lambda *_a: pytest.fail("unexpected build"))
    assert builder.main(["--check", "--no-fetch"]) == 0
    assert before == {p: p.read_bytes() for p in package_inputs.rglob("*") if p.is_file()}


@pytest.mark.parametrize("resource", ["model", "minitouch", "frida", "adb", "bridge", "client-version"])
def test_rejects_missing_or_corrupt_runtime_resource(package_inputs, monkeypatch, resource):
    if resource == "model":
        write(builder.RESOURCE_DATA_ROOT / "runs" / "models" / "rapidocr" / MODEL_NAMES[0], b"bad model")
    elif resource == "minitouch":
        write(package_inputs / "resources/minitouch/minitouch-x86", b"<html>bad CDN response</html>")
    elif resource == "frida":
        path = package_inputs / "resources/frida-server/frida-server-17.17.0-android-arm64.xz"
        path.write_bytes(path.read_bytes()[:-4])
    elif resource == "adb":
        (package_inputs / "resources/scrcpy-win64/AdbWinApi.dll").unlink()
    elif resource == "bridge":
        monkeypatch.setattr(builder, "java_bridge", lambda: None)
    else:
        monkeypatch.setattr(builder.importlib.metadata, "version", lambda _name: "17.0.0")
    assert builder.validate_resources()


def test_failed_download_stops_preparation(package_inputs, monkeypatch):
    calls = []

    def fail(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(builder.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        builder.prepare_resources(fetch=True)
    assert len(calls) == 1
    assert calls[0][1]["check"] is True
    assert calls[0][1]["env"]["QQPET_DATA_DIR"] == str(builder.RESOURCE_DATA_ROOT)
    assert calls[0][1]["env"]["PYTHONUTF8"] == "1"


def test_no_fetch_skips_downloads(package_inputs, monkeypatch):
    monkeypatch.setattr(builder.subprocess, "run", lambda *_a, **_kw: pytest.fail("download"))
    builder.prepare_resources(fetch=False)


def test_spec_inputs_exclude_extra_models_and_unrelated_resources(package_inputs, monkeypatch):
    import PyInstaller.utils.hooks as hooks

    write(package_inputs / "resources" / "unrelated-huge.bin")
    write(package_inputs / "runs" / "config.yaml", b"private-token")
    write(package_inputs / "models" / "rapidocr" / "unused-huge.onnx")
    collections = {}

    def collect_all(package, **kwargs):
        collections[package] = kwargs
        return [], [], [package]

    monkeypatch.setattr(hooks, "collect_all", collect_all)
    monkeypatch.setattr(hooks, "collect_data_files", lambda *_a, **_kw: [])
    datas, binaries, hidden = builder.runtime_spec_inputs()
    assert {Path(source).name for source, destination in datas if destination == "models/rapidocr"} == set(MODEL_NAMES)
    assert len([item for item in datas if item[1] == "resources/minitouch"]) == 4
    assert len([item for item in datas if item[1] == "resources/frida-server"]) == 2
    assert {Path(source).name for source, destination in binaries
            if destination == "resources/platform-tools"} == set(builder.ADB_FILES)
    assert not any("unrelated" in source or "config.yaml" == Path(source).name for source, _ in datas)
    assert "desktop.windows.runtime_self_test" in hidden
    assert "frida" in hidden
    rapid = collections["rapidocr"]
    assert rapid["include_py_files"] is False
    assert "**/*.onnx" in rapid["exclude_datas"]
    assert rapid["filter_submodules"]("rapidocr.inference_engine.onnxruntime.main")
    assert not rapid["filter_submodules"]("rapidocr.inference_engine.pytorch.main")


def onedir_outputs():
    for name, exe in (("QQPetCopilot", "QQPetCopilot.exe"),
                      ("QQPetCopilotScheduler", "QQPetCopilotScheduler.exe")):
        directory = builder.DIST_ROOT / name
        write(directory / exe, exe.encode("ascii"))
        write(directory / "_internal" / "base_library.zip", b"same stdlib")
    return builder.DIST_ROOT / builder.portable_name(builder.read_version())


def test_merge_preserves_executables_and_zip_contains_no_user_data(package_inputs):
    destination = onedir_outputs()
    write(builder.DIST_ROOT / "QQPetCopilotScheduler" / "_internal" / "scheduler-extra.dll")
    builder.merge_onedir_outputs(destination)
    assert (destination / "QQPetCopilot.exe").is_file()
    assert (destination / "QQPetCopilotScheduler.exe").is_file()
    assert (destination / "_internal" / "scheduler-extra.dll").is_file()
    assert list((destination / "runs").iterdir()) == []
    assert not (destination / "config.yaml").exists()
    archive = builder.make_zip(destination)
    assert archive.name == "QQPet-Steward_0.8.1_windows_x64.zip"
    with zipfile.ZipFile(archive) as stream:
        names = stream.namelist()
    assert f"{destination.name}/QQPetCopilot.exe" in names
    assert f"{destination.name}/start_portable.bat" in names
    assert not any(name.endswith("/config.yaml") for name in names)
    assert archive.with_suffix(".zip.sha256").read_text("ascii").startswith(builder.file_sha256(archive))


def test_merge_rejects_conflict_before_replacing_existing_package(package_inputs):
    destination = onedir_outputs()
    write(destination / "previous-good-package")
    write(builder.DIST_ROOT / "QQPetCopilotScheduler" / "_internal" / "base_library.zip", b"different stdlib")
    with pytest.raises(ValueError, match="different runtime payloads"):
        builder.merge_onedir_outputs(destination)
    assert (destination / "previous-good-package").is_file()
    assert not destination.with_name(destination.name + ".building").exists()


def test_merge_allows_only_the_bundled_rapidocr_default_config(package_inputs):
    destination = onedir_outputs()
    rapidocr = importlib.util.find_spec("rapidocr")
    config = (Path(rapidocr.origin).parent / "config.yaml").read_bytes()
    relative = Path("_internal/rapidocr/config.yaml")
    for output in ("QQPetCopilot", "QQPetCopilotScheduler"):
        write(builder.DIST_ROOT / output / relative, config)
    builder.merge_onedir_outputs(destination)
    assert (destination / relative).read_bytes() == config
    with zipfile.ZipFile(builder.make_zip(destination)) as archive:
        assert archive.read(f"{destination.name}/{relative.as_posix()}") == config


@pytest.mark.parametrize("relative", [
    "config.yaml", "config.yml", "_internal/config.yaml",
    "_internal/rapidocr/config.yml", "_internal/rapidocr/backup/config.yaml",
    "runs/access_key.txt", "_internal/RUNS/queue_status.json", "dashboard_access.token",
])
def test_merge_rejects_private_payloads(package_inputs, relative):
    destination = onedir_outputs()
    write(builder.DIST_ROOT / "QQPetCopilot" / relative, b"private")
    with pytest.raises(ValueError, match="private data"):
        builder.merge_onedir_outputs(destination)
    assert not destination.exists()


def test_specs_analyze_both_launchers_but_execute_only_their_own(monkeypatch):
    analyses = []
    executables = []
    monkeypatch.setattr(builder, "runtime_spec_inputs", lambda: ([], [], []))

    def analysis(scripts, **kwargs):
        analyses.append((scripts, kwargs))
        return SimpleNamespace(
            pure=[], binaries=[], datas=[],
            scripts=[("hook", "/hook.py", "PYSOURCE"),
                     *[(Path(path).stem, path, "PYSOURCE") for path in scripts]],
        )

    def exe(_pyz, scripts, _empty, **kwargs):
        executables.append((scripts, kwargs))
        return object()

    for name in ("QQPetCopilotDashboard.spec", "QQPetCopilotScheduler.spec"):
        path = REPO / "desktop" / "windows" / name
        namespace = {
            "SPECPATH": str(path.parent), "Analysis": analysis,
            "EXE": exe, "PYZ": lambda _pure: object(), "COLLECT": lambda *_a, **_kw: None,
        }
        exec(compile(path.read_text("utf-8"), str(path), "exec"), namespace)
    assert analyses[0] == analyses[1]
    assert [Path(entry[1]).name for entry in executables[0][0]] == ["hook.py", "dashboard_launcher.py"]
    assert [Path(entry[1]).name for entry in executables[1][0]] == ["hook.py", "scheduler_launcher.py"]
    assert all(exe_config["console"] is False for _scripts, exe_config in executables)
