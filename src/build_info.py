"""Build metadata of the Python runtime that serves the dashboard."""

from __future__ import annotations

import json
import re
import string
import sys
from pathlib import Path

FINGERPRINT_FILE = ".build-fingerprint"
VERSION_FILE = ".build-version"
VERSION_RE = re.compile(r"[0-9][0-9A-Za-z.+-]*")


def build_fingerprint() -> str:
    """Return the bundled runtime fingerprint, or an empty string in source mode.

    The native shell writes ``.build-fingerprint`` next to the frozen
    executable (see desktop/macos/scripts/build-runtime.sh).  The dashboard
    exposes this value through ``/api/client/meta`` so the shell can tell
    whether the launchd job still serves an older backend after an app update.
    """

    try:
        executable = Path(sys.executable).resolve()
    except OSError:
        return ""
    try:
        value = (executable.parent / FINGERPRINT_FILE).read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""
    if len(value) == 64 and all(character in string.hexdigits for character in value):
        return value.lower()
    return ""


def app_version() -> str:
    """Return the app version this runtime belongs to, or an empty string.

    Frozen builds carry ``.build-version`` next to the executable (written by
    build-runtime.sh / build_portable.py).  Source runs read the version from
    ``desktop/macos/tauri.conf.json`` — the single source of truth used by the
    release scripts.  The dashboard shows it in Settings, which is the only
    place a phone browser can see the version (it has no app shell to ask).
    """

    try:
        executable = Path(sys.executable).resolve()
    except OSError:
        executable = None
    if executable is not None:
        try:
            value = (executable.parent / VERSION_FILE).read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            value = ""
        if VERSION_RE.fullmatch(value):
            return value
    if getattr(sys, "frozen", False):
        return ""
    config = (
        Path(__file__).resolve().parents[1] / "desktop" / "macos" / "tauri.conf.json"
    )
    try:
        value = json.loads(config.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, TypeError):
        return ""
    if isinstance(value, str) and VERSION_RE.fullmatch(value.strip()):
        return value.strip()
    return ""
