from __future__ import annotations

import json
from pathlib import Path
import plistlib
import struct
import subprocess
import sys

import pytest

from desktop.macos import check_bundle_compatibility as compatibility


ARM64 = 0x0100000C
X86_64 = 0x01000007
TOOL = Path(compatibility.__file__)


def thin(
    version=(11, 0, 0), *, cpu=ARM64, endian="<", is_64=True,
    old=False, platform=1, no_version=False,
):
    subtype = 3 if cpu == X86_64 else 0
    encoded = version[0] << 16 | version[1] << 8 | version[2]
    if no_version:
        commands = b""
    elif old:
        commands = struct.pack(endian + "IIII", 0x24, 16, encoded, encoded)
    else:
        commands = struct.pack(endian + "IIIIII", 0x32, 24, platform, encoded, encoded, 0)
    header = struct.pack(
        endian + "IIIIIII", 0xFEEDFACF if is_64 else 0xFEEDFACE,
        cpu, subtype, 2, bool(commands), len(commands), 0,
    )
    return header + (b"\0" * 4 if is_64 else b"") + commands


def fat(*slices, endian=">", is_64=False):
    entry_size = 32 if is_64 else 20
    offset = 8 + entry_size * len(slices)
    table = []
    for cpu, data in slices:
        subtype = 3 if cpu == X86_64 else 0
        if is_64:
            table.append(struct.pack(endian + "IIQQII", cpu, subtype, offset, len(data), 0, 0))
        else:
            table.append(struct.pack(endian + "IIIII", cpu, subtype, offset, len(data), 0))
        offset += len(data)
    return (
        struct.pack(endian + "II", 0xCAFEBABF if is_64 else 0xCAFEBABE, len(slices))
        + b"".join(table) + b"".join(data for _cpu, data in slices)
    )


def bundle(tmp_path, *, minimum="14.0", main=None):
    app = tmp_path / "Example App.app"
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True)
    information = {
        "LSMinimumSystemVersion": minimum,
        "CFBundleExecutable": "example",
    }
    with (app / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump(information, handle)
    (macos / "example").write_bytes(thin() if main is None else main)
    return app


def put(app, relative, data):
    path = app / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_normal_bundle_reports_targets_without_executing_files(tmp_path):
    app = bundle(tmp_path)
    put(app, "Contents/Resources/runtime.dylib", thin((14, 0, 0)))
    put(app, "Contents/Resources/icon.png", b"ordinary resource")
    result = compatibility.check_bundle_compatibility(app)
    assert result == {
        "ok": True, "macho_files": 2, "slices": 2, "architectures": ["arm64"],
        "minimum_system_version": "14.0", "max_minos": "14.0", "errors": [],
    }
    assert not (app / "Contents/MacOS/example").stat().st_mode & 0o111


def test_rejects_high_target_with_relative_path_and_version(tmp_path):
    app = bundle(tmp_path)
    put(app, "Contents/Resources/Python", thin((26, 0, 0)))
    result = compatibility.check_bundle_compatibility(app)
    assert not result["ok"]
    assert result["max_minos"] == "26.0"
    assert result["errors"] == [
        "Contents/Resources/Python: arm64 requires macOS 26.0; bundle declares 14.0"
    ]


@pytest.mark.parametrize("endian,is_64", [("<", False), (">", False), ("<", True), (">", True)])
def test_reads_old_macos_load_command_and_both_endians(tmp_path, endian, is_64):
    app = bundle(tmp_path, minimum="10.10.2", main=thin(
        (10, 10, 2), cpu=7, old=True, endian=endian, is_64=is_64,
    ))
    assert compatibility.check_bundle_compatibility(app)["max_minos"] == "10.10.2"


@pytest.mark.parametrize("endian,is_64", [(">", False), ("<", False), (">", True), ("<", True)])
def test_reads_fat_and_fat64_architectures(tmp_path, endian, is_64):
    binary = fat(
        (ARM64, thin((11, 0, 0))), (X86_64, thin((10, 13, 0), cpu=X86_64)),
        endian=endian, is_64=is_64,
    )
    app = bundle(tmp_path, main=binary)
    put(app, "Contents/Frameworks/Universal.dylib", binary)
    result = compatibility.check_bundle_compatibility(app)
    assert result["ok"]
    assert result["architectures"] == ["arm64", "x86_64"]
    assert result["macho_files"] == 2
    assert result["slices"] == 4


def test_universal_main_requires_dependencies_to_cover_both_architectures(tmp_path):
    app = bundle(tmp_path, main=fat(
        (ARM64, thin()), (X86_64, thin(cpu=X86_64)),
    ))
    put(app, "Contents/Resources/arm-only.dylib", thin())
    result = compatibility.check_bundle_compatibility(app)
    assert not result["ok"]
    assert "missing architecture x86_64" in result["errors"][0]


def test_universal_dependency_can_contain_extra_architectures(tmp_path):
    app = bundle(tmp_path)
    put(app, "Contents/Resources/universal.dylib", fat(
        (ARM64, thin()), (X86_64, thin(cpu=X86_64)),
    ))
    assert compatibility.check_bundle_compatibility(app)["ok"]


def test_file_and_directory_symlinks_are_not_followed_or_double_counted(tmp_path):
    app = bundle(tmp_path)
    library = put(app, "Contents/Resources/actual/library.dylib", thin())
    (library.parent.parent / "alias.dylib").symlink_to("actual/library.dylib")
    (library.parent.parent / "alias-directory").symlink_to("actual", target_is_directory=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "too-new.dylib").write_bytes(thin((26, 0, 0)))
    (library.parent.parent / "outside").symlink_to(outside, target_is_directory=True)
    (library.parent.parent / "outside.dylib").symlink_to(outside / "too-new.dylib")
    result = compatibility.check_bundle_compatibility(app)
    assert result["ok"]
    assert result["macho_files"] == 2
    assert result["max_minos"] == "11.0"


@pytest.mark.parametrize("executable", ["../outside", "/tmp/outside", ".", "..", "a/b", r"a\b"])
def test_rejects_main_executable_path_traversal(tmp_path, executable):
    app = bundle(tmp_path)
    path = app / "Contents/Info.plist"
    information = plistlib.loads(path.read_bytes())
    information["CFBundleExecutable"] = executable
    path.write_bytes(plistlib.dumps(information))
    with pytest.raises(compatibility.BundleCompatibilityError, match="plain filename"):
        compatibility.check_bundle_compatibility(app)


def test_rejects_main_executable_symlink_outside_bundle(tmp_path):
    app = bundle(tmp_path)
    executable = app / "Contents/MacOS/example"
    executable.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(thin())
    executable.symlink_to(outside)
    with pytest.raises(compatibility.BundleCompatibilityError, match="outside the app"):
        compatibility.check_bundle_compatibility(app)


def test_main_executable_symlink_inside_bundle_is_checked_once(tmp_path):
    app = bundle(tmp_path)
    put(app, "Contents/Resources/actual-main", thin())
    executable = app / "Contents/MacOS/example"
    executable.unlink()
    executable.symlink_to("../Resources/actual-main")
    result = compatibility.check_bundle_compatibility(app)
    assert result["ok"]
    assert result["macho_files"] == 1


@pytest.mark.parametrize("data,message", [
    (b"unknown executable", "recognized Mach-O"),
    (thin(no_version=True), "Missing macOS minimum-version"),
    (thin(platform=2), "non-macOS"),
    (thin()[:20], "Truncated"),
    (struct.pack(">II", 0xCAFEBABE, 2), "FAT architecture table"),
])
def test_unknown_or_missing_deployment_metadata_fails_clearly(tmp_path, data, message):
    app = bundle(tmp_path, main=data)
    with pytest.raises(compatibility.BundleCompatibilityError, match=message):
        compatibility.check_bundle_compatibility(app)


@pytest.mark.parametrize("data", [
    thin()[:20], thin(no_version=True), b"unknown native-library format",
])
def test_malformed_nested_binary_is_a_reported_failure(tmp_path, data):
    app = bundle(tmp_path)
    put(app, "Contents/Resources/broken.dylib", data)
    result = compatibility.check_bundle_compatibility(app)
    assert not result["ok"]
    assert "Contents/Resources/broken.dylib" in result["errors"][0]


@pytest.mark.parametrize("mode,message", [
    ("cpu", "does not match"), ("overlap", "Overlapping FAT"),
])
def test_rejects_malformed_fat_slice_metadata(tmp_path, mode, message):
    binary = bytearray(fat((ARM64, thin()), (X86_64, thin(cpu=X86_64))))
    if mode == "cpu":
        struct.pack_into(">I", binary, 8, X86_64)
    else:
        first_offset = struct.unpack_from(">I", binary, 16)[0]
        struct.pack_into(">I", binary, 36, first_offset)
    app = bundle(tmp_path, main=binary)
    with pytest.raises(compatibility.BundleCompatibilityError, match=message):
        compatibility.check_bundle_compatibility(app)


@pytest.mark.parametrize("minimum", ["", "14.0beta", "14.0.0.1", "0.0"])
def test_rejects_unknown_bundle_minimum_version(tmp_path, minimum):
    app = bundle(tmp_path, minimum=minimum)
    with pytest.raises(compatibility.BundleCompatibilityError, match="LSMinimumSystemVersion"):
        compatibility.check_bundle_compatibility(app)


def test_checks_every_fat_slice_target(tmp_path):
    app = bundle(tmp_path)
    put(app, "Contents/Resources/universal.dylib", fat(
        (ARM64, thin()), (X86_64, thin((26, 0, 0), cpu=X86_64)),
    ))
    result = compatibility.check_bundle_compatibility(app)
    assert not result["ok"]
    assert "x86_64 requires macOS 26.0" in result["errors"][0]


def test_cli_emits_json_and_nonzero_for_incompatible_bundle(tmp_path):
    app = bundle(tmp_path)
    arguments = [sys.executable, str(TOOL), "--app", str(app)]
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout)["ok"] is True
    put(app, "Contents/Resources/new.dylib", thin((26, 0, 0)))
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["max_minos"] == "26.0"
    assert "Contents/Resources/new.dylib" in report["errors"][0]
