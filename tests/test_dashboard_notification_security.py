import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import dashboard
from src import dashboard_auth, settings

KEY = 'test-dashboard-key-that-is-at-least-32-characters'


@contextmanager
def server(key_file=None):
    service = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
    service.access_key = KEY
    service.access_key_file = key_file
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{service.server_port}'
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


@pytest.fixture(autouse=True)
def _clean_login_limiter():
    """登录失败限流是进程级的（按来源 IP），测试之间必须清干净，
    否则前面用例故意输错的口令会把后面用例锁在 429 上。"""
    dashboard.AUTH_FAILURES.clear()
    yield
    dashboard.AUTH_FAILURES.clear()


def request(url, *, data=None, headers=None):
    req = Request(url, data=data, headers=headers or {})
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as e:
        response = e
    with response:
        return response.code, response.headers, response.read()


def test_pairing_key_is_persistent_and_private(tmp_path):
    path = tmp_path / 'access.token'
    key = dashboard_auth.load_access_key(path)
    assert dashboard_auth.load_access_key(path) == key
    assert len(key) >= 32
    assert path.stat().st_mode & 0o777 == 0o600


def test_custom_access_key_validation():
    assert dashboard_auth.validate_new_key('short')[0] is False
    assert dashboard_auth.validate_new_key('x' * 129)[0] is False
    assert dashboard_auth.validate_new_key('has\nnewline')[0] is False
    assert dashboard_auth.validate_new_key(12345)[0] is False
    ok, fixed = dashboard_auth.validate_new_key('  杨萌萌的仪表盘口令  ')
    assert ok and fixed == '杨萌萌的仪表盘口令'


def test_custom_access_key_saves_privately_and_marks_custom(tmp_path):
    path = tmp_path / 'access.token'
    dashboard_auth.load_access_key(path)          # 自动生成（43 位随机串）
    assert dashboard_auth.is_custom_key(path) is False
    dashboard_auth.save_access_key(path, 'hao-ji-de-kou-ling')
    assert path.read_text('utf-8').strip() == 'hao-ji-de-kou-ling'
    assert path.stat().st_mode & 0o777 == 0o600
    assert dashboard_auth.is_custom_key(path) is True
    assert dashboard_auth.load_access_key(path) == 'hao-ji-de-kou-ling'
    # 删掉访问码文件重新生成后，自定义标记必须失效（否则界面会谎报"已自定义"）
    path.unlink()
    regenerated = dashboard_auth.load_access_key(path)
    assert regenerated != 'hao-ji-de-kou-ling'
    assert dashboard_auth.is_custom_key(path) is False


def test_access_key_endpoint_rotates_key_and_session(monkeypatch, tmp_path):
    key_file = tmp_path / 'access.token'
    key_file.write_text(KEY + '\n')
    with server(key_file) as url:
        status, headers, _ = request(url + '/api/auth',
                                     data=json.dumps({'key': KEY}).encode(),
                                     headers={'Content-Type': 'application/json'})
        assert status == 200
        cookie = headers['Set-Cookie']
        new_key = 'my-easy-dashboard-key'
        status, headers, body = request(
            url + '/api/access-key', data=json.dumps({'key': new_key}).encode(),
            headers={'Content-Type': 'application/json', 'Cookie': cookie})
        assert status == 200 and json.loads(body)['ok']
        assert key_file.read_text('utf-8').strip() == new_key
        # 后端换发的新 Cookie 必须直接可用（否则用户改完码把自己锁在外面）
        rotated = headers['Set-Cookie']
        assert new_key not in rotated
        assert request(url + '/api/data', headers={'Cookie': rotated})[0] == 200
        # 旧 Cookie 立即失效，旧访问码也不能再登录
        assert request(url + '/api/data', headers={'Cookie': cookie})[0] == 401
        assert request(url + '/api/auth', data=json.dumps({'key': KEY}).encode(),
                       headers={'Content-Type': 'application/json'})[0] == 401
        # 太短的码被拒绝，且不落盘
        status, _, body = request(
            url + '/api/access-key', data=json.dumps({'key': '1234'}).encode(),
            headers={'Content-Type': 'application/json', 'Cookie': rotated})
        assert status == 400 and not json.loads(body)['ok']
        assert key_file.read_text('utf-8').strip() == new_key


def test_login_failures_are_rate_limited():
    with server() as base:
        for _ in range(dashboard.AUTH_FAIL_LIMIT):
            assert request(base + '/api/auth', data=b'{"key":"nope"}',
                           headers={'Content-Type': 'application/json'})[0] == 401
        assert request(base + '/api/auth', data=b'{"key":"nope"}',
                       headers={'Content-Type': 'application/json'})[0] == 429
        # 正确口令此刻也被挡住（限流是按来源 IP 的），避免"猜着猜着就绕过"
        assert request(base + '/api/auth', data=json.dumps({'key': KEY}).encode(),
                       headers={'Content-Type': 'application/json'})[0] == 429
    dashboard.AUTH_FAILURES.clear()


def test_sensitive_routes_require_authentication(monkeypatch):
    build = lambda: (_ for _ in ()).throw(AssertionError('Unauthorized data access'))
    monkeypatch.setattr(dashboard, 'build_data', build)
    with server() as base:
        assert request(base + '/api/data')[0] == 401
        assert request(base + '/api/settings', data=b'{}')[0] == 401
        assert request(base + '/api/notify/test', data=b'{}')[0] == 401
        assert request(base + '/api/screenshot')[0] == 401
        assert request(base + '/files/test.png')[0] == 401
        status, _, body = request(base + '/')
        assert status == 200 and b'/api/auth' in body
        assert KEY.encode() not in body


def test_login_cookie_and_bearer_authorize_data(monkeypatch):
    monkeypatch.setattr(dashboard, 'build_data', lambda: {'safe': True})
    with server() as base:
        status, headers, body = request(base + '/api/auth', data=json.dumps({'key': KEY}).encode(),
                                       headers={'Content-Type': 'application/json'})
        assert status == 200
        cookie = headers['Set-Cookie']
        assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
        assert KEY not in cookie
        assert request(base + '/api/data', headers={'Cookie': cookie})[0] == 200
        assert request(base + '/api/data', headers={'Authorization': f'Bearer {KEY}'})[0] == 200
        assert request(base + '/api/auth', data=b'{"key":"wrong"}',
                       headers={'Content-Type': 'application/json'})[0] == 401


def test_cross_origin_write_is_rejected():
    with server() as base:
        status, _, _ = request(base + '/api/settings', data=b'{}', headers={
            'Authorization': f'Bearer {KEY}', 'Origin': 'https://attacker.invalid'})
        assert status == 403


def test_snapshot_does_not_return_notification_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, 'BASE', tmp_path)
    (tmp_path / 'config.yaml').write_text('notify:\n  telegram_token: private-token\n'
        '  telegram_chat_id: private-chat\n  feishu_secret: private-secret\n'
        '  feishu_access_token: private-access\n  feishu_webhook: private-webhook\n')
    snapshot = dashboard.editable_snapshot()
    for field in ('telegram_token', 'telegram_chat_id', 'feishu_secret',
                  'feishu_access_token', 'feishu_webhook'):
        assert snapshot[f'notify_{field}'] == ''
        assert snapshot[f'notify_{field}_set']
    assert 'private-' not in json.dumps(snapshot)


def test_credential_save_response_is_masked_and_other_settings_preserved(monkeypatch, tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('notify:\n  telegram_enabled: true\n  telegram_token: old\n'
                    'schedule:\n  study_quota_hours: 7\n')
    monkeypatch.setattr(settings, 'CONFIG_FILE', path)
    result = dashboard.apply_settings({'notify_telegram_token': 'new-private-token'})
    assert result['ok']
    assert result['applied']['notify_telegram_token'] == '[configured]'
    assert settings.load_raw()['notify']['telegram_token'] == 'new-private-token'
    assert settings.load_raw()['schedule']['study_quota_hours'] == 7


@pytest.mark.parametrize('url', [
    'https://attacker.invalid/open.feishu.cn/open-apis/bot/v2/hook/x',
    'https://open.feishu.cn.attacker.invalid/open-apis/bot/v2/hook/x',
    'https://user:password@open.feishu.cn/open-apis/bot/v2/hook/x',
])
def test_webhook_validation_rejects_spoofed_host(url):
    assert not settings.validate_field('notify.feishu_webhook', url)[0]


def test_webhook_validation_accepts_real_host():
    assert settings.validate_field('notify.feishu_webhook',
        'https://open.feishu.cn/open-apis/bot/v2/hook/test')[0]


def test_notification_history_is_empty_before_first_send(monkeypatch, tmp_path):
    """历史文件在第一次发送后才创建；缺失时端点要返回空列表而不是 500。"""
    from src import notify
    monkeypatch.setattr(notify, 'HISTORY_FILE', tmp_path / 'notifications.jsonl')
    with server() as base:
        status, _, body = request(base + '/api/notify/history',
                                  headers={'Authorization': f'Bearer {KEY}'})
        assert status == 200
        assert json.loads(body) == {'records': []}


def test_notification_history_lists_newest_first(monkeypatch, tmp_path):
    from src import notify
    path = tmp_path / 'notifications.jsonl'
    path.write_text('\n'.join(json.dumps(record) for record in (
        {'ts': '2026-10-08 12:00:00', 'title': 'a', 'content': 'first'},
        {'ts': '2026-10-08 13:00:00', 'title': 'b', 'content': 'second'},
    )) + '\n')
    monkeypatch.setattr(notify, 'HISTORY_FILE', path)
    with server() as base:
        status, _, body = request(base + '/api/notify/history',
                                  headers={'Authorization': f'Bearer {KEY}'})
        assert status == 200
        records = json.loads(body)['records']
        assert [record['content'] for record in records] == ['second', 'first']
