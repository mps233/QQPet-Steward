#!/usr/bin/env python3
"""Restore PyInstaller links that the Tauri resource copy expanded."""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


class BundleSymlinkError(ValueError):
    """A runtime tree cannot safely be restored."""


@dataclass(frozen=True)
class _Change:
    path: Path
    target: str
    resolved_target: Path
    directory: bool
    copied_bytes: int
    missing: bool = False


def _runtime_root(path: Path) -> Path:
    if path.is_symlink():
        raise BundleSymlinkError(f"Runtime root must not be a symlink: {path}")
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise BundleSymlinkError(f"Runtime root is not a directory: {root}")
    for name, directory in (("QQPetRuntime", False), ("_internal", True)):
        marker = root / name
        if marker.is_symlink() or not (
            marker.is_dir() if directory else marker.is_file()
        ):
            raise BundleSymlinkError(f"Missing runtime marker: {marker}")
    return root


def _resolve_inside(
    root: Path, path: Path, missing_directories: dict[Path, str] | None = None
) -> Path:
    """Resolve every link without allowing an absolute target or a root escape."""
    pending = deque(path.relative_to(root).parts)
    current = root
    followed = 0
    while pending:
        part = pending.popleft()
        if part == ".":
            continue
        if part == "..":
            if current == root:
                raise BundleSymlinkError(f"Link escapes runtime: {path}")
            current = current.parent
            continue
        candidate = current / part
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            if (
                missing_directories is None
                or candidate.relative_to(root) not in missing_directories
            ):
                raise
            metadata = None
        if metadata is None or stat.S_ISLNK(metadata.st_mode):
            target = (
                Path(missing_directories[candidate.relative_to(root)])
                if metadata is None else candidate.readlink()
            )
            if target.is_absolute():
                raise BundleSymlinkError(f"Absolute link is not allowed: {candidate}")
            followed += 1
            if followed > 128:
                raise BundleSymlinkError(f"Cyclic or excessive links: {path}")
            pending.extendleft(reversed(target.parts))
        else:
            current = candidate
            if pending and not stat.S_ISDIR(metadata.st_mode):
                raise BundleSymlinkError(f"Link traverses a non-directory: {candidate}")
    return current


def _plain_parents(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        if current.is_symlink() or not current.is_dir():
            raise BundleSymlinkError(f"Link parent must be a real directory: {current}")


def _same_contents(
    root: Path,
    left: Path,
    right: Path,
    active: frozenset[Path] = frozenset(),
) -> None:
    left = _resolve_inside(root, left)
    right = _resolve_inside(root, right)
    if left == right:
        return
    if left.is_file() and right.is_file():
        if left.stat().st_size == right.stat().st_size:
            with left.open("rb") as first, right.open("rb") as second:
                while True:
                    block = first.read(1024 * 1024)
                    if block != second.read(1024 * 1024):
                        break
                    if not block:
                        return
        raise BundleSymlinkError(f"Expanded file differs from its target: {left}")
    if left.is_dir() and right.is_dir():
        if left in active or right in active:
            raise BundleSymlinkError(f"Directory link creates a cycle: {left}")
        names = {child.name for child in left.iterdir()}
        if names != {child.name for child in right.iterdir()}:
            raise BundleSymlinkError(f"Expanded directory differs from its target: {left}")
        for name in sorted(names):
            _same_contents(root, left / name, right / name, active | {left, right})
        return
    raise BundleSymlinkError(f"Expanded entry has a different type from its target: {left}")


def _copied_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for parent, _directories, files in os.walk(path, followlinks=False):
        for name in files:
            metadata = (Path(parent) / name).lstat()
            if stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
    return total


def _replace_with_link(change: _Change) -> None:
    if change.missing:
        # Tauri omits directory links rather than expanding them.
        change.path.symlink_to(change.target, target_is_directory=True)
        return
    staging = Path(tempfile.mkdtemp(prefix=".restore-links-", dir=change.path.parent))
    replacement = staging / "link"
    original = staging / "original"
    try:
        replacement.symlink_to(change.target, target_is_directory=change.directory)
        # Nonempty directories cannot be atomically overwritten by a symlink.
        if change.directory:
            change.path.rename(original)
        try:
            replacement.replace(change.path)
        except BaseException:
            if original.exists():
                try:
                    original.rename(change.path)
                except OSError as error:
                    raise BundleSymlinkError(
                        f"Could not roll back directory; original retained at {original}"
                    ) from error
            raise
        if original.exists():
            shutil.rmtree(original)
    finally:
        # Preserve the only original copy if either replacement or rollback failed.
        if not original.exists():
            shutil.rmtree(staging)


def restore_runtime_symlinks(
    source: Path | str, destination: Path | str, *, check: bool = False
) -> dict[str, int | bool]:
    source = _runtime_root(Path(source).expanduser())
    destination = _runtime_root(Path(destination).expanduser())
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise BundleSymlinkError("Source and destination runtime roots must not overlap")

    source_links: list[tuple[Path, Path, str]] = []
    for parent, directories, files in os.walk(source, followlinks=False):
        for name in sorted(directories + files):
            link = Path(parent) / name
            if not link.is_symlink():
                continue
            source_target = _resolve_inside(source, link)
            if not source_target.is_file() and not source_target.is_dir():
                raise BundleSymlinkError(f"Unsupported link target: {link}")
            target = str(link.readlink())
            source_links.append((link, source_target, target))
    missing_directories = {
        link.relative_to(source): target
        for link, source_target, target in source_links if source_target.is_dir()
    }
    changes: list[_Change] = []
    for link, source_target, target in source_links:
        path = destination / link.relative_to(source)
        _plain_parents(destination, path)
        destination_target = _resolve_inside(
            destination, path.parent / target, missing_directories
        )
        directory = source_target.is_dir()
        if destination_target.is_dir() != directory or (
            not directory and not destination_target.is_file()
        ):
            raise BundleSymlinkError(f"Destination target has the wrong type: {path}")
        if path.is_symlink():
            _resolve_inside(destination, path, missing_directories)
            if str(path.readlink()) != target:
                raise BundleSymlinkError(f"Destination link points elsewhere: {path}")
            continue
        try:
            path.lstat()
        except FileNotFoundError:
            if not directory:
                raise
            changes.append(_Change(path, target, destination_target, directory, 0, True))
        else:
            _same_contents(destination, path, destination_target)
            changes.append(
                _Change(path, target, destination_target, directory, _copied_bytes(path))
            )

    if check and changes:
        raise BundleSymlinkError(
            f"{len(changes)} runtime symlinks are expanded or missing in {destination}"
        )
    for change in changes:
        _plain_parents(destination, change.path)
        if change.path.is_symlink():
            raise BundleSymlinkError(f"Destination changed after validation: {change.path}")
        if change.missing:
            if change.path.exists():
                raise BundleSymlinkError(f"Destination changed after validation: {change.path}")
        else:
            _same_contents(destination, change.path, change.resolved_target)
        _replace_with_link(change)
    for link, _source_target, _target in source_links:
        _resolve_inside(destination, destination / link.relative_to(source))
    link_count = len(source_links)
    return {
        "ok": True,
        "links": link_count,
        "restored": len(changes),
        "already_present": link_count - len(changes),
        "saved_bytes": sum(change.copied_bytes for change in changes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Verify without changing files")
    arguments = parser.parse_args(argv)
    try:
        result = restore_runtime_symlinks(
            arguments.source, arguments.destination, check=arguments.check
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
