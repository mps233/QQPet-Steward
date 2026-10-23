"""First-run setup state stays separate from real device and scheduler data."""
from __future__ import annotations

import io
import json
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import dashboard
from src.client_protocol import ClientProtocolStore
from src.dashboard_auth import COOKIE_NAME, session_value
from src.jsonio import write_json


KEY = 'onboarding-test-access-key'


@pytest.fixture
def onboarding_runs(monkeypatch, runs_dir, tmp_path):
    monkeypatch.setattr(dashboard, 'RUNS', runs_dir)
    monkeypatch.setattr(dashboard, 'APP_ROOT', tmp_path)
    monkeypatch.setattr(dashboard, 'LOGS', runs_dir / 'logs')
    monkeypatch.setattr(dashboard, '_CLIENT_PROTOCOL_STORE', None)
    monkeypatch.setattr(dashboard, 'scheduler_info',
                        lambda: {'alive': False, 'pid': None, 'uptime': ''})
    return runs_dir


def test_seeded_config_and_startup_files_still_require_onboarding(
        onboarding_runs, repo_root, tmp_path):
    (tmp_path / 'config.yaml').write_bytes((repo_root / 'config.example.yaml').read_bytes())
    logs = onboarding_runs / 'logs'
    logs.mkdir()
    (logs / '2026-10-24.log').write_text('dashboard started\n', encoding='utf-8')
    write_json(onboarding_runs / 'queue_status.json', {
        'starting': True, 'pid': 123, 'current': '', 'pending': '',
    })
    write_json(onboarding_runs / 'status_cache.json', {
        'accounts': {'default': {'updated': '2026-10-24 12:00:00', 'energy': None}},
    })
    write_json(onboarding_runs / 'work_progress.json', {
        'date': '2026-10-24', 'learned': 0, 'history': {'2026-10-23': 0},
    })
    assert dashboard.build_data()['onboarding'] == {
        'version': 1, 'required': True, 'completed': False,
    }
    assert not (onboarding_runs / 'onboarding.json').exists()


@pytest.mark.parametrize(('name', 'data'), [
    ('status_cache.json', {'accounts': {'default': {'coins': 0}}}),
    ('status_cache.json', {'accounts': {'old-account': {'pet_name': 'test pet'}}}),
    ('work_progress.json', {'learned': 1}),
    ('school_progress.json', {'study_secs': 600}),
    ('work_progress.json', {'learned': 0, 'history': {'2026-10-23': 2}}),
    ('exp_daily_progress.json', {'done': True}),
    ('queue_status.json', {'pending': 'work'}),
])
def test_existing_activity_does_not_interrupt_user(onboarding_runs, name, data):
    write_json(onboarding_runs / name, data)
    assert dashboard.onboarding_status() == {
        'version': 1, 'required': False, 'completed': False,
    }


@pytest.mark.parametrize(('name', 'record'), [
    ('session_rewards.jsonl', {'kind': 'work', 'coins': 28}),
    ('adventure_live.jsonl', {'coins': 0, 'settle': []}),
])
def test_existing_reward_records_skip_automatic_guide(onboarding_runs, name, record):
    (onboarding_runs / name).write_text(
        'interrupted record\n' + json.dumps(record) + '\n', encoding='utf-8')
    assert dashboard.onboarding_status()['required'] is False


def test_corrupt_or_empty_records_do_not_hide_first_run(onboarding_runs):
    (onboarding_runs / 'onboarding.json').write_text('{broken', encoding='utf-8')
    (onboarding_runs / 'work_progress.json').write_text('[]', encoding='utf-8')
    (onboarding_runs / 'session_rewards.jsonl').write_text(
        'broken\n{}\n[]\n', encoding='utf-8')
    assert dashboard.onboarding_status()['required'] is True


def test_completion_is_persisted_and_idempotent(onboarding_runs, monkeypatch):
    first = dashboard.complete_onboarding()
    path = onboarding_runs / 'onboarding.json'
    saved = json.loads(path.read_text('utf-8'))
    assert saved['version'] == 1 and saved['completed'] is True
    assert saved['completed_at']
    assert not path.with_name(path.name + '.tmp').exists()
    assert first == {
        'ok': True,
        'onboarding': {'version': 1, 'required': False, 'completed': True},
    }

    def unexpected_write(*_args, **_kwargs):
        raise AssertionError('idempotent completion must keep the saved timestamp')

    monkeypatch.setattr(dashboard, 'write_json', unexpected_write)
    assert dashboard.complete_onboarding() == first


@contextmanager
def server(store=None):
    service = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
    service.access_key = KEY
    if store is not None:
        service.client_protocol_store = store
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{service.server_port}'
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


def post(base, payload, *, authenticated=True, origin=None):
    headers = {'Content-Type': 'application/json'}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + KEY
    if origin is not None:
        headers['Origin'] = origin
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    request = Request(base + '/api/onboarding', data=raw, headers=headers, method='POST')
    try:
        response = urlopen(request, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


def test_completion_route_preserves_auth_origin_and_payload_guards(onboarding_runs):
    path = onboarding_runs / 'onboarding.json'
    with server() as base:
        assert post(base, {'action': 'complete'}, authenticated=False)[0] == 401
        assert post(base, {'action': 'complete'}, origin='https://elsewhere.invalid')[0] == 403
        for invalid in ({'action': 'skip'}, [], b'{broken', b'x' * 4097):
            status, body = post(base, invalid)
            assert status == 400
            assert body == {'ok': False, 'error': 'invalid_onboarding_action'}
        assert not path.exists()
        status, body = post(base, {'action': 'complete'}, origin=base)
        assert status == 200 and body['onboarding']['completed'] is True


def test_remote_completion_requires_paired_device_and_returns_cors(onboarding_runs):
    store = ClientProtocolStore(onboarding_runs / 'client_protocol.json')
    ticket = store.create_ticket()['ticket']
    token, _device = store.pair(ticket, 'remote dashboard', 'web')
    origin = 'https://remote-client.invalid'
    credentials = [
        ({'Authorization': 'Bearer ' + KEY}, 403),
        ({'Cookie': f'{COOKIE_NAME}={session_value(KEY)}'}, 403),
        ({'Authorization': 'Bearer invalid-device-token'}, 403),
        ({'Authorization': 'Bearer ' + token}, 200),
    ]
    with server(store) as base:
        for credential, expected in credentials:
            request = Request(
                base + '/api/onboarding',
                data=b'{"action":"complete"}',
                headers={'Origin': origin, 'Content-Type': 'application/json', **credential},
                method='POST',
            )
            try:
                response = urlopen(request, timeout=3)
            except HTTPError as error:
                response = error
            with response:
                assert response.status == expected
                payload = json.loads(response.read())
                if expected == 200:
                    assert payload['onboarding']['completed'] is True
                    assert response.headers['Access-Control-Allow-Origin'] == origin
                    assert response.headers.get('Access-Control-Allow-Credentials') != 'true'
                else:
                    assert 'Access-Control-Allow-Origin' not in response.headers
                    assert not (onboarding_runs / 'onboarding.json').exists()


def test_screenshot_resolves_bundled_adb_and_uses_saved_device(monkeypatch):
    from PIL import Image

    image = io.BytesIO()
    Image.new('RGB', (4, 6)).save(image, format='PNG')
    calls = []
    monkeypatch.setattr(dashboard, '_adb_settings',
                        lambda: ('resources/scrcpy-win64/adb.exe', 'selected-device'))
    monkeypatch.setattr(dashboard, '_resolve_adb',
                        lambda _configured: ('/bundled/platform-tools/adb', ''))
    monkeypatch.setattr(dashboard, '_shot_cache', {
        'ts': 0.0, 'data': b'', 'at': '', 'source': None,
    })

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=image.getvalue(), stderr=b'')

    monkeypatch.setattr(dashboard.subprocess, 'run', run)
    jpeg, at, cached = dashboard.capture_phone()
    assert jpeg.startswith(b'\xff\xd8') and at and cached is False
    assert calls == [[
        '/bundled/platform-tools/adb', '-s', 'selected-device',
        'exec-out', 'screencap', '-p',
    ]]


def test_device_change_discards_previous_screenshot_even_if_new_capture_fails(monkeypatch):
    monkeypatch.setattr(dashboard, '_shot_cache', {
        'ts': time.time(), 'data': b'old-device-image', 'at': '12:00:00',
        'source': ('adb', 'old-device'),
    })
    monkeypatch.setattr(dashboard, '_adb_settings', lambda: ('adb', 'new-device'))
    monkeypatch.setattr(dashboard, '_resolve_adb', lambda _configured: ('/bundled/adb', ''))
    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=1, stdout=b'', stderr=b'device offline'))
    with pytest.raises(RuntimeError, match='device offline'):
        dashboard.capture_phone()
    assert dashboard._shot_cache['data'] == b''
    assert dashboard._shot_cache['source'] == ('adb', 'new-device')


def test_inflight_old_capture_cannot_replace_new_device_cache(monkeypatch):
    from PIL import Image

    image = io.BytesIO()
    Image.new('RGB', (4, 6)).save(image, format='PNG')
    monkeypatch.setattr(dashboard, '_shot_cache', {
        'ts': 0.0, 'data': b'', 'at': '', 'source': None,
    })
    monkeypatch.setattr(dashboard, '_adb_settings', lambda: ('adb', 'old-device'))
    monkeypatch.setattr(dashboard, '_resolve_adb', lambda _configured: ('/bundled/adb', ''))

    def run(*_args, **_kwargs):
        dashboard._shot_cache.update(
            ts=time.time(), data=b'new-device-image', at='12:00:01',
            source=('adb', 'new-device'))
        return SimpleNamespace(returncode=0, stdout=image.getvalue(), stderr=b'')

    monkeypatch.setattr(dashboard.subprocess, 'run', run)
    dashboard.capture_phone()
    assert dashboard._shot_cache['data'] == b'new-device-image'
    assert dashboard._shot_cache['source'] == ('adb', 'new-device')
