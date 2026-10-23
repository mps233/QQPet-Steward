"""The macOS menu-bar host uses a loopback-only control credential."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import dashboard

KEY = 'desktop-control-test-key-123456789'


@contextmanager
def server():
    service = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
    service.access_key = KEY
    service.access_key_file = None
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{service.server_port}'
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


def request(url: str, *, method: str = 'GET', headers: dict[str, str] | None = None):
    req = Request(url, method=method, headers=headers or {})
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        return response.code, response.read()


def test_control_key_is_private_and_persistent(monkeypatch, tmp_path):
    path = tmp_path / 'desktop_control.token'
    monkeypatch.setattr(dashboard, 'LOCAL_CONTROL_FILE', path)
    first = dashboard._local_control_key()
    second = dashboard._local_control_key()
    assert first == second
    assert len(first) >= 32
    assert path.stat().st_mode & 0o777 == 0o600


def test_loopback_control_reads_data_but_other_callers_do_not(monkeypatch):
    monkeypatch.setattr(dashboard, 'build_data', lambda: {'safe': True})
    token = dashboard._local_control_key()
    with server() as base:
        assert request(base + '/api/data')[0] == 401
        assert request(base + '/api/data', headers={
            dashboard.LOCAL_CONTROL_HEADER: 'wrong'
        })[0] == 401
        status, body = request(base + '/api/data', headers={
            dashboard.LOCAL_CONTROL_HEADER: token
        })
        assert status == 200
        assert json.loads(body) == {'safe': True}


def test_control_can_pause_and_resume_scheduler(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, 'PAUSE_SIGNAL', tmp_path / 'pause.signal')
    monkeypatch.setattr(dashboard, 'scheduler_pause', lambda: {'ok': True, 'msg': 'paused'})
    monkeypatch.setattr(dashboard, 'scheduler_resume', lambda: {'ok': True, 'msg': 'resumed'})
    token = dashboard._local_control_key()
    headers = {dashboard.LOCAL_CONTROL_HEADER: token}
    with server() as base:
        status, body = request(base + '/api/runner/pause', method='POST', headers=headers)
        assert status == 200 and json.loads(body)['ok']
        status, body = request(base + '/api/runner/resume', method='POST', headers=headers)
        assert status == 200 and json.loads(body)['ok']


def test_control_token_cannot_be_used_from_non_loopback(monkeypatch):
    class FakeHandler:
        client_address = ('192.0.2.5', 1234)
        headers = {dashboard.LOCAL_CONTROL_HEADER: dashboard._local_control_key()}

    assert dashboard._local_control_is_loopback(FakeHandler()) is False
    assert dashboard._local_control_authorized(FakeHandler()) is False
