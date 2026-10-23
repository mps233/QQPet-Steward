"""Frozen Windows scheduler entry point.

``scenarios/runner.py`` contains its CLI under ``if __name__ == "__main__"``.
PyInstaller does set that name for a script entry, but the frozen scheduler also
needs a writable data root before ``src.config`` binds its module paths.  This
launcher binds that root, then executes the runner in a fresh main namespace.

Use ``runpy`` with ``alter_sys=True`` rather than importing and calling a
function.  That preserves the original no-argument CLI, ``sys.argv`` parsing,
top-level startup errors, and ``SystemExit`` semantics.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Scenarios and the dashboard both write under the data root.  Set it before any
# src.* import so frozen builds never inherit PyInstaller's read-only bundle.
data_root = Path(os.environ.get("QQPET_DATA_DIR", str(Path(sys.executable).resolve().parent)))
data_root.mkdir(parents=True, exist_ok=True)
(data_root / "runs").mkdir(parents=True, exist_ok=True)
os.environ["QQPET_DATA_DIR"] = str(data_root)


def main(argv: list[str] | None = None) -> int:
    """Run the real scheduler CLI with its original ``__main__`` semantics.

    ``argv`` exists for offline tests; the packaged executable calls ``main()``
    with no arguments and preserves the process's original ``sys.argv``.
    """
    # Import config only after QQPET_DATA_DIR is set, then redirect roots that
    # runner and scenario imports resolve from. RESOURCE_ROOT stays on
    # PyInstaller's _MEIPASS for bundled models and binaries.
    # Resolve through sys.modules so this also works when the module was
    # imported before a packaging/test harness replaced it.
    import src.config  # noqa: F401
    runtime_config = sys.modules["src.config"]

    runtime_config.APP_ROOT = data_root
    runtime_config.PROJECT_ROOT = data_root
    runtime_config.CONFIG_FILE = data_root / "config.yaml"

    original_argv = sys.argv
    sys.argv = [original_argv[0], *(argv if argv is not None else original_argv[1:])]
    try:
        # runner.py lives in PyInstaller's PYZ, so run_module is the stable
        # no-arg entry that still executes its if __name__ == "__main__" block.
        runpy.run_module("scenarios.runner", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
