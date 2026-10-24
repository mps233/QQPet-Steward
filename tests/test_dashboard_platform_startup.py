"""Server startup must reach the HTTP loop on both Windows and macOS."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import dashboard


@pytest.mark.parametrize('has_sighup', [False, True], ids=['windows', 'macos'])
def test_server_runs_and_closes_with_platform_available_signals(
    monkeypatch, runs_dir, has_sighup
):
    calls = []
    registrations = []

    class FakeServer:
        server_port = 12345

        def handle_error(self, *_args):
            pass

        def serve_forever(self, *, poll_interval):
            calls.append(('serve_forever', poll_interval))

        def server_close(self):
            calls.append(('server_close',))

    server = FakeServer()

    def make_server(address, handler):
        assert address == ('127.0.0.1', 12345)
        assert handler is dashboard.Handler
        return server

    def register(signum, handler):
        registrations.append(signum)
        assert callable(handler)

    def unexpected_start():
        pytest.fail('startup test must not start Telegram or the scheduler')

    platform_signals = SimpleNamespace(SIGTERM=15, signal=register)
    if has_sighup:
        platform_signals.SIGHUP = 1
    monkeypatch.setattr(dashboard, 'signal', platform_signals)
    monkeypatch.setattr(dashboard, 'ThreadingHTTPServer', make_server)
    monkeypatch.setattr(dashboard, 'RUNS', runs_dir)
    monkeypatch.setattr(dashboard, 'LOGS', runs_dir / 'logs')
    monkeypatch.setattr(dashboard, 'LOCAL_CONTROL_FILE', runs_dir / 'desktop_control.token')
    monkeypatch.setattr(dashboard, '_CLIENT_PROTOCOL_STORE', None)
    monkeypatch.setattr(dashboard, 'start_tg_bot', unexpected_start)
    monkeypatch.setattr(dashboard, 'scheduler_start', unexpected_start)
    monkeypatch.setenv('QQPET_DISABLE_TG', '1')
    monkeypatch.delenv('QQPET_DASHBOARD_ACCESS_FILE', raising=False)
    monkeypatch.setattr(sys, 'argv', [
        'QQPetCopilot.exe', '--bind', '127.0.0.1', '--port', '12345',
    ])

    assert dashboard.main() == 0
    assert registrations == ([15, 1] if has_sighup else [15])
    assert calls == [('serve_forever', 0.5), ('server_close',)]
    assert server.access_key_file == runs_dir / 'dashboard_access.token'
    assert server.access_key_file.is_file()
    assert (runs_dir / 'desktop_control.token').is_file()
    assert not (runs_dir / 'runner.pid').exists()
