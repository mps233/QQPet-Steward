#!/usr/bin/env python3
"""Check a macOS bundle's deployment targets without executing bundled code."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import struct
import sys
from typing import BinaryIO


THIN_MAGICS = {
    b"\xfe\xed\xfa\xce": (">", False),
    b"\xce\xfa\xed\xfe": ("<", False),
    b"\xfe\xed\xfa\xcf": (">", True),
    b"\xcf\xfa\xed\xfe": ("<", True),
}
FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": (">", False),
    b"\xbe\xba\xfe\xca": ("<", False),
    b"\xca\xfe\xba\xbf": (">", True),
    b"\xbf\xba\xfe\xca": ("<", True),
}
LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_MACOSX = 0x24
PLATFORM_MACOS = 1
Version = tuple[int, int, int]


class BundleCompatibilityError(ValueError):
    """A bundle or Mach-O file cannot be checked reliably."""


@dataclass(frozen=True)
class MachOSlice:
    architecture: str
    minimum_version: Version


def _version_string(version: Version) -> str:
    major, minor, patch = version
    return f"{major}.{minor}" + (f".{patch}" if patch else "")


def _declared_version(value: object) -> Version:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+){0,2}", value):
        raise BundleCompatibilityError("Invalid or missing LSMinimumSystemVersion")
    components = [int(part) for part in value.split(".")]
    components += [0] * (3 - len(components))
    major, minor, patch = components
    if not 0 < major <= 65535 or not 0 <= minor <= 255 or not 0 <= patch <= 255:
        raise BundleCompatibilityError("Invalid LSMinimumSystemVersion")
    return major, minor, patch


def _architecture(cpu: int, subtype: int) -> str:
    subtype &= 0x00FFFFFF
    if cpu == 0x0100000C and subtype in (0, 1, 2):
        return "arm64e" if subtype == 2 else "arm64"
    if cpu == 0x01000007 and subtype in (3, 8):
        return "x86_64h" if subtype == 8 else "x86_64"
    if cpu == 7:
        return "i386"
    raise BundleCompatibilityError(
        f"Unsupported Mach-O architecture: CPU 0x{cpu:08x}, subtype 0x{subtype:06x}"
    )


def _read_at(handle: BinaryIO, offset: int, size: int, end: int) -> bytes:
    if offset < 0 or size < 0 or offset + size > end:
        raise BundleCompatibilityError("Truncated Mach-O header or load commands")
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise BundleCompatibilityError("Truncated Mach-O file")
    return data


def _parse_thin(
    handle: BinaryIO, offset: int, size: int
) -> tuple[int, int, MachOSlice]:
    end = offset + size
    magic = _read_at(handle, offset, 4, end)
    if magic not in THIN_MAGICS:
        raise BundleCompatibilityError("FAT slice is not a thin Mach-O file")
    endian, is_64 = THIN_MAGICS[magic]
    header_size = 32 if is_64 else 28
    header = _read_at(handle, offset, header_size, end)
    cpu, subtype, _filetype, command_count, commands_size = struct.unpack_from(
        endian + "IIIII", header, 4
    )
    architecture = _architecture(cpu, subtype)
    commands_end = offset + header_size + commands_size
    if commands_end > end or command_count * 8 > commands_size:
        raise BundleCompatibilityError("Invalid Mach-O load-command bounds")
    position = offset + header_size
    versions = []
    for _ in range(command_count):
        command, command_size = struct.unpack(
            endian + "II", _read_at(handle, position, 8, commands_end)
        )
        if command_size < 8 or command_size % 4 or position + command_size > commands_end:
            raise BundleCompatibilityError("Invalid Mach-O load-command size")
        if command == LC_BUILD_VERSION:
            if command_size < 24:
                raise BundleCompatibilityError("Truncated LC_BUILD_VERSION")
            platform, encoded, _sdk, tools = struct.unpack(
                endian + "IIII", _read_at(handle, position + 8, 16, commands_end)
            )
            if command_size < 24 + tools * 8:
                raise BundleCompatibilityError("Truncated LC_BUILD_VERSION tools")
            if platform != PLATFORM_MACOS:
                raise BundleCompatibilityError(
                    f"Unsupported non-macOS build platform {platform}"
                )
            versions.append(encoded)
        elif command == LC_VERSION_MIN_MACOSX:
            if command_size < 16:
                raise BundleCompatibilityError("Truncated LC_VERSION_MIN_MACOSX")
            encoded, _sdk = struct.unpack(
                endian + "II", _read_at(handle, position + 8, 8, commands_end)
            )
            versions.append(encoded)
        position += command_size
    if position != commands_end:
        raise BundleCompatibilityError("Mach-O load commands do not match sizeofcmds")
    if not versions or any(version == 0 for version in versions):
        raise BundleCompatibilityError("Missing macOS minimum-version load command")
    if len(set(versions)) != 1:
        raise BundleCompatibilityError("Conflicting macOS minimum-version load commands")
    encoded = versions[0]
    version = encoded >> 16, (encoded >> 8) & 255, encoded & 255
    return cpu, subtype, MachOSlice(architecture, version)


def read_macho(path: Path) -> tuple[MachOSlice, ...] | None:
    """Return every slice, or None for an ordinary non-Mach-O resource."""
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        magic = handle.read(4)
        if magic in THIN_MAGICS:
            return (_parse_thin(handle, 0, size)[2],)
        if magic not in FAT_MAGICS:
            return None
        endian, is_64 = FAT_MAGICS[magic]
        count = struct.unpack(endian + "I", _read_at(handle, 4, 4, size))[0]
        entry_size = 32 if is_64 else 20
        table_end = 8 + count * entry_size
        if count == 0 or table_end > size:
            raise BundleCompatibilityError("Invalid FAT architecture table")
        slices = []
        ranges = []
        architectures = set()
        for index in range(count):
            entry = _read_at(handle, 8 + index * entry_size, entry_size, size)
            if is_64:
                cpu, subtype, offset, slice_size, align, _reserved = struct.unpack(
                    endian + "IIQQII", entry
                )
            else:
                cpu, subtype, offset, slice_size, align = struct.unpack(
                    endian + "IIIII", entry
                )
            if (
                offset < table_end or slice_size == 0 or offset + slice_size > size
                or align > 63 or offset % (1 << align)
            ):
                raise BundleCompatibilityError("Invalid FAT slice bounds or alignment")
            if any(offset < end and start < offset + slice_size for start, end in ranges):
                raise BundleCompatibilityError("Overlapping FAT slices")
            ranges.append((offset, offset + slice_size))
            inner_cpu, inner_subtype, parsed = _parse_thin(handle, offset, slice_size)
            if (cpu, subtype) != (inner_cpu, inner_subtype):
                raise BundleCompatibilityError("FAT architecture does not match its slice")
            if parsed.architecture in architectures:
                raise BundleCompatibilityError("Duplicate FAT architecture")
            architectures.add(parsed.architecture)
            slices.append(parsed)
        return tuple(slices)


def _covers(available: set[str], architecture: str) -> bool:
    if architecture in available:
        return True
    return (
        architecture == "arm64e" and "arm64" in available
        or architecture == "x86_64h" and "x86_64" in available
    )


def check_bundle_compatibility(app: Path | str) -> dict[str, object]:
    root = Path(app).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise BundleCompatibilityError(f"App is not a directory: {root}")
    info_path = root / "Contents/Info.plist"
    if not info_path.resolve(strict=True).is_relative_to(root):
        raise BundleCompatibilityError("Info.plist points outside the app")
    with info_path.open("rb") as handle:
        information = plistlib.load(handle)
    if not isinstance(information, dict):
        raise BundleCompatibilityError("Info.plist is not a dictionary")
    declared = _declared_version(information.get("LSMinimumSystemVersion"))
    executable = information.get("CFBundleExecutable")
    if (
        not isinstance(executable, str) or executable in ("", ".", "..")
        or "/" in executable or "\\" in executable or "\0" in executable
    ):
        raise BundleCompatibilityError("CFBundleExecutable must be a plain filename")
    main_path = (root / "Contents/MacOS" / executable).resolve(strict=True)
    if not main_path.is_relative_to(root) or not main_path.is_file():
        raise BundleCompatibilityError("Main executable points outside the app or is not a file")
    try:
        main_slices = read_macho(main_path)
    except (OSError, BundleCompatibilityError) as error:
        raise BundleCompatibilityError(f"Main executable: {error}") from error
    if not main_slices:
        raise BundleCompatibilityError("Main executable is not a recognized Mach-O file")
    main_architectures = {item.architecture for item in main_slices}
    records: list[tuple[Path, tuple[MachOSlice, ...]]] = []
    errors = []

    def walk_error(error: OSError) -> None:
        raise error

    for folder, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        directories.sort()
        for name in sorted(files):
            path = Path(folder) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                continue
            relative = path.relative_to(root)
            try:
                parsed = main_slices if path == main_path else read_macho(path)
            except (OSError, BundleCompatibilityError) as error:
                errors.append(f"{relative}: {error}")
                continue
            if parsed is None:
                if path.suffix.lower() in (".dylib", ".so"):
                    errors.append(f"{relative}: native library is not a recognized Mach-O file")
                continue
            records.append((relative, parsed))
            available = {item.architecture for item in parsed}
            for architecture in sorted(main_architectures):
                if not _covers(available, architecture):
                    errors.append(
                        f"{relative}: missing architecture {architecture} required by "
                        f"the main executable (available: {', '.join(sorted(available))})"
                    )
            for item in parsed:
                if item.minimum_version > declared:
                    errors.append(
                        f"{relative}: {item.architecture} requires macOS "
                        f"{_version_string(item.minimum_version)}; bundle declares "
                        f"{_version_string(declared)}"
                    )
    versions = [item.minimum_version for _path, items in records for item in items]
    if not versions:
        raise BundleCompatibilityError("No physical Mach-O files found in the app")
    return {
        "ok": not errors,
        "macho_files": len(records),
        "slices": sum(len(items) for _path, items in records),
        "architectures": sorted(main_architectures),
        "minimum_system_version": _version_string(declared),
        "max_minos": _version_string(max(versions)),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = check_bundle_compatibility(arguments.app)
    except (OSError, ValueError, struct.error) as error:
        result = {"ok": False, "errors": [str(error)]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
