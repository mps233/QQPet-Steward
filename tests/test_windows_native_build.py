from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import yaml
from PIL import Image

from desktop.windows import build_native as native
from desktop.windows.verify_portable import VerificationError


REPO = Path(__file__).resolve().parents[1]
WINDOWS_CONFIG = (REPO / "desktop/windows/tauri.windows.conf.json").read_text("utf-8")


def write(path: Path, content: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def native_inputs(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    desktop = root / "desktop" / "macos"
    windows = root / "desktop" / "windows"
    paths = {
        "ROOT": root, "DESKTOP_DIR": desktop, "WINDOWS_DIR": windows,
        "BUILD_ROOT": root / "build", "DIST_ROOT": root / "dist",
        "RUNTIME_STAGE": desktop / "target/runtime/QQPetRuntime",
        "CONFIG": windows / "tauri.windows.conf.json",
        "ICON_SOURCE": desktop / "icons/icon.png",
        "ICON_PATH": root / "build/windows-native/QQPet-Steward.ico",
        "TAURI_CLI": desktop / "node_modules/@tauri-apps/cli/tauri.js",
    }
    for name, value in paths.items():
        monkeypatch.setattr(native, name, value)
    monkeypatch.setattr(native.build_portable, "ROOT", root)
    monkeypatch.setattr(native.shutil, "which", lambda tool: f"/tools/{tool}")
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    for relative in (
        "desktop/macos/Cargo.toml", "desktop/macos/Cargo.lock",
        "desktop/macos/app/index.html", "desktop/macos/app/panel.html",
        "desktop/macos/node_modules/@tauri-apps/cli/tauri.js", "web/dist/index.html",
    ):
        write(root / relative)
    write(paths["CONFIG"], WINDOWS_CONFIG.encode("utf-8"))
    write(desktop / "tauri.conf.json", json.dumps({
        "version": "0.8.2",
        "bundle": {"resources": {"target/runtime/QQPetRuntime/": "backend/"}},
    }).encode("utf-8"))
    paths["ICON_SOURCE"].parent.mkdir(parents=True)
    Image.new("RGBA", (512, 512), (255, 196, 0, 255)).save(paths["ICON_SOURCE"])
    source = native.portable_directory()
    for name in native.BACKEND_EXECUTABLES:
        write(source / name, b"MZ" + name.encode("ascii"))
    write(source / "_internal/python313.dll")
    write(source / "_internal/config.example.yaml")
    write(source / "_internal/rapidocr/config.yaml")
    write(source / "config.example.yaml")
    write(source / "start_portable.bat", b"browser-launch-entry")
    (source / "runs").mkdir()
    return source


def test_native_configuration_inherits_one_backend_mapping(native_inputs):
    assert native.validate_inputs() == []
    windows = json.loads(native.CONFIG.read_text("utf-8"))
    assert "resources" not in windows["bundle"]
    assert windows["mainBinaryName"] == "QQPet-Steward"
    assert windows["bundle"]["targets"] == ["nsis"]
    settings = windows["bundle"]["windows"]
    assert settings["nsis"]["installMode"] == "currentUser"
    assert settings["webviewInstallMode"] == {"type": "downloadBootstrapper", "silent": True}
    assert settings["nsis"]["startMenuFolder"] == "QQPet-Steward"
    assert windows["app"]["windows"][0]["label"] == "launcher"


def test_native_check_is_read_only(native_inputs, monkeypatch):
    before = {path: path.read_bytes() for path in native.ROOT.rglob("*") if path.is_file()}
    monkeypatch.setattr(native, "run", lambda *_a, **_kw: pytest.fail("unexpected subprocess"))
    assert native.main(["--check"]) == 0
    assert before == {path: path.read_bytes() for path in native.ROOT.rglob("*") if path.is_file()}


def test_rejects_duplicate_resource_override(native_inputs):
    config = json.loads(native.CONFIG.read_text("utf-8"))
    config["bundle"]["resources"] = {"old-macos-runtime": "backend/"}
    native.CONFIG.write_text(json.dumps(config), encoding="utf-8")
    assert any("inherit" in problem for problem in native.validate_inputs())


def test_native_python_preparation_builds_and_verifies_before_staging(native_inputs, monkeypatch):
    commands = []
    monkeypatch.setattr(native, "run", lambda command, **_kw: commands.append(command))
    assert native.prepare_python(build=True, fetch=False) == native_inputs
    assert Path(commands[0][1]).name == "build_portable.py"
    assert commands[0][-2:] == ["--no-zip", "--no-fetch"]
    assert Path(commands[1][1]).name == "verify_portable.py"
    assert commands[1][commands[1].index("--directory") + 1] == str(native_inputs)


def test_reusing_python_still_runs_frozen_verification(native_inputs, monkeypatch):
    commands = []
    monkeypatch.setattr(native, "run", lambda command, **_kw: commands.append(command))
    native.prepare_python(build=False, fetch=False)
    assert len(commands) == 1
    assert Path(commands[0][1]).name == "verify_portable.py"


def test_stage_excludes_browser_launchers_and_has_content_fingerprint(native_inputs):
    first = native.stage_backend(native_inputs)
    assert len(first) == 64
    assert (native.RUNTIME_STAGE / ".build-fingerprint").read_text("ascii") == first + "\n"
    assert native.backend_fingerprint(native.RUNTIME_STAGE) == first
    assert (native.RUNTIME_STAGE / "_internal/rapidocr/config.yaml").is_file()
    assert not (native.RUNTIME_STAGE / "runs").exists()
    assert not (native.RUNTIME_STAGE / "start_portable.bat").exists()
    assert not (native.RUNTIME_STAGE / "config.example.yaml").exists()
    assert {path.name for path in native.RUNTIME_STAGE.glob("*.exe")} == set(native.BACKEND_EXECUTABLES)
    assert native.stage_backend(native_inputs) == first
    write(native_inputs / "QQPetCopilot.exe", b"MZchanged-backend")
    assert native.stage_backend(native_inputs) != first


def test_stage_rejects_private_runtime_data(native_inputs):
    write(native_inputs / "runs" / "dashboard_access.token", b"private")
    with pytest.raises(VerificationError, match="user data"):
        native.stage_backend(native_inputs)
    assert not native.RUNTIME_STAGE.exists()


def test_icon_is_generated_from_png_in_all_windows_sizes(native_inputs):
    assert not native.ICON_PATH.exists()
    assert native.create_windows_icon() == native.ICON_PATH
    with Image.open(native.ICON_PATH) as image:
        assert image.format == "ICO"
        assert image.ico.sizes() == {(size, size) for size in (16, 24, 32, 48, 64, 128, 256)}


def test_tauri_build_requires_fresh_versioned_installer(native_inputs, monkeypatch):
    installer = native.target_directory() / "release/bundle/nsis/QQPet-Steward_0.8.2_x64-setup.exe"
    write(installer, b"MZold-installer")
    monkeypatch.setattr(native, "run", lambda *_a, **_kw: None)
    with pytest.raises(ValueError, match="did not produce"):
        native.build_tauri()
    assert not installer.exists()


def test_tauri_command_uses_windows_override_and_locked_cargo(native_inputs, monkeypatch):
    commands = []
    installer = native.target_directory() / "release/bundle/nsis/QQPet-Steward_0.8.2_x64-setup.exe"

    def build(command, **kwargs):
        commands.append((command, kwargs))
        write(installer, b"MZnew-installer")

    monkeypatch.setattr(native, "run", build)
    assert native.build_tauri() == installer
    command, kwargs = commands[0]
    assert command[command.index("--config") + 1] == str(native.CONFIG)
    assert command[-4:] == ["--bundles", "nsis", "--", "--locked"]
    assert kwargs["cwd"] == native.DESKTOP_DIR


def test_native_outputs_contain_one_user_entry_and_verified_backend(native_inputs):
    native.stage_backend(native_inputs)
    write(native.target_directory() / "release" / native.MAIN_EXECUTABLE, b"MZnative-app")
    release = native.prepare_native_directory()
    assert (release / "backend/.build-fingerprint").is_file()
    installer = write(native.BUILD_ROOT / "original-setup.exe", b"MZnsis-installer")
    outputs = native.publish_outputs(installer, release, native_zip=True)
    names = {path.name for path in outputs}
    assert names == {
        "QQPet-Steward_0.8.2_windows_x64-setup.exe",
        "QQPet-Steward_0.8.2_windows_x64-setup.exe.sha256",
        "QQPet-Steward_0.8.2_windows_x64-native.zip",
        "QQPet-Steward_0.8.2_windows_x64-native.zip.sha256",
    }
    for output in outputs:
        if output.suffix == ".sha256":
            artifact = output.with_suffix("")
            assert output.read_text("ascii").startswith(native.build_portable.file_sha256(artifact))
    archive = next(path for path in outputs if path.suffix == ".zip")
    with zipfile.ZipFile(archive) as stream:
        members = stream.namelist()
    prefix = "QQPet-Steward_0.8.2_windows_x64-native/"
    assert [name for name in members if name.startswith(prefix) and "/" not in name[len(prefix):]
            and name.endswith(".exe")] == [prefix + native.MAIN_EXECUTABLE]
    assert prefix + "backend/QQPetCopilot.exe" in members
    assert prefix + "backend/QQPetCopilotScheduler.exe" in members
    assert not any("start_portable" in name for name in members)


def test_ci_uploads_only_after_native_gui_verification():
    workflow = yaml.safe_load((REPO / ".github/workflows/windows-desktop.yml").read_text("utf-8"))
    job = workflow["jobs"]["windows-x64"]
    assert job["runs-on"] == "windows-2025"
    steps = job["steps"]
    verification = next(index for index, step in enumerate(steps)
                        if "verify_native.py" in step.get("run", ""))
    upload = next(index for index, step in enumerate(steps)
                  if step.get("name") == "Upload verified Windows App")
    assert verification < upload
    assert "if" not in steps[upload]
    assert "windows_x64-setup.exe" in steps[upload]["with"]["path"]
