from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from desktop.macos import restore_runtime_symlinks as restore


TOOL = Path(restore.__file__)


def runtime(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "QQPetRuntime").write_bytes(b"bundled-runtime")
    (root / "_internal").mkdir()
    return root


def pair(tmp_path: Path) -> tuple[Path, Path]:
    source = runtime(tmp_path / "source")
    library = source / "_internal" / "package" / "lib.dylib"
    library.parent.mkdir()
    library.write_bytes(b"library-contents")
    (source / "_internal" / "lib.dylib").symlink_to("package/lib.dylib")
    destination = tmp_path / "destination"
    shutil.copytree(source, destination, symlinks=False)
    return source, destination


def snapshot(root: Path) -> dict[str, bytes | str]:
    result = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                result[relative] = f"link:{path.readlink()}"
            elif path.is_file():
                result[relative] = path.read_bytes()
            else:
                result[relative] = "directory"
    return result


def test_restores_113_library_links_and_is_idempotent(tmp_path):
    source = runtime(tmp_path / "source")
    libraries = source / "_internal" / "cv2" / ".dylibs"
    libraries.mkdir(parents=True)
    expected_size = 0
    for index in range(113):
        name = f"lib{index}.dylib"
        content = bytes([index]) * (index + 1)
        (libraries / name).write_bytes(content)
        (source / "_internal" / name).symlink_to(f"cv2/.dylibs/{name}")
        expected_size += len(content)
    destination = tmp_path / "destination"
    shutil.copytree(source, destination, symlinks=False)
    before = snapshot(source)
    result = restore.restore_runtime_symlinks(source, destination)
    assert result == {
        "ok": True, "links": 113, "restored": 113,
        "already_present": 0, "saved_bytes": expected_size,
    }
    assert snapshot(destination) == before
    assert snapshot(source) == before
    expected = {
        "ok": True, "links": 113, "restored": 0,
        "already_present": 113, "saved_bytes": 0,
    }
    assert restore.restore_runtime_symlinks(source, destination) == expected
    assert restore.restore_runtime_symlinks(source, destination, check=True) == expected


@pytest.mark.parametrize("omitted", [False, True])
def test_restores_python_framework_directory_links(tmp_path, omitted):
    source = runtime(tmp_path / "source")
    framework = source / "_internal" / "Python.framework"
    version = framework / "Versions" / "3.13"
    resources = version / "Resources"
    resources.mkdir(parents=True)
    (resources / "Info.plist").write_bytes(b"framework-information")
    (version / "Python").write_bytes(b"framework-library")
    (framework / "Versions" / "Current").symlink_to("3.13", target_is_directory=True)
    (framework / "Resources").symlink_to(
        "Versions/Current/Resources", target_is_directory=True
    )
    destination = tmp_path / "destination"
    shutil.copytree(source, destination, symlinks=False)
    if omitted:
        shutil.rmtree(destination / "_internal" / "Python.framework" / "Resources")
        shutil.rmtree(destination / "_internal" / "Python.framework" / "Versions" / "Current")
    result = restore.restore_runtime_symlinks(source, destination)
    assert result["links"] == result["restored"] == 2
    expected_bytes = 2 * len(b"framework-information") + len(b"framework-library")
    assert result["saved_bytes"] == (0 if omitted else expected_bytes)
    assert snapshot(destination) == snapshot(source)
    assert (destination / framework.relative_to(source) / "Resources" / "Info.plist").read_bytes() == b"framework-information"
    assert restore.restore_runtime_symlinks(source, destination, check=True)["restored"] == 0


def test_check_is_read_only_for_expanded_files(tmp_path):
    source, destination = pair(tmp_path)
    before = snapshot(destination)
    with pytest.raises(restore.BundleSymlinkError, match="symlinks are expanded or missing"):
        restore.restore_runtime_symlinks(source, destination, check=True)
    assert snapshot(destination) == before


def test_source_can_be_read_only(tmp_path):
    source, destination = pair(tmp_path)
    paths = [source] + [path for path in source.rglob("*") if not path.is_symlink()]
    for path in paths:
        path.chmod(0o555 if path.is_dir() else 0o444)
    before = snapshot(source)
    try:
        assert restore.restore_runtime_symlinks(source, destination)["restored"] == 1
        assert snapshot(source) == before
        assert all(path.stat().st_mode & 0o222 == 0 for path in paths)
    finally:
        for path in paths:
            path.chmod(0o755 if path.is_dir() else 0o644)


@pytest.mark.parametrize("mode", ["missing", "different", "directory"])
def test_refuses_missing_or_changed_destination_before_any_replacement(tmp_path, mode):
    source, destination = pair(tmp_path)
    (source / "_internal" / "z-extra").symlink_to("package/lib.dylib")
    extra = destination / "_internal" / "z-extra"
    if mode == "different":
        extra.write_bytes(b"different")
    elif mode == "directory":
        extra.mkdir()
    before = snapshot(destination)
    with pytest.raises((restore.BundleSymlinkError, OSError)):
        restore.restore_runtime_symlinks(source, destination)
    assert snapshot(destination) == before
    assert not (destination / "_internal" / "lib.dylib").is_symlink()


@pytest.mark.parametrize("mode", ["absolute", "escape", "dangling", "cycle"])
def test_rejects_unsafe_source_links(tmp_path, mode):
    source, destination = pair(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    targets = {
        "absolute": str(outside),
        "escape": "../../outside",
        "dangling": "not-here",
        "cycle": "bad",
    }
    (source / "_internal" / "bad").symlink_to(targets[mode])
    (destination / "_internal" / "bad").write_bytes(b"placeholder")
    before = snapshot(destination)
    with pytest.raises((restore.BundleSymlinkError, OSError)):
        restore.restore_runtime_symlinks(source, destination)
    assert snapshot(destination) == before


@pytest.mark.parametrize("mode", ["wrong", "dangling", "absolute", "escape"])
def test_rejects_wrong_or_unsafe_destination_links(tmp_path, mode):
    source, destination = pair(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"library-contents")
    (destination / "_internal" / "package" / "other.dylib").write_bytes(b"library-contents")
    path = destination / "_internal" / "lib.dylib"
    path.unlink()
    path.symlink_to({
        "wrong": "package/other.dylib", "dangling": "not-here",
        "absolute": str(outside), "escape": "../../outside",
    }[mode])
    before = snapshot(destination)
    for check in (False, True):
        with pytest.raises((restore.BundleSymlinkError, OSError)):
            restore.restore_runtime_symlinks(source, destination, check=check)
    assert snapshot(destination) == before


def test_rejects_destination_target_escape(tmp_path):
    source, destination = pair(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"library-contents")
    library = destination / "_internal" / "package" / "lib.dylib"
    library.unlink()
    library.symlink_to("../../../outside")
    before = snapshot(destination)
    with pytest.raises(restore.BundleSymlinkError, match="escapes runtime"):
        restore.restore_runtime_symlinks(source, destination)
    assert snapshot(destination) == before


def test_rejects_destination_parent_link_even_when_it_stays_inside(tmp_path):
    source, destination = pair(tmp_path)
    (source / "_internal" / "package" / "alias").symlink_to("lib.dylib")
    package = destination / "_internal" / "package"
    package.rename(destination / "_internal" / "real-package")
    package.symlink_to("real-package")
    before = snapshot(destination)
    with pytest.raises(restore.BundleSymlinkError, match="parent must be a real directory"):
        restore.restore_runtime_symlinks(source, destination)
    assert snapshot(destination) == before


@pytest.mark.parametrize("mode", ["same", "nested", "symlink", "missing-marker"])
def test_validates_runtime_roots(tmp_path, mode):
    source, destination = pair(tmp_path)
    if mode == "same":
        destination = source
    elif mode == "nested":
        destination = source / "nested"
        shutil.copytree(tmp_path / "destination", destination)
    elif mode == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(destination, target_is_directory=True)
        destination = alias
    else:
        (destination / "QQPetRuntime").unlink()
    before = snapshot(source)
    with pytest.raises(restore.BundleSymlinkError):
        restore.restore_runtime_symlinks(source, destination)
    assert snapshot(source) == before


def test_directory_difference_is_not_deleted(tmp_path):
    source, destination = pair(tmp_path)
    (source / "_internal" / "directory").symlink_to("package", target_is_directory=True)
    shutil.copytree(destination / "_internal" / "package", destination / "_internal" / "directory")
    (destination / "_internal" / "directory" / "extra").write_bytes(b"keep me")
    before = snapshot(destination)
    with pytest.raises(restore.BundleSymlinkError, match="directory differs"):
        restore.restore_runtime_symlinks(source, destination)
    assert snapshot(destination) == before


def test_directory_replacement_failure_rolls_back(tmp_path, monkeypatch):
    source = runtime(tmp_path / "source")
    package = source / "_internal" / "package"
    package.mkdir()
    (package / "file").write_bytes(b"keep me")
    (source / "_internal" / "directory").symlink_to("package", target_is_directory=True)
    destination = tmp_path / "destination"
    shutil.copytree(source, destination)
    before = snapshot(destination)

    def fail_replace(_path, _target):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement"):
        restore.restore_runtime_symlinks(source, destination)
    assert snapshot(destination) == before
    assert not list(destination.rglob(".restore-links-*"))


def test_cli_reports_json_and_check_exit_status(tmp_path):
    source, destination = pair(tmp_path)
    arguments = [
        sys.executable, str(TOOL),
        "--source", str(source), "--destination", str(destination),
    ]
    checked = subprocess.run(arguments + ["--check"], capture_output=True, text=True, timeout=10)
    assert checked.returncode == 1
    assert json.loads(checked.stderr)["ok"] is False
    completed = subprocess.run(arguments, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["restored"] == 1
    checked = subprocess.run(arguments + ["--check"], capture_output=True, text=True, timeout=10)
    assert checked.returncode == 0
    assert json.loads(checked.stdout)["already_present"] == 1
