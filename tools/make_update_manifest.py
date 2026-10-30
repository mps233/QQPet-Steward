#!/usr/bin/env python3
"""Generate a Tauri updater ``latest.json`` from signed release artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def read_signature(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"signature file is empty: {path}")
    return value


def build_manifest(
    version: str,
    notes: str,
    pub_date: str,
    platforms: list[tuple[str, str, Path]],
) -> dict:
    if not version.strip():
        raise ValueError("version is required")
    if not platforms:
        raise ValueError("at least one platform is required")
    entries: dict[str, dict[str, str]] = {}
    for key, url, signature_path in platforms:
        key = key.strip()
        if not key:
            raise ValueError("platform key is required")
        if key in entries:
            raise ValueError(f"duplicate platform key: {key}")
        if not url.startswith("https://"):
            raise ValueError(f"platform {key} needs an https URL")
        entries[key] = {
            "signature": read_signature(signature_path),
            "url": url,
        }
    return {
        "version": version.strip(),
        "notes": notes,
        "pub_date": pub_date,
        "platforms": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release version, for example 0.8.5")
    parser.add_argument("--notes", default="", help="release notes shown by the updater")
    parser.add_argument(
        "--pub-date",
        default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        help="RFC 3339 publication time (defaults to now)",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--platform",
        action="append",
        nargs=3,
        metavar=("KEY", "URL", "SIG"),
        default=[],
        help="platform entry; repeat for each OS/arch key",
    )
    args = parser.parse_args(argv)
    manifest = build_manifest(
        args.version,
        args.notes,
        args.pub_date,
        [(key, url, Path(sig)) for key, url, sig in args.platform],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
