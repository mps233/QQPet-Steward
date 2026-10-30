from __future__ import annotations

import json
import time

import dashboard


def _status(**values) -> str:
    payload = {
        'phase': 'latest', 'current': '0.8.7', 'latest': '0.8.7',
        'error': '', 'installable': True, 'done': 0, 'total': None,
    }
    payload.update(values)
    return json.dumps(payload)


def test_bridge_requires_fresh_heartbeat(tmp_path, monkeypatch) -> None:
    runs = tmp_path / 'runs'
    runs.mkdir(exist_ok=True)
    monkeypatch.setattr(dashboard, 'RUNS', runs)
    assert dashboard.update_bridge_state() == {'running': False, 'status': None}

    path = runs / dashboard.UPDATE_STATUS_FILE
    path.write_text(_status(updated_at=time.time()), encoding='utf-8')
    state = dashboard.update_bridge_state()
    assert state['running'] is True
    assert state['status']['phase'] == 'latest'

    path.write_text(_status(updated_at=time.time() - 3600), encoding='utf-8')
    assert dashboard.update_bridge_state()['running'] is False


def test_request_update_writes_one_shot_request(tmp_path, monkeypatch) -> None:
    runs = tmp_path / 'runs'
    runs.mkdir(exist_ok=True)
    monkeypatch.setattr(dashboard, 'RUNS', runs)
    (runs / dashboard.UPDATE_STATUS_FILE).write_text(
        _status(updated_at=time.time()), encoding='utf-8'
    )

    assert dashboard.request_update('check')['ok'] is True
    request = json.loads((runs / dashboard.UPDATE_REQUEST_FILE).read_text('utf-8'))
    assert request['action'] == 'check'
    assert request['id']
    assert dashboard.request_update('bogus')['ok'] is False


def test_request_update_refuses_without_running_app(tmp_path, monkeypatch) -> None:
    runs = tmp_path / 'runs'
    runs.mkdir(exist_ok=True)
    monkeypatch.setattr(dashboard, 'RUNS', runs)
    result = dashboard.request_update('install')
    assert result['ok'] is False
    assert not (runs / dashboard.UPDATE_REQUEST_FILE).exists()
