"""Cross-platform helpers for launching and stopping the hosted scheduler.

The dashboard runs on macOS and Windows, while tests for this layer must run on
either platform.  Keep OS-specific choices in small, injectable functions:

* source checkouts use ``.venv/bin/python`` or ``.venv/Scripts/python.exe``;
* frozen macOS builds reuse the bundled runtime's scheduler subcommand;
* frozen Windows builds use a sibling scheduler executable;
* POSIX stops use SIGINT -> SIGTERM -> SIGKILL;
* Windows stops use CTRL_BREAK_EVENT -> taskkill /T -> taskkill /T /F.

This module is intentionally independent from ``dashboard.py`` so the HTTP
entry point can integrate it without creating an import cycle.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

APP_NAME = "QQPetCopilot"
DATA_DIR_ENV = "QQPET_DATA_DIR"
PORTABLE_ENV = "QQPET_PORTABLE"
SCHEDULER_EXE_NAME = "QQPetCopilotScheduler.exe"
SCHEDULER_SCRIPT = Path("scenarios") / "runner.py"
RUNNER_LOG_NAME = "runner_console.log"

# These constants exist on Windows builds of subprocess.  Keep local fallbacks
# so argument construction can be tested on macOS without importing winreg or
# touching a real process.
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CTRL_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", 1)


@dataclass(frozen=True)
class SpawnedProcess:
    """A detached process accepted by the OS, plus its diagnostic log."""

    pid: int
    argv: tuple[str, ...]
    log_path: Path


@dataclass(frozen=True)
class StopResult:
    """Outcome of a graceful-to-forceful stop ladder."""

    stopped: bool
    forced: bool
    attempts: tuple[str, ...]


@dataclass(frozen=True)
class UserDataPaths:
    """Writable paths owned by the user, never by the read-only app bundle."""

    root: Path
    config: Path
    runs: Path
    logs: Path
    runner_console_log: Path
    runner_pid: Path

    def ensure(self) -> "UserDataPaths":
        """Create the writable directories with a private POSIX mode."""
        for directory in (self.root, self.runs, self.logs):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            try:
                self.root.chmod(0o700)
            except OSError:
                pass
        return self

    def environment(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return an environment that pins child processes to these paths."""
        env = dict(os.environ if base is None else base)
        env[DATA_DIR_ENV] = str(self.root)
        return env


def _is_windows(platform: str | None = None) -> bool:
    value = (platform or sys.platform).lower()
    return os.name == "nt" if platform is None else value in ("win32", "windows", "nt")


def _is_frozen(frozen: bool | None = None) -> bool:
    return bool(getattr(sys, "frozen", False)) if frozen is None else frozen


def find_project_python(
    project_root: str | Path,
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    executable: str | Path | None = None,
    exists: Callable[[Path], bool] | None = None,
) -> Path:
    """Locate the interpreter used to run the source scheduler.

    Frozen builds return ``sys.executable`` as requested by the hosted-app
    contract.  They should launch the sibling scheduler executable through
    :func:`build_scheduler_argv`; they must not append ``runner.py`` to the
    dashboard executable.
    """
    root = Path(project_root)
    exe = Path(executable or sys.executable)
    if _is_frozen(frozen):
        return exe

    relative = Path("Scripts") / "python.exe" if _is_windows(platform) else Path("bin") / "python"
    candidate = root / ".venv" / relative
    checker = exists or Path.is_file
    return candidate if checker(candidate) else exe


def build_scheduler_argv(
    project_root: str | Path,
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    executable: str | Path | None = None,
    scheduler_executable: str | Path | None = None,
    runner_script: str | Path = SCHEDULER_SCRIPT,
    extra_args: Sequence[str] = (),
    exists: Callable[[Path], bool] | None = None,
) -> list[str]:
    """Build the scheduler command without starting a process.

    Source mode: ``[project-python, scenarios/runner.py, ...]``.
    Frozen macOS: ``[bundled-runtime, scheduler, ...]``.
    Frozen Windows: ``[sibling QQPetCopilotScheduler.exe, ...]``.
    """
    extra = [str(arg) for arg in extra_args]
    if _is_frozen(frozen):
        exe = Path(executable or sys.executable)
        if (platform or sys.platform).lower() == "darwin":
            return [str(exe), "scheduler", *extra]
        scheduler = (Path(scheduler_executable) if scheduler_executable is not None
                     else exe.with_name(SCHEDULER_EXE_NAME))
        checker = exists or Path.is_file
        if not checker(scheduler):
            raise FileNotFoundError(
                f"frozen dashboard is missing its scheduler executable: {scheduler}"
            )
        return [str(scheduler), *extra]

    root = Path(project_root)
    python = find_project_python(
        root,
        frozen=False,
        platform=platform,
        executable=executable,
        exists=exists,
    )
    runner = Path(runner_script)
    if not runner.is_absolute():
        runner = root / runner
    return [str(python), str(runner), *extra]


def build_career_sync_argv(
    project_root: str | Path,
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    executable: str | Path | None = None,
) -> list[str]:
    """Select a bundled subcommand or the source career-sync script."""
    exe = Path(executable or sys.executable)
    if _is_frozen(frozen) and (platform or sys.platform).lower() == "darwin":
        return [str(exe), "career-sync"]
    root = Path(project_root)
    script = root / "tools" / "career_sync.py"
    if not script.is_file():
        raise FileNotFoundError("未找到 tools/career_sync.py")
    return [str(exe), str(script)]


def scheduler_environment(
    paths: UserDataPaths,
    *,
    base: Mapping[str, str] | None = None,
    portable: bool = False,
) -> dict[str, str]:
    """Environment required by the frozen scheduler's no-argument entry."""
    env = paths.environment(base)
    if portable:
        env[PORTABLE_ENV] = "1"
    return env


def spawn_detached_scheduler(
    command: Sequence[str | Path],
    *,
    cwd: str | Path,
    log_path: str | Path,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    popen: Callable[..., object] | None = None,
    open_log: Callable[..., object] | None = None,
    now: Callable[[], datetime] | None = None,
) -> SpawnedProcess:
    """Start the scheduler detached and append stdout/stderr to one log.

    ``popen``, ``open_log`` and ``now`` are injectable so tests can verify the
    Windows and POSIX argument construction without launching the scheduler.
    """
    argv = tuple(str(item) for item in command)
    if not argv:
        raise ValueError("scheduler command must not be empty")
    cwd_path = Path(cwd)
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    opener = open_log or open
    pid_handle = opener(log_file, "ab")
    try:
        stamp = (now or datetime.now)().strftime("%Y-%m-%d %H:%M:%S")
        pid_handle.write(
            f"\n===== {stamp} 由仪表盘启动调度器 =====\n".encode("utf-8")
        )
        pid_handle.flush()
        kwargs: dict[str, object] = {
            "cwd": str(cwd_path),
            "stdin": subprocess.DEVNULL,
            "stdout": pid_handle,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if env is not None:
            kwargs["env"] = dict(env)
        if _is_windows(platform):
            kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        process = (popen or subprocess.Popen)(list(argv), **kwargs)
    finally:
        pid_handle.close()
    return SpawnedProcess(pid=int(process.pid), argv=argv, log_path=log_file)


def pid_alive(
    pid: int,
    *,
    platform: str | None = None,
    probe: Callable[[int], bool] | None = None,
    winapi: object | None = None,
) -> bool:
    """Return whether a pid still exists, without invoking ``ps`` or ``pgrep``."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if probe is not None:
        return bool(probe(pid))
    if _is_windows(platform):
        try:
            kernel32 = winapi or ctypes.windll.kernel32
            process_query_limited_information = 0x1000
            still_active = 259
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_until_gone(
    pid: int,
    seconds: float,
    *,
    alive: Callable[[int], bool],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    poll_interval: float,
) -> bool:
    if not alive(pid):
        return True
    deadline = clock() + max(0.0, seconds)
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return not alive(pid)
        sleep(min(poll_interval, remaining))
        if not alive(pid):
            return True


def stop_process(
    pid: int,
    *,
    platform: str | None = None,
    alive: Callable[[int], bool] | None = None,
    send_signal: Callable[[int, int], None] | None = None,
    run_command: Callable[..., object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    interrupt_wait: float = 12.0,
    terminate_wait: float = 4.0,
    kill_wait: float = 1.0,
    poll_interval: float = 0.5,
) -> StopResult:
    """Stop one scheduler, escalating only when the earlier stage failed.

    POSIX preserves the dashboard's long-standing SIGINT > SIGTERM > SIGKILL
    sequence.  Windows first sends CTRL_BREAK_EVENT to the process group, then
    falls back to ``taskkill /PID <pid> /T`` and finally ``/F``.
    """
    alive_fn = alive or pid_alive
    signal_fn = send_signal or os.kill
    run_fn = run_command or subprocess.run
    attempts: list[str] = []
    forced = False

    if not alive_fn(pid):
        return StopResult(True, False, ())

    if _is_windows(platform):
        break_sent = False
        try:
            # The dashboard is created with CREATE_NEW_PROCESS_GROUP.  Deliver
            # the event to that group, not just the scheduler PID, so any
            # runner-owned children receive the graceful shutdown too.
            signal_fn(-pid, CTRL_BREAK_EVENT)
            break_sent = True
        except (OSError, ValueError):
            pass
        attempts.append("ctrl_break")
        if break_sent and _wait_until_gone(
            pid, interrupt_wait, alive=alive_fn, sleep=sleep, clock=clock,
            poll_interval=poll_interval,
        ):
            return StopResult(True, False, tuple(attempts))

        run_fn(["taskkill", "/PID", str(pid), "/T"],
               capture_output=True, text=True, check=False,
               creationflags=CREATE_NO_WINDOW)
        attempts.append("taskkill_tree")
        if _wait_until_gone(
            pid, terminate_wait, alive=alive_fn, sleep=sleep, clock=clock,
            poll_interval=poll_interval,
        ):
            return StopResult(True, False, tuple(attempts))

        forced = True
        run_fn(["taskkill", "/PID", str(pid), "/T", "/F"],
               capture_output=True, text=True, check=False,
               creationflags=CREATE_NO_WINDOW)
        attempts.append("taskkill_tree_force")
        stopped = _wait_until_gone(
            pid, kill_wait, alive=alive_fn, sleep=sleep, clock=clock,
            poll_interval=poll_interval,
        )
        return StopResult(stopped, forced, tuple(attempts))

    try:
        signal_fn(pid, signal.SIGINT)
    except OSError:
        pass
    attempts.append("sigint")
    if _wait_until_gone(
        pid, interrupt_wait, alive=alive_fn, sleep=sleep, clock=clock,
        poll_interval=poll_interval,
    ):
        return StopResult(True, False, tuple(attempts))

    try:
        signal_fn(pid, signal.SIGTERM)
    except OSError:
        pass
    attempts.append("sigterm")
    if _wait_until_gone(
        pid, terminate_wait, alive=alive_fn, sleep=sleep, clock=clock,
        poll_interval=poll_interval,
    ):
        return StopResult(True, False, tuple(attempts))

    forced = True
    try:
        signal_fn(pid, signal.SIGKILL)
    except OSError:
        pass
    attempts.append("sigkill")
    stopped = _wait_until_gone(
        pid, kill_wait, alive=alive_fn, sleep=sleep, clock=clock,
        poll_interval=poll_interval,
    )
    return StopResult(stopped, forced, tuple(attempts))


def user_data_paths(
    app_root: str | Path,
    *,
    portable: bool = False,
    frozen: bool | None = None,
    platform: str | None = None,
    app_name: str = APP_NAME,
    env: Mapping[str, str] | None = None,
    home: str | Path | None = None,
) -> UserDataPaths:
    """Resolve writable state for source, portable, and installed builds.

    Resolution order:

    1. ``QQPET_DATA_DIR`` override;
    2. portable/source mode -> project or exe directory;
    3. installed/frozen mode -> per-user application-data directory.

    The packaging scaffold uses portable mode for milestone one.  The installed
    mode is already isolated for the later MSI/autostart milestone.
    """
    environment = dict(os.environ if env is None else env)
    override = str(environment.get(DATA_DIR_ENV) or "").strip()
    portable_requested = portable or str(environment.get(PORTABLE_ENV) or "").lower() in {
        "1", "true", "yes", "on",
    }
    root = Path(app_root).expanduser()
    if override:
        root = Path(override).expanduser()
    elif portable_requested or not _is_frozen(frozen):
        root = root
    elif _is_windows(platform):
        base = Path(environment.get("LOCALAPPDATA") or
                    (Path(home).expanduser() if home else Path.home()) /
                    "AppData" / "Local")
        root = base / app_name
    elif (platform or sys.platform).lower() == "darwin":
        base = Path(home).expanduser() if home else Path.home()
        root = base / "Library" / "Application Support" / app_name
    else:
        base = Path(environment.get("XDG_DATA_HOME") or
                    (Path(home).expanduser() if home else Path.home()) /
                    ".local" / "share")
        root = base / "qqpet-copilot"

    runs = root / "runs"
    return UserDataPaths(
        root=root,
        config=root / "config.yaml",
        runs=runs,
        logs=runs / "logs",
        runner_console_log=runs / RUNNER_LOG_NAME,
        runner_pid=runs / "runner.pid",
    )
