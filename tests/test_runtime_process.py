from __future__ import annotations

import signal
from datetime import datetime
from pathlib import Path

import pytest

from src import runtime_process as rp


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_find_project_python_uses_posix_venv(tmp_path):
    python = tmp_path / ".venv" / "bin" / "python"
    found = rp.find_project_python(
        tmp_path,
        frozen=False,
        platform="darwin",
        executable="/usr/bin/python3",
        exists=lambda path: path == python,
    )
    assert found == python


def test_find_project_python_uses_windows_venv(tmp_path):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    found = rp.find_project_python(
        tmp_path,
        frozen=False,
        platform="win32",
        executable="C:/Python/python.exe",
        exists=lambda path: path == python,
    )
    assert found == python


def test_find_project_python_does_not_cross_select_platform(tmp_path):
    cmd_shim = tmp_path / ".venv" / "bin" / "python"
    found = rp.find_project_python(
        tmp_path,
        frozen=False,
        platform="win32",
        executable="C:/Python/python.exe",
        exists=lambda path: path == cmd_shim,
    )
    assert found == Path("C:/Python/python.exe")


def test_find_project_python_falls_back_and_frozen_uses_executable(tmp_path):
    fallback = rp.find_project_python(
        tmp_path,
        frozen=False,
        platform="darwin",
        executable="/usr/bin/python3",
        exists=lambda _path: False,
    )
    frozen = rp.find_project_python(
        tmp_path,
        frozen=True,
        executable="/opt/app/QQPetCopilot",
        exists=lambda _path: False,
    )
    assert fallback == Path("/usr/bin/python3")
    assert frozen == Path("/opt/app/QQPetCopilot")


def test_build_scheduler_argv_source_mode(tmp_path):
    python = tmp_path / ".venv" / "bin" / "python"
    argv = rp.build_scheduler_argv(
        tmp_path,
        frozen=False,
        platform="darwin",
        executable="/usr/bin/python3",
        exists=lambda path: path == python,
        extra_args=["--emulator"],
    )
    assert argv == [
        str(python),
        str(tmp_path / "scenarios" / "runner.py"),
        "--emulator",
    ]


def test_build_scheduler_argv_source_uses_windows_venv_and_absolute_runner(tmp_path):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    runner = tmp_path / "scenarios" / "runner.py"
    argv = rp.build_scheduler_argv(
        tmp_path,
        frozen=False,
        platform="win32",
        executable="C:/Python/python.exe",
        scheduler_executable=tmp_path / "ignored.exe",
        exists=lambda path: path in (python, runner),
    )
    assert argv == [str(python), str(runner)]


def test_build_scheduler_argv_frozen_uses_sibling_executable(tmp_path):
    executable = tmp_path / "QQPetCopilot.exe"
    scheduler = tmp_path / rp.SCHEDULER_EXE_NAME
    argv = rp.build_scheduler_argv(
        tmp_path,
        frozen=True,
        platform="win32",
        executable=executable,
        exists=lambda path: path == scheduler,
    )
    assert argv == [str(scheduler)]


def test_build_scheduler_argv_frozen_requires_scheduler(tmp_path):
    with pytest.raises(FileNotFoundError, match="scheduler executable"):
        rp.build_scheduler_argv(
            tmp_path,
            frozen=True,
            platform="win32",
            executable=tmp_path / "QQPetCopilot.exe",
            exists=lambda _path: False,
        )


def test_build_scheduler_argv_frozen_preserves_runner_flags(tmp_path):
    scheduler = tmp_path / rp.SCHEDULER_EXE_NAME
    argv = rp.build_scheduler_argv(
        tmp_path,
        frozen=True,
        platform="win32",
        executable=tmp_path / "QQPetCopilot.exe",
        extra_args=["--emulator", "--emulator-device", "127.0.0.1:7555"],
        exists=lambda path: path == scheduler,
    )
    assert argv == [
        str(scheduler),
        "--emulator",
        "--emulator-device",
        "127.0.0.1:7555",
    ]


def test_scheduler_environment_always_sets_data_root(tmp_path):
    paths = rp.user_data_paths(tmp_path / "app", portable=True, frozen=True,
                              platform="win32", env={})
    env = rp.scheduler_environment(paths, base={"PATH": "C:/Windows"}, portable=True)
    assert env[rp.DATA_DIR_ENV] == str(paths.root)
    assert env[rp.PORTABLE_ENV] == "1"
    assert env["PATH"] == "C:/Windows"


def test_spawn_detached_scheduler_posix_arguments(tmp_path):
    calls = {}

    class Proc:
        pid = 4321

    class Log:
        def __init__(self):
            self.data = b""
            self.closed = False

        def write(self, data):
            self.data += data

        def flush(self):
            pass

        def close(self):
            self.closed = True

    log = Log()

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return Proc()

    result = rp.spawn_detached_scheduler(
        ["/venv/python", "runner.py"],
        cwd=tmp_path,
        log_path=tmp_path / "runs" / "runner_console.log",
        platform="darwin",
        popen=fake_popen,
        open_log=lambda _path, _mode: log,
        now=lambda: datetime(2026, 10, 23, 12, 34, 56),
    )
    assert result.pid == 4321
    assert calls["argv"] == ["/venv/python", "runner.py"]
    assert calls["kwargs"]["start_new_session"] is True
    assert "creationflags" not in calls["kwargs"]
    assert calls["kwargs"]["stderr"] == rp.subprocess.STDOUT
    assert b"2026-10-23 12:34:56" in log.data
    assert log.closed is True


def test_spawn_detached_scheduler_creates_log_parent(tmp_path):
    class Proc:
        pid = 7

    class Log:
        def write(self, _data):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    log_path = tmp_path / "portable" / "runs" / "runner_console.log"
    rp.spawn_detached_scheduler(
        ["python", "runner.py"],
        cwd=tmp_path,
        log_path=log_path,
        platform="darwin",
        popen=lambda *_args, **_kwargs: Proc(),
        open_log=lambda _path, _mode: Log(),
    )
    assert log_path.parent.is_dir()


def test_spawn_detached_scheduler_closes_log_when_popen_fails(tmp_path):
    class Log:
        closed = False

        def write(self, _data):
            pass

        def flush(self):
            pass

        def close(self):
            self.closed = True

    log = Log()

    def fail(*_args, **_kwargs):
        raise OSError("cannot spawn")

    with pytest.raises(OSError, match="cannot spawn"):
        rp.spawn_detached_scheduler(
            ["python", "runner.py"],
            cwd=tmp_path,
            log_path=tmp_path / "runner_console.log",
            platform="darwin",
            popen=fail,
            open_log=lambda _path, _mode: log,
        )
    assert log.closed is True


def test_spawn_detached_scheduler_windows_arguments(tmp_path):
    calls = {}

    class Proc:
        pid = 99

    class Log:
        def write(self, _data):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return Proc()

    rp.spawn_detached_scheduler(
        ["QQPetCopilotScheduler.exe"],
        cwd=tmp_path,
        log_path=tmp_path / "runner_console.log",
        platform="win32",
        env={"PATH": "C:/Windows"},
        popen=fake_popen,
        open_log=lambda _path, _mode: Log(),
    )
    assert calls["argv"] == ["QQPetCopilotScheduler.exe"]
    assert "start_new_session" not in calls["kwargs"]
    assert calls["kwargs"]["creationflags"] == (
        rp.CREATE_NEW_PROCESS_GROUP | rp.CREATE_NO_WINDOW
    )
    assert calls["kwargs"]["env"] == {"PATH": "C:/Windows"}


def test_stop_process_posix_graceful_sigint(tmp_path):
    state = {"alive": True}
    signals = []

    def send(pid, sig):
        signals.append((pid, sig))
        state["alive"] = False

    result = rp.stop_process(
        123,
        platform="darwin",
        alive=lambda _pid: state["alive"],
        send_signal=send,
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    assert result == rp.StopResult(True, False, ("sigint",))
    assert signals == [(123, signal.SIGINT)]


def test_stop_process_already_dead_does_nothing():
    signals = []
    result = rp.stop_process(
        123,
        platform="darwin",
        alive=lambda _pid: False,
        send_signal=lambda *args: signals.append(args),
    )
    assert result == rp.StopResult(True, False, ())
    assert signals == []


def test_stop_process_posix_escalates_to_sigkill(monkeypatch):
    # Windows hosts need a synthetic POSIX constant for this mocked delivery.
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    clock = FakeClock()
    state = {"alive": True}
    signals = []

    def send(_pid, sig):
        signals.append(sig)
        if sig == signal.SIGKILL:
            state["alive"] = False

    result = rp.stop_process(
        123,
        platform="darwin",
        alive=lambda _pid: state["alive"],
        send_signal=send,
        sleep=clock.sleep,
        clock=clock,
        interrupt_wait=0.5,
        terminate_wait=0.5,
        kill_wait=0.5,
        poll_interval=0.25,
    )
    assert result.stopped is True
    assert result.forced is True
    assert result.attempts == ("sigint", "sigterm", "sigkill")
    assert signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]


def test_stop_process_windows_falls_back_to_taskkill_tree_force():
    clock = FakeClock()
    state = {"alive": True}
    commands = []

    def send(_pid, sig):
        assert _pid == -456
        assert sig == rp.CTRL_BREAK_EVENT
        raise OSError("no console")

    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        if "/F" in argv:
            state["alive"] = False

    result = rp.stop_process(
        456,
        platform="win32",
        alive=lambda _pid: state["alive"],
        send_signal=send,
        run_command=run,
        sleep=clock.sleep,
        clock=clock,
        interrupt_wait=0.5,
        terminate_wait=0.5,
        kill_wait=0.5,
        poll_interval=0.25,
    )
    assert result.stopped is True
    assert result.forced is True
    assert result.attempts == (
        "ctrl_break", "taskkill_tree", "taskkill_tree_force",
    )
    assert [entry[0] for entry in commands] == [
        ["taskkill", "/PID", "456", "/T"],
        ["taskkill", "/PID", "456", "/T", "/F"],
    ]


def test_stop_process_windows_ctrl_break_alone_is_graceful():
    state = {"alive": True}

    def send(_pid, sig):
        assert _pid == -456
        assert sig == rp.CTRL_BREAK_EVENT
        state["alive"] = False

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("taskkill must not run after a graceful stop")

    result = rp.stop_process(
        456,
        platform="win32",
        alive=lambda _pid: state["alive"],
        send_signal=send,
        run_command=should_not_run,
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    assert result == rp.StopResult(True, False, ("ctrl_break",))


def test_stop_process_windows_skips_taskkill_after_successful_break():
    state = {"alive": True}
    calls = []

    def send(_pid, sig):
        assert _pid == -456
        assert sig == rp.CTRL_BREAK_EVENT
        state["alive"] = False

    result = rp.stop_process(
        456,
        platform="win32",
        alive=lambda _pid: state["alive"],
        send_signal=send,
        run_command=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    assert result == rp.StopResult(True, False, ("ctrl_break",))
    assert calls == []


def test_pid_alive_injection_and_invalid_pid():
    assert rp.pid_alive(123, probe=lambda pid: pid == 123) is True
    assert rp.pid_alive(0, probe=lambda _pid: True) is False


def test_user_data_paths_override_and_portable_mode(tmp_path):
    override = tmp_path / "private-data"
    custom = rp.user_data_paths(
        tmp_path / "app",
        frozen=True,
        platform="darwin",
        env={rp.DATA_DIR_ENV: str(override)},
    )
    assert custom.root == override
    assert custom.config == override / "config.yaml"
    assert custom.runner_console_log == override / "runs" / "runner_console.log"

    portable = rp.user_data_paths(
        tmp_path / "app",
        portable=True,
        frozen=True,
        platform="win32",
        env={},
    )
    assert portable.root == tmp_path / "app"


def test_user_data_paths_installed_mode_is_outside_app(tmp_path):
    home = tmp_path / "home"
    paths = rp.user_data_paths(
        tmp_path / "Program Files" / "QQPetCopilot",
        frozen=True,
        platform="darwin",
        home=home,
        env={},
    )
    assert paths.root == home / "Library" / "Application Support" / rp.APP_NAME
    paths.ensure()
    assert paths.runs.is_dir()
    assert paths.logs.is_dir()
    assert paths.environment({"PATH": "/bin"})[rp.DATA_DIR_ENV] == str(paths.root)


def test_user_data_paths_windows_installed_mode_uses_localappdata(tmp_path):
    local = tmp_path / "LocalAppData"
    paths = rp.user_data_paths(
        tmp_path / "Program Files" / "QQPetCopilot",
        frozen=True,
        platform="win32",
        env={"LOCALAPPDATA": str(local)},
    )
    assert paths.root == local / rp.APP_NAME
    assert paths.runner_pid == local / rp.APP_NAME / "runs" / "runner.pid"


def test_windows_launcher_rebinds_dashboard_to_bundle_and_user_data(tmp_path, monkeypatch):
    import importlib
    import sys

    # Importing dashboard normally is avoided; stub the module surface the
    # launcher touches so this test cannot start a server or seed real data.
    fake = type(sys)("dashboard")
    fake.BASE = tmp_path / "bundle"
    fake.RUNS = tmp_path / "stale-runs"
    fake.RUNNER_PID = tmp_path / "stale-runner.pid"
    fake.LOGS = tmp_path / "stale-logs"
    fake.ADV_LIVE_FILE = tmp_path / "stale-adventure.jsonl"
    fake.PAUSE_SIGNAL = tmp_path / "stale-pause.signal"
    fake.SESSION_REWARD_FILE = tmp_path / "stale-session_rewards.jsonl"
    fake.PLAN_FILE = tmp_path / "stale-career_plan.json"
    fake.main = lambda: 0
    fake_config = type(sys)("src.config")
    fake_config.APP_ROOT = tmp_path / "stale-app"
    fake_config.PROJECT_ROOT = tmp_path / "stale-project"
    fake_config.CONFIG_FILE = tmp_path / "stale-config.yaml"
    monkeypatch.setitem(sys.modules, "dashboard", fake)
    monkeypatch.setitem(sys.modules, "src.config", fake_config)
    for name in (
        "src.progress", "src.queue_status", "src.status_cache", "src.notify",
        "src.notification_outbox", "src.ad_bonus", "src.coins",
        "src.fatigue", "src.hire_quota",
        "src.daily_summary", "src.tg_bot", "src.career_watch", "src.ocr",
    ):
        fake_dep = type(sys)(name)
        fake_dep.__file__ = str(tmp_path / "stale-project" / (name.replace(".", "/") + ".py"))
        fake_dep.PROJECT_ROOT = tmp_path / "stale-project"
        fake_dep.STATE_FILE = tmp_path / "stale-project" / "runs" / "state.json"
        monkeypatch.setitem(sys.modules, name, fake_dep)

    launcher = importlib.import_module("desktop.windows.dashboard_launcher")
    data = tmp_path / "portable-data"
    monkeypatch.setenv("QQPET_DATA_DIR", str(data))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    observed: dict[str, object] = {}

    def fake_main():
        observed["fake"] = {
            "BASE": fake.BASE,
            "RUNS": fake.RUNS,
            "RUNNER_PID": fake.RUNNER_PID,
            "LOGS": fake.LOGS,
            "ADV_LIVE_FILE": fake.ADV_LIVE_FILE,
            "PAUSE_SIGNAL": fake.PAUSE_SIGNAL,
            "SESSION_REWARD_FILE": fake.SESSION_REWARD_FILE,
            "PLAN_FILE": fake.PLAN_FILE,
        }
        observed["config"] = {
            "APP_ROOT": fake_config.APP_ROOT,
            "PROJECT_ROOT": fake_config.PROJECT_ROOT,
            "CONFIG_FILE": fake_config.CONFIG_FILE,
        }
        observed["state"] = sys.modules["src.progress"].STATE_FILE
        return 0

    fake.main = fake_main
    assert launcher.main() == 0
    actual_fake = observed["fake"]
    actual_config = observed["config"]
    assert actual_fake["BASE"] == tmp_path / "bundle"
    assert actual_fake["RUNS"] == data / "runs"
    assert actual_fake["RUNNER_PID"] == data / "runs" / "runner.pid"
    assert actual_fake["LOGS"] == data / "runs" / "logs"
    assert actual_fake["ADV_LIVE_FILE"] == data / "runs" / "adventure_live.jsonl"
    assert actual_fake["PAUSE_SIGNAL"] == data / "runs" / "pause.signal"
    assert actual_fake["SESSION_REWARD_FILE"] == data / "runs" / "session_rewards.jsonl"
    assert actual_fake["PLAN_FILE"] == data / "runs" / "career_plan.json"
    assert actual_config["APP_ROOT"] == data
    assert actual_config["PROJECT_ROOT"] == data
    assert actual_config["CONFIG_FILE"] == data / "config.yaml"
    assert observed["state"] == data / "runs" / "state.json"
    # Paths are restored after main(), so a later server in this interpreter
    # cannot keep writing into the portable configuration.
    assert fake.RUNS == tmp_path / "stale-runs"
    assert fake_config.CONFIG_FILE == tmp_path / "stale-config.yaml"


def test_launcher_rebind_module_paths_moves_runs_constants(tmp_path):
    import sys

    from desktop.windows import dashboard_launcher

    module = type(sys)("src.synthetic")
    module.RUNS = Path("/bundle/runs")
    module.MODEL_ROOT = Path("/bundle/runs/models/rapidocr")
    module.CONFIG_FILE = Path("/bundle/config.yaml")
    module.SQLITE_FILE = Path("/bundle/database.sqlite")
    dashboard_launcher._rebind_module_paths(module, tmp_path / "data")
    assert module.RUNS == tmp_path / "data" / "runs"
    assert module.MODEL_ROOT == tmp_path / "data" / "runs" / "models" / "rapidocr"
    assert module.CONFIG_FILE == tmp_path / "data" / "config.yaml"
    assert module.SQLITE_FILE == Path("/bundle/database.sqlite")


def test_scheduler_start_uses_active_data_root_for_child_env(monkeypatch, tmp_path):
    import dashboard

    root = tmp_path / "portable"
    monkeypatch.setenv(rp.DATA_DIR_ENV, str(root))
    monkeypatch.setenv(rp.PORTABLE_ENV, "1")
    paths = dashboard._runtime_data_paths()
    env = rp.scheduler_environment(paths, portable=True)
    assert paths.root == root
    assert env[rp.DATA_DIR_ENV] == str(root)
    assert env[rp.PORTABLE_ENV] == "1"
