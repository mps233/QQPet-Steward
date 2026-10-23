"""Frozen Windows dashboard entry point.

``dashboard.py`` resolves ``BASE`` from ``__file__``.  In a PyInstaller onedir
build that file lives in the bundle root, not in ``_MEIPASS`` where ``web/dist``
and ``static`` are unpacked.  Bind ``BASE`` to the unpacked resource root just
for this launcher before importing the module.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A frozen dashboard starts beside the portable config.  Show config.py the
# same root before importing dashboard so first-run migration and settings
# writes cannot accidentally target the read-only bundle.
user_root = Path(os.environ.get("QQPET_DATA_DIR", str(Path(sys.executable).resolve().parent)))
os.environ.setdefault("QQPET_DATA_DIR", str(user_root))


def _snapshot_module_paths(modules):
    """Capture only path constants, so the launcher can restore them later."""
    touched: dict[object, dict[str, Path]] = {}
    for name in list(modules):
        if not name.startswith("src.") and name != "dashboard":
            continue
        module = modules.get(name)
        if module is None:
            continue
        values: dict[str, Path] = {}
        for attr, value in vars(module).items():
            if attr.isupper() and isinstance(value, Path):
                values[attr] = value
        if values:
            touched[module] = values
    return touched

import dashboard  # noqa: E402
import src.progress  # noqa: E402,F401
import src.queue_status  # noqa: E402,F401
import src.status_cache  # noqa: E402,F401
import src.notify  # noqa: E402,F401
import src.notification_outbox  # noqa: E402,F401
import src.ad_bonus  # noqa: E402,F401
import src.coins  # noqa: E402,F401
import src.fatigue  # noqa: E402,F401
import src.hire_quota  # noqa: E402,F401
import src.daily_summary  # noqa: E402,F401
import src.tg_bot  # noqa: E402,F401
import src.career_watch  # noqa: E402,F401
import src.ocr  # noqa: E402,F401
runtime_config = sys.modules["src.config"]


def _rebind_module_paths(module, data_root: Path) -> None:
    """Move path constants that were derived from an app-bundle root."""
    for attr, value in list(vars(module).items()):
        if not attr.isupper() or not isinstance(value, Path):
            continue
        if "runs" in value.parts:
            index = value.parts.index("runs")
            setattr(module, attr, data_root.joinpath(*value.parts[index:]))
        elif attr == "CONFIG_FILE":
            setattr(module, attr, data_root / "config.yaml")


def main() -> int:
    resource_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    # Keep every original path before any mutation. The launcher is a test
    # target and an embeddable entry point; restoring afterwards prevents a
    # later dashboard in the same interpreter from using portable paths.
    original_paths: list[tuple[object, str, Path]] = []
    for name in list(sys.modules):
        if not name.startswith("src.") and name != "dashboard":
            continue
        module = sys.modules.get(name)
        if module is None:
            continue
        for attr, value in list(vars(module).items()):
            if attr.isupper() and isinstance(value, Path):
                original_paths.append((module, attr, value))

    dashboard.BASE = resource_root
    data_root = Path(os.environ["QQPET_DATA_DIR"]).expanduser()
    data_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QQPET_PORTABLE", "1")
    runs = data_root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    # dashboard.py binds module-level path constants at import time.  Redirect
    # every consumer, not just RUNS: audit/logs/token/queue paths all derive from
    # these names and must never point back into the read-only bundle.
    dashboard.RUNS = runs
    dashboard.RUNNER_PID = runs / "runner.pid"
    dashboard.LOGS = runs / "logs"
    dashboard.ADV_LIVE_FILE = runs / "adventure_live.jsonl"
    dashboard.PAUSE_SIGNAL = runs / "pause.signal"
    dashboard.SESSION_REWARD_FILE = runs / "session_rewards.jsonl"
    dashboard.PLAN_FILE = runs / "career_plan.json"
    runtime_config.APP_ROOT = data_root
    runtime_config.PROJECT_ROOT = data_root
    runtime_config.CONFIG_FILE = data_root / "config.yaml"
    for name in list(sys.modules):
        if not name.startswith("src."):
            continue
        module = sys.modules.get(name)
        if module is None:
            continue
        _rebind_module_paths(module, data_root)
    try:
        return int(dashboard.main())
    finally:
        for module, attr, value in original_paths:
            setattr(module, attr, value)


if __name__ == "__main__":
    raise SystemExit(main())
