import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import dashboard
from src import dashboard_auth
from src.client_protocol import (ClientProtocolStore, PairingAttemptLimiter,
                                 candidate_base_urls)

KEY = 'test-client-protocol-access-key-at-least-32-chars'
ORIGIN = 'http://phone.test'


def request(url, *, data=None, headers=None, method=None):
    req = Request(url, data=data, headers=headers or {}, method=method)
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, dict(response.headers), response.read()


def json_request(url, payload=None, *, headers=None, method=None):
    merged = {'Content-Type': 'application/json'}
    merged.update(headers or {})
    if payload is None and method is None:
        method = 'GET'
        data = None
    else:
        data = (json.dumps(payload, ensure_ascii=False).encode('utf-8')
                if payload is not None else b'')
    status, response_headers, body = request(
        url, data=data, headers=merged, method=method)
    return status, response_headers, (json.loads(body) if body else {})


@contextmanager
def server(store, key_file=None):
    service = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
    service.access_key = KEY
    service.access_key_file = key_file
    service.client_protocol_store = store
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{service.server_port}'
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


@pytest.fixture(autouse=True)
def _clean_errors():
    dashboard.AUTH_FAILURES.clear()
    dashboard.PAIRING_FAILURES.clear()
    yield
    dashboard.AUTH_FAILURES.clear()
    dashboard.PAIRING_FAILURES.clear()


@pytest.fixture
def clock():
    class Clock:
        now = 1_000_000.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    return Clock()


@pytest.fixture
def protocol_store(tmp_path, clock):
    return ClientProtocolStore(tmp_path / 'runs' / 'client_protocol.json', clock=clock)


def create_ticket(base, *, origin=None):
    headers = {'Authorization': f'Bearer {KEY}'}
    if origin:
        headers['Origin'] = origin
    status, _, payload = json_request(
        base + '/api/client/pairing', {}, headers=headers)
    assert status == 200
    return payload


def exchange(base, ticket, *, origin=None, name='Pixel', platform='android'):
    headers = {'Origin': origin} if origin else None
    return json_request(base + '/api/client/pair', {
        'ticket': ticket,
        'name': name,
        'platform': platform,
    }, headers=headers)


def test_protocol_meta_is_public_and_secret_free(protocol_store):
    with server(protocol_store) as base:
        status, headers, payload = json_request(base + '/api/client/meta')
        assert status == 200
        assert payload['protocol_version'] == 1
        assert payload['server']['name']
        assert 'client.pairing' in payload['capabilities']
        assert payload['pairing']['available'] is True
        assert KEY not in json.dumps(payload)
        assert 'token' not in payload


def test_public_meta_supports_remote_client_without_credentials(protocol_store):
    with server(protocol_store) as base:
        status, headers, _ = request(
            base + '/api/client/meta', headers={'Origin': ORIGIN})
        assert status == 200
        assert headers['Access-Control-Allow-Origin'] == ORIGIN
        assert 'Access-Control-Allow-Credentials' not in headers


def test_pair_exchange_allows_remote_origin_but_ticket_creation_does_not(protocol_store):
    with server(protocol_store) as base:
        status, _, _ = json_request(
            base + '/api/client/pairing', {},
            headers={'Authorization': f'Bearer {KEY}', 'Origin': ORIGIN})
        assert status == 403

        issued = create_ticket(base)
        status, headers, _ = json_request(base + '/api/client/pair', {
            'ticket': issued['ticket'],
            'name': 'phone',
            'platform': 'android',
        }, headers={'Origin': ORIGIN})
        assert status == 200
        assert headers['Access-Control-Allow-Origin'] == ORIGIN


def test_public_meta_is_get_only(protocol_store):
    with server(protocol_store) as base:
        status, _, _ = json_request(
            base + '/api/client/meta', {}, method='POST',
            headers={'Authorization': f'Bearer {KEY}'})
        assert status == 405


def test_pairing_success_is_one_time_and_persists_device(protocol_store, clock, tmp_path):
    with server(protocol_store) as base:
        issued = create_ticket(base)
        assert issued['ticket']
        assert issued['candidate_base_urls']
        assert 'qr_payload' in issued and 'deep_link' in issued
        assert issued['ticket'] not in json.dumps(protocol_store.list_devices())

        status, _, paired = exchange(base, issued['ticket'])
        assert status == 200
        token = paired['token']
        assert token and token != issued['ticket']
        assert paired['device']['name'] == 'Pixel'
        assert paired['device']['platform'] == 'android'

        # Ticket is one-time.
        status, _, again = exchange(base, issued['ticket'])
        assert status == 401 and again['error'] == 'pairing_ticket_invalid'

        # Token survives a fresh store instance reading the same file.
        restarted = ClientProtocolStore(protocol_store.path, clock=clock)
        assert restarted.authenticate_token(token)['id'] == paired['device']['id']
        raw = protocol_store.path.read_text('utf-8')
        assert token not in raw
        assert issued['ticket'] not in raw
        assert protocol_store.path.stat().st_mode & 0o777 == 0o600


def test_pair_accepts_device_name_alias_used_by_native_ui(protocol_store):
    with server(protocol_store) as base:
        issued = create_ticket(base)
        status, _, paired = json_request(base + '/api/client/pair', {
            'ticket': issued['ticket'],
            'device_name': 'Android remote',
            'platform': 'android',
        })
        assert status == 200
        assert paired['device']['name'] == 'Android remote'


def test_pairing_ticket_expires(protocol_store, clock):
    with server(protocol_store) as base:
        issued = create_ticket(base)
        clock.advance(301)
        status, _, payload = exchange(base, issued['ticket'])
        assert status == 401
        assert payload['error'] == 'pairing_ticket_invalid'


def test_pairing_failures_are_rate_limited(protocol_store, monkeypatch):
    limiter = PairingAttemptLimiter(limit=2, window=60)
    monkeypatch.setattr(dashboard, 'PAIRING_FAILURES', limiter)
    with server(protocol_store) as base:
        for _ in range(2):
            assert exchange(base, 'not-a-ticket')[0] == 401
        status, _, payload = exchange(base, 'not-a-ticket')
        assert status == 429
        assert payload['error'] == 'pairing_rate_limited'

        # A valid ticket is also blocked while the source is rate-limited.
        issued = create_ticket(base)
        assert exchange(base, issued['ticket'])[0] == 429


def test_session_heartbeat_devices_and_revocation(protocol_store):
    with server(protocol_store) as base:
        issued = create_ticket(base)
        status, _, paired = exchange(base, issued['ticket'])
        assert status == 200
        token = paired['token']
        device_id = paired['device']['id']
        bearer = {'Authorization': f'Bearer {token}'}

        status, _, session = json_request(
            base + '/api/client/session', headers=bearer)
        assert status == 200
        assert session['authenticated'] is True
        assert session['auth_type'] == 'device'
        assert session['device']['id'] == device_id
        assert token not in json.dumps(session)

        status, _, heartbeat = json_request(
            base + '/api/client/heartbeat', {}, headers=bearer)
        assert status == 200
        assert heartbeat['device']['last_seen']

        status, _, listing = json_request(
            base + '/api/client/devices', headers=bearer)
        assert status == 200
        assert [item['id'] for item in listing['devices']] == [device_id]

        # Same-origin browser session can revoke, and revocation is immediate.
        status, headers, _ = json_request(
            base + '/api/auth', {'key': KEY},
            headers={'Origin': base})
        assert status == 200
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        status, _, revoked = json_request(
            base + f'/api/client/devices/{device_id}', method='DELETE',
            headers={'Cookie': cookie, 'Origin': base})
        assert status == 200 and revoked['revoked'] == device_id
        assert protocol_store.authenticate_token(token) is None
        assert json_request(
            base + '/api/client/session', headers=bearer)[0] == 401


def test_legacy_bearer_still_authorizes_same_origin(monkeypatch, protocol_store):
    monkeypatch.setattr(dashboard, 'build_data', lambda: {'safe': True})
    with server(protocol_store) as base:
        # /api/data is intentionally not JSON-encoded by the legacy route;
        # read it raw and assert the auth boundary rather than the serializer.
        status, _, body = request(
            base + '/api/data', headers={'Authorization': f'Bearer {KEY}'})
        assert status == 200 and json.loads(body) == {'safe': True}
        assert json_request(
            base + '/api/client/session',
            headers={'Authorization': f'Bearer {KEY}'})[0] == 200


def test_paired_device_token_authorizes_data(monkeypatch, protocol_store):
    monkeypatch.setattr(dashboard, 'build_data', lambda: {'safe': True})
    with server(protocol_store) as base:
        issued = create_ticket(base)
        token = exchange(base, issued['ticket'])[2]['token']
        status, _, body = request(
            base + '/api/data', headers={'Authorization': f'Bearer {token}'})
        assert status == 200 and json.loads(body) == {'safe': True}


def test_pairing_fails_with_unwritable_or_corrupt_store(tmp_path):
    corrupt = tmp_path / 'client_protocol.json'
    corrupt.write_text('{broken', encoding='utf-8')
    store = ClientProtocolStore(corrupt)
    with server(store) as base:
        status, _, payload = json_request(
            base + '/api/client/meta')
        assert status == 200 and payload['pairing']['available'] is True
        status, _, payload = json_request(
            base + '/api/client/pairing', {},
            headers={'Authorization': f'Bearer {KEY}'})
        assert status == 500
        assert payload['error'] == 'pairing_unavailable'


def test_cors_preflight_and_device_origin(monkeypatch, protocol_store):
    monkeypatch.setattr(dashboard, 'build_data', lambda: {'safe': True})
    monkeypatch.setattr(dashboard, 'apply_settings', lambda _updates: {
        'ok': True, 'applied': {}, 'rejected': [],
    })
    with server(protocol_store) as base:
        issued = create_ticket(base)
        token = exchange(base, issued['ticket'])[2]['token']

        status, headers, _ = request(
            base + '/api/data', method='OPTIONS', headers={
                'Origin': ORIGIN,
                'Access-Control-Request-Method': 'GET',
                'Access-Control-Request-Headers': 'authorization, content-type',
            })
        assert status == 204
        assert headers['Access-Control-Allow-Origin'] == ORIGIN
        assert 'Authorization' in headers['Access-Control-Allow-Headers']
        assert set(headers['Access-Control-Allow-Methods'].replace(' ', '').split(',')) >= {
            'GET', 'POST', 'DELETE'}
        # Bearer auth is used, not cookies.  Omitting Allow-Credentials is
        # intentional so a browser can never attach a cookie cross-origin.
        assert 'Access-Control-Allow-Credentials' not in headers

        status, headers, body = request(
            base + '/api/data',
            headers={'Authorization': f'Bearer {token}', 'Origin': ORIGIN})
        assert status == 200 and json.loads(body) == {'safe': True}
        assert headers['Access-Control-Allow-Origin'] == ORIGIN
        status, headers, _ = request(
            base + '/api/client/session',
            headers={'Authorization': f'Bearer {token}', 'Origin': ORIGIN})
        assert status == 200
        assert headers['Access-Control-Allow-Origin'] == ORIGIN

        status, headers, _ = json_request(
            base + '/api/settings', {'updates': {}},
            headers={'Authorization': f'Bearer {token}', 'Origin': ORIGIN})
        assert status == 200
        assert headers.get('Access-Control-Allow-Origin') == ORIGIN


def test_legacy_bearer_cross_origin_is_still_rejected(protocol_store):
    with server(protocol_store) as base:
        status, headers, _ = json_request(
            base + '/api/client/session',
            headers={'Authorization': f'Bearer {KEY}', 'Origin': ORIGIN})
        assert status == 403
        assert 'Access-Control-Allow-Origin' not in headers


def test_cookie_cross_origin_write_is_rejected(protocol_store):
    with server(protocol_store) as base:
        status, headers, _ = json_request(
            base + '/api/auth', {'key': KEY}, headers={'Origin': base})
        assert status == 200
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        status, response_headers, _ = json_request(
            base + '/api/settings', {'updates': {}},
            headers={'Cookie': cookie, 'Origin': ORIGIN})
        assert status == 403
        assert 'Access-Control-Allow-Origin' not in response_headers


def test_protocol_store_rejects_plaintext_token_on_disk(protocol_store):
    created = protocol_store.create_ticket()
    token, _ = protocol_store.pair(created['ticket'], 'phone', 'android')
    raw = protocol_store.path.read_text('utf-8')
    assert token not in raw
    assert 'token_hash' in raw
    assert 'devices' in raw


def test_bearer_parser_is_case_insensitive_and_rejects_empty():
    class Headers(dict):
        pass

    headers = Headers(Authorization=f'bEaReR   {KEY}  ')
    assert dashboard_auth.bearer_credential(headers) == KEY
    assert dashboard_auth.bearer_credential(Headers(Authorization='Bearer   ')) is None
    assert dashboard_auth.bearer_credential(Headers()) is None


def test_candidate_base_urls_reject_header_injection_and_derive_lan():
    urls = candidate_base_urls(
        '192.168.1.20:8787', 8787, addresses=['192.168.1.21'])
    assert urls == ['http://192.168.1.20:8787', 'http://192.168.1.21:8787']
    assert candidate_base_urls('evil.test/x:8787', 8787, addresses=[]) == []
    assert candidate_base_urls('0.0.0.0:8787', 8787, addresses=[]) == []


def test_pairing_payload_binds_ticket_to_protocol_v1(protocol_store):
    issued = protocol_store.create_ticket()
    payload = dashboard.pairing_payloads(
        issued['ticket'], issued['expires_at'], ['http://192.168.1.2:8787'])
    assert payload['qr_data']['v'] == 1
    assert payload['qr_data']['ticket'] == issued['ticket']
    assert payload['payload'] == payload['deep_link']
    assert payload['deep_link'].startswith('qqpet://pair?')
    assert issued['ticket'] in payload['deep_link']


def test_pairing_route_returns_scanable_png(protocol_store):
    with server(protocol_store) as base:
        issued = create_ticket(base)
        image = issued.get('qr_image', '')
        assert image.startswith('data:image/png;base64,')
        import base64
        raw = base64.b64decode(image.split(',', 1)[1])
        assert raw.startswith(b'\x89PNG\r\n\x1a\n')
