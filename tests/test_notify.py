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
    monkeypatch.setattr(notify, '_telegram_send_photo',
                        lambda *args, **kwargs: (False, 'offline'))
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    ok, detail = notify._send_telegram_result(telegram_config(), 'title', 'body', str(image))
    assert ok and '降级文本' in detail
    assert post.call_args.args[1]['text'] == 'title\nbody'


def test_telegram_uses_html_parse_mode_for_table_bodies(monkeypatch):
    """含 <pre> 表格的正文必须带 parse_mode=HTML，否则 TG 按纯文本发出满屏标签。"""
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    notify._send_telegram_result(telegram_config(), 'title', '<pre>a  b\nc  d</pre>')
    assert post.call_args.args[1]['parse_mode'] == 'HTML'


def test_telegram_plain_body_omits_parse_mode(monkeypatch):
    """老通知（纯文本）不带 parse_mode：正文恰好含 `<` 时不会被当标签解析失败。"""
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    notify._send_telegram_result(telegram_config(), 'title', 'a < b')
    assert 'parse_mode' not in post.call_args.args[1]


def test_telegram_falls_back_to_plain_text_when_html_rejected(monkeypatch):
    """HTML 被 TG 拒收（非法实体等）时剥标签重发，宁可丢表格也不能丢通知。"""
    post = Mock(side_effect=[(False, 'HTTP 400 bad entity'), (True, 'ok')])
    monkeypatch.setattr(notify, '_post_json', post)
    ok, detail = notify._send_telegram_result(telegram_config(), 'title', '<pre>x  y</pre>')
    assert ok and '降级' in detail
    assert post.call_count == 2
    assert post.call_args.args[1]['text'] == 'title\nx  y'
    assert 'parse_mode' not in post.call_args.args[1]


def test_html_tags_are_stripped_for_channels_that_cannot_render_them(monkeypatch):
    """飞书/桌面/OnePush 不认 HTML：正文要剥成纯文本，表格靠空格照样对齐。"""
    feishu = Mock(return_value=(True, 'ok'))
    toast = Mock(return_value=True)
    monkeypatch.setattr(notify, '_post_json', feishu)
    monkeypatch.setattr(notify, '_send_mac_toast', toast)
    monkeypatch.setattr(notify.sys, 'platform', 'darwin')
    cfg = NotifyConfig(feishu_enabled=True, feishu_webhook='https://example.invalid',
                       win_toast=True)
    result = notify.send_detailed('title', '<pre>a  b\nc  d</pre>', cfg=cfg)
    assert result['ok']
    assert feishu.call_args.args[1]['content']['text'] == 'title\na  b\nc  d'
    assert toast.call_args.args[0] == 'a  b\nc  d'


def test_empty_title_does_not_leave_a_leading_blank_line(monkeypatch):
    """空标题（冒险日报已改为不带前缀）不能在正文前顶一个空行。"""
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    notify.send_detailed('', '🧭 日报正文', cfg=telegram_config(win_toast=False))
    sent = post.call_args.args[1]['text']
    assert sent == '🧭 日报正文'
    assert not sent.startswith('\n')


def test_empty_title_is_dropped_from_rich_message_head(monkeypatch):
    """富消息里空标题不能留下 `<b></b>`（会渲染成一行空粗体）。"""
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    notify.send_detailed('', '<table><tr><td>a</td></tr></table>',
                         cfg=telegram_config(win_toast=False))
    html = post.call_args.args[1]['rich_message']['html']
    assert not html.startswith('<b></b>')
    assert html.startswith('<table')


def test_empty_title_falls_back_to_generic_name_for_desktop_channels(monkeypatch):
    """桌面通知/OnePush 的标题是系统级独立字段，空标题要回落到通用名。

    兜底发生在 `_send_mac_toast` / `_send_onepush` **内部**（它们才是真正把
    title 交给系统的地方），所以这里桩掉 subprocess 看最终脚本里的 title。
    """
    monkeypatch.setattr(notify.sys, 'platform', 'darwin')
    run = Mock(return_value=SimpleNamespace(returncode=0, stderr=''))
    monkeypatch.setattr(notify.subprocess, 'run', run)
    assert notify._send_mac_toast('正文', title='')
    script = run.call_args.args[0][-1]
    assert f'with title "{notify.TITLE}"' in script
    # OnePush 走 payload，同样兜底
    assert notify._fallback_title('') == notify.TITLE
    assert notify._fallback_title('   ') == notify.TITLE
    assert notify._fallback_title('自定义') == '自定义'
    assert notify._join_title('   ', '正文') == '正文'    # 全空白也算空


def test_non_empty_title_is_kept_as_is(monkeypatch):
    """有标题时行为不变（回归保护：别把 `_join_title` 写成总是吞标题）。"""
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    notify.send_detailed('[QQ宠物·收工总结]', '正文', cfg=telegram_config(win_toast=False))
    assert post.call_args.args[1]['text'] == '[QQ宠物·收工总结]\n正文'


def test_table_body_goes_through_rich_message_endpoint(monkeypatch):
    """含 <table> 的正文必须走 sendRichMessage —— parse_mode=HTML 不支持 table。"""
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    body = '<table bordered striped>\n<tr><td>a</td></tr>\n</table>'
    result = notify.send_detailed('标题', body, cfg=telegram_config(win_toast=False))
    assert result['ok'] and result['results']['Telegram'] == '成功（富消息）'
    url = post.call_args.args[0]
    assert url.endswith('/sendRichMessage')
    payload = post.call_args.args[1]
    assert payload['rich_message']['html'].startswith('<b>标题</b>')
    assert '<table bordered striped>' in payload['rich_message']['html']
    assert 'parse_mode' not in payload


def test_rich_message_failure_falls_back_to_plain_message(monkeypatch):
    """富消息不可用（旧服务器/权限）时回落普通消息：表格只是排版，通知不能丢。"""
    calls = []

    def post(url, payload):
        calls.append(url)
        if url.endswith('/sendRichMessage'):
            return False, 'HTTP 400 method not found'
        return True, 'ok'

    monkeypatch.setattr(notify, '_post_json', post)
    body = '<table><tr><td>a</td></tr></table>'
    result = notify.send_detailed('标题', body, cfg=telegram_config(win_toast=False))
    assert result['ok']
    assert calls[0].endswith('/sendRichMessage')
    assert calls[1].endswith('/sendMessage')
    sent = [c for c in calls if c.endswith('/sendMessage')]
    assert sent, '应回落到普通消息'


def test_plain_body_never_uses_rich_endpoint(monkeypatch):
    """普通正文不该走富消息：老通知的发送路径与解析行为保持不变。"""
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    notify.send_detailed('标题', '纯文本正文', cfg=telegram_config(win_toast=False))
    assert post.call_args.args[0].endswith('/sendMessage')


def test_table_is_stripped_to_readable_lines_for_other_channels(monkeypatch):
    """飞书/桌面不认 table：剥成一行一条（制表符分列），不能让标签漏出去。"""
    feishu = Mock(return_value=(True, 'ok'))
    toast = Mock(return_value=True)
    monkeypatch.setattr(notify, '_post_json', feishu)
    monkeypatch.setattr(notify, '_send_mac_toast', toast)
    monkeypatch.setattr(notify.sys, 'platform', 'darwin')
    cfg = NotifyConfig(feishu_enabled=True, feishu_webhook='https://example.invalid',
                       win_toast=True)
    body = ('说明行\n<table bordered striped>\n'
            '<tr><th>时间</th><th>金币</th></tr>\n'
            '<tr><td>09:00</td><td>+28</td></tr>\n</table>\n合计 +28')
    notify.send_detailed('标题', body, cfg=cfg)
    text = toast.call_args.args[0]
    assert '<table' not in text and '<td>' not in text
    assert '09:00\t+28' in text              # 列间保留制表符
    assert '说明行' in text and '合计 +28' in text
    assert '\n\n\n' not in text              # 表体不拉空行


def test_rich_truncation_keeps_table_tags_balanced():
    """富消息超限按字符硬切会把 <table>/<td> 切半截 → 同样整条拒收，必须补平。"""
    body = '<table bordered striped>\n' + ('<tr><td>x</td><td>y</td></tr>\n' * 4000) \
        + '</table>'
    out = notify._fit_rich(body)
    assert out.count('<table') == out.count('</table>')
    assert out.count('<tr>') == out.count('</tr>')
    assert out.count('<td>') == out.count('</td>')
    assert '已截断' in out


def test_rich_truncation_drops_trailing_unclosed_table():
    """连收尾标签都放不下时丢掉最后那张表，不能留半个 <table> 让它整条失败。"""
    body = 'x' * 100 + '<table>' + ('<tr><td>y</td></tr>' * 5000)
    out = notify._fit_rich(body, limit=200)
    assert '<table' not in out or out.count('<table') == out.count('</table>')


def test_short_rich_body_is_untouched():
    html = '<b>标题</b>\n<table><tr><td>a</td></tr></table>'
    assert notify._fit_rich(html) == html


def test_delivery_history_stores_plain_text_body(monkeypatch):
    """仪表盘历史是 pre-wrap 渲染：存 HTML 会让用户看到一堆标签。"""
    cfg = telegram_config(win_toast=False)
    monkeypatch.setattr(notify, '_post_json', Mock(return_value=(True, 'ok')))
    notify.send('title', '<pre>a  b</pre>', cfg=cfg, event_key='table')
    record = json.loads(notify.HISTORY_FILE.read_text())
    assert record['content'] == 'a  b'
    assert '<pre>' not in record['content']


def test_overlong_telegram_body_is_truncated_before_sending(monkeypatch):
    """超长正文在 Telegram 是整条拒收，必须在发送前自己砍掉。"""
    post = Mock(return_value=(True, 'ok'))
    monkeypatch.setattr(notify, '_post_json', post)
    notify._send_telegram_result(telegram_config(), 'title', 'x' * 9000)
    sent = post.call_args.args[1]['text']
    assert len(sent) <= notify.TELEGRAM_TEXT_LIMIT + 40
    assert '已截断' in sent


def test_truncation_closes_dangling_pre_tag(monkeypatch):
    """截断把 <pre> 砍剩半截会让标签不配平、TG 同样拒收，所以要补收尾。"""
    body = '<pre>' + ('表格行内容\n' * 900) + '</pre>'
    text, clipped = notify._fit_telegram(body)
    assert clipped
    assert text.count('<pre>') == text.count('</pre>')


def test_overlong_photo_caption_stays_tag_balanced(monkeypatch, tmp_path):
    """带截图的 caption 有 1024 上限：截断后若不补 </pre>，整张图会被 400 拒收。"""
    image = tmp_path / 'image.png'
    image.write_bytes(b'test')
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, **kwargs):
        captured['body'] = req.data
        return FakeResponse()

    monkeypatch.setattr(notify, 'urlopen', fake_urlopen)
    notify._telegram_send_photo('tok', 'chat', '<pre>' + ('表格\n' * 900),
                                str(image), html=True)
    body = captured['body'].decode('utf-8', 'replace')
    assert body.count('<pre>') == body.count('</pre>')


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
