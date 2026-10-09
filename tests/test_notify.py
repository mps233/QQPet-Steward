import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import onepush

from src.config import NotifyConfig
from src import notify


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(notify, 'log', lambda *args: None)
    monkeypatch.setattr(notify, '_LAST_GOOD_NOTIFY', None)
    monkeypatch.setattr(notify, '_ERROR_SENT_AT', {})
    monkeypatch.setattr(notify, 'urlopen', Mock(side_effect=AssertionError('Unexpected HTTP')))
    monkeypatch.setattr(notify.subprocess, 'run',
                        Mock(side_effect=AssertionError('Unexpected desktop notification')))
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        Mock(side_effect=AssertionError('Unexpected OnePush HTTP')))


def telegram_config(**changes):
    return NotifyConfig(telegram_enabled=True, telegram_token='private-test-token',
                        telegram_chat_id='private-test-chat', **changes)


def test_desktop_success_does_not_mask_remote_failure(monkeypatch):
    monkeypatch.setattr(notify, '_send_telegram_result', lambda *args: (False, 'network failed'))
    monkeypatch.setattr(notify, '_send_mac_toast', lambda *args, **kwargs: True)
    monkeypatch.setattr(notify.sys, 'platform', 'darwin')
    result = notify.send_detailed('test', 'body', cfg=telegram_config())
    assert not result['ok']
    assert result['results']['Telegram'] == 'network failed'
    assert result['results']['桌面'] == '成功'


def test_fatal_alert_uses_cached_channel_configuration(monkeypatch):
    cfg = telegram_config(win_toast=False)
    notify.remember_config(cfg)
    monkeypatch.setattr(notify, 'load_config', Mock(side_effect=ValueError('bad yaml')))
    deliver = Mock(return_value=(True, 'success'))
    monkeypatch.setattr(notify, '_send_telegram_result', deliver)
    assert notify.send_alert('test')
    assert deliver.call_args.args[0] is cfg


def test_test_notification_respects_disabled_channels(monkeypatch):
    cfg = telegram_config(win_toast=False)
    cfg.telegram_enabled = False
    monkeypatch.setattr(notify, 'load_config', lambda: SimpleNamespace(notify=cfg))
    deliver = Mock(side_effect=AssertionError('Disabled channel must not send'))
    monkeypatch.setattr(notify, '_send_telegram_result', deliver)
    result = notify.test_notify()
    assert not result['ok']
    assert result['results']['Telegram'] == '已关闭'


def test_incomplete_remote_channel_is_not_masked_by_desktop(monkeypatch):
    monkeypatch.setattr(notify, '_send_mac_toast', lambda *args, **kwargs: True)
    monkeypatch.setattr(notify.sys, 'platform', 'darwin')
    cfg = NotifyConfig(telegram_enabled=True)
    result = notify.send_detailed('test', 'body', cfg=cfg)
    assert not result['ok']
    assert result['results']['Telegram'] == '配置不完整'


def test_error_cooldown_only_starts_after_success(monkeypatch):
    monkeypatch.setattr(notify, 'load_config',
                        lambda: SimpleNamespace(notify=telegram_config(win_toast=False)))
    deliver = Mock(side_effect=[False, True])
    monkeypatch.setattr(notify, 'send', deliver)
    assert not notify.notify_error('test', key='audit')
    assert notify.notify_error('test', key='audit')
    assert not notify.notify_error('test', key='audit', log=lambda *args: None)
    assert deliver.call_count == 2


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.payload).encode()


@pytest.mark.parametrize('body, expected', [
    ({'ok': True}, True), ({'ok': False, 'description': 'denied'}, False),
    ({'code': 0}, True), ({'code': 1}, False),
    ({'StatusCode': 0}, True), ({'StatusCode': 19021}, False),
    ({}, False), ([], False),
])
def test_post_json_requires_provider_success(monkeypatch, body, expected):
    monkeypatch.setattr(notify, 'urlopen', lambda *args, **kwargs: Reply(body))
    assert notify._post_json('https://example.invalid', {})[0] is expected


def test_feishu_upload_requires_authorization(monkeypatch, tmp_path):
    image = tmp_path / 'image.png'
    image.write_bytes(b'test')
    capture = Mock(return_value=Reply({'code': 0, 'data': {'image_key': 'image-id'}}))
    monkeypatch.setattr(notify, 'urlopen', capture)
    assert notify._feishu_upload_image('', str(image)) is None
    capture.assert_not_called()
    assert notify._feishu_upload_image('private-access-token', str(image)) == 'image-id'
    request = capture.call_args.args[0]
    assert request.get_header('Authorization') == 'Bearer private-access-token'
    assert capture.call_args.kwargs['timeout'] == notify.HTTP_TIMEOUT


def test_feishu_missing_upload_token_falls_back_to_text(monkeypatch, tmp_path):
    image = tmp_path / 'image.png'
    image.write_bytes(b'test')
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    cfg = NotifyConfig(feishu_enabled=True, feishu_webhook='https://example.invalid')
    ok, detail = notify._send_feishu_result(cfg, 'title', 'body', str(image))
    assert ok and '未配置图片上传令牌' in detail
    assert post.call_count == 1
    assert post.call_args.args[1]['msg_type'] == 'text'


def test_feishu_image_only_does_not_mark_full_notification_delivered(monkeypatch, tmp_path):
    image = tmp_path / 'image.png'
    image.write_bytes(b'test')
    monkeypatch.setattr(notify, '_feishu_upload_image', lambda *args: 'image-id')
    monkeypatch.setattr(notify, '_post_json', Mock(side_effect=[(True, 'ok'), (False, 'text failed')]))
    cfg = NotifyConfig(feishu_enabled=True, feishu_webhook='https://example.invalid')
    ok, detail = notify._send_feishu_result(cfg, 'title', 'body', str(image))
    assert not ok and 'text failed' in detail


def test_telegram_photo_failure_preserves_text_and_reports_fallback(monkeypatch, tmp_path):
    image = tmp_path / 'image.png'
    image.write_bytes(b'test')
    monkeypatch.setattr(notify, '_telegram_send_photo', lambda *args: (False, 'offline'))
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    ok, detail = notify._send_telegram_result(telegram_config(), 'title', 'body', str(image))
    assert ok and '降级文本' in detail
    assert post.call_args.args[1]['text'] == 'title\nbody'


def test_onepush_network_error_is_not_success(monkeypatch):
    request = Mock(side_effect=requests.exceptions.ConnectionError('offline test'))
    monkeypatch.setattr(requests.sessions.Session, 'request', request)
    assert not notify._send_onepush('{provider: bark, key: test-key}', 'body')
    assert request.call_count == 1
    assert request.call_args.kwargs['timeout'] == notify.HTTP_TIMEOUT


@pytest.mark.parametrize('response, expected', [
    (None, False),
    (SimpleNamespace(status_code=200, json=lambda: {'code': 500}), False),
    (SimpleNamespace(status_code=200, json=lambda: {'code': 200}), True),
    (SimpleNamespace(status_code=500, json=lambda: {}), False),
])
def test_onepush_checks_transport_and_business_response(monkeypatch, response, expected):
    notifier = SimpleNamespace(request=lambda *args, **kwargs: response,
                               notify=lambda **kwargs: response)
    monkeypatch.setattr(onepush, 'get_notifier', lambda provider: notifier)
    assert notify._send_onepush('{provider: bark, key: test-key}', 'body') is expected


def test_delivery_history_has_body_results_but_no_credentials(monkeypatch):
    cfg = telegram_config(win_toast=False)
    monkeypatch.setattr(notify, '_send_telegram_result', lambda *args: (True, 'success'))
    assert notify.send('event', 'body private-test-token', cfg=cfg, event_key='event-id')
    record = json.loads(notify.HISTORY_FILE.read_text())
    assert record['event_key'] == 'event-id'
    assert record['content'] == 'body [REDACTED]'
    assert record['results']['Telegram'] == 'success'
    assert notify.HISTORY_FILE.stat().st_mode & 0o777 == 0o600


def test_desktop_preserves_event_title_and_escapes_applescript(monkeypatch):
    monkeypatch.setattr(notify.sys, 'platform', 'darwin')
    run = Mock(return_value=SimpleNamespace(returncode=0, stderr=''))
    monkeypatch.setattr(notify.subprocess, 'run', run)
    assert notify._send_mac_toast('body "quote" \\ slash', title='event title')
    script = run.call_args.args[0][-1]
    assert 'with title "event title"' in script
    assert '\\"quote\\"' in script
    assert '\\\\ slash' in script
