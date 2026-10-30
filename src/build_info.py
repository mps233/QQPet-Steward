"""Build metadata of the Python runtime that serves the dashboard."""

from __future__ import annotations

import string
import sys
from pathlib import Path

FINGERPRINT_FILE = ".build-fingerprint"


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
