"""告警与事件通知：多渠道推送（参考 qq-farm-copilot）。

渠道（config.yaml 的 notify 段配置，可叠加；启用远程渠道时须有远程成功才算送达）：
- Windows/macOS 桌面通知：notify.win_toast: true（Windows 需 winotify）
- OnePush 推送：notify.onepush_config 填 YAML（设置页是多行输入框），
  支持 Bark / PushPlus / Server酱 / Telegram / SMTP / 自定义 webhook 等
  onepush 提供方，例如：
    onepush_config: "{provider: bark, key: 你的Key}"
  各提供方参数教程（ALAS wiki 中文文档）：
  https://github.com/LmeSzinc/AzurLaneAutoScript/wiki/Onepush-configuration-%5BCN%5D
- **飞书群机器人**：notify.feishu_enabled + feishu_webhook（+ 可选 feishu_secret 加签）
- **Telegram Bot**：notify.telegram_enabled + telegram_token + telegram_chat_id

飞书/Telegram 用标准库 urllib 直接发（不依赖 onepush），配置项少、设置页可直接填。
告警时可附当前手机屏幕截图（image_path）：Toast 用作图标、飞书/Telegram 直接传图
（Telegram 用 sendPhoto、飞书用图片上传接口；上传失败则降级为纯文本）。
发送失败只记日志不抛异常——通知本身不能再把调度器弄崩。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import NotifyConfig, PROJECT_ROOT, load_config
from .jsonio import append_jsonl
from .progress import log

TITLE = '[QQ宠物助手告警]'
# 错误/降级通知（"出错了但没崩"类，如配置读取失败后沿用旧配置）
ERROR_TITLE = '[QQ宠物·异常]'
HTTP_TIMEOUT = 20
HISTORY_FILE = PROJECT_ROOT / 'runs' / 'notifications.jsonl'
_HISTORY_LOCK = threading.Lock()


def send_alert(reason: str, image_path: str | None = None) -> bool:
    """按 notify 配置发送告警，返回是否有渠道发送成功。"""
    return send(TITLE, reason, image_path)


# 错误通知限频：按 key（错误类别）记上次发送时间，避免同一错误刷屏。
# 调度器是长驻进程，模块级字典即可（进程重启后计数清零，可接受）。
_ERROR_SENT_AT: dict[str, float] = {}
# 同一 key 最短发送间隔（秒）——默认 30 分钟；同类错误在这期间只推一次
ERROR_NOTIFY_COOLDOWN = 1800
# 最近一次**成功**读取的 notify 配置：配置坏掉时（error_notify 的典型场景就是
# "config.yaml 解析失败"）用它兜底，否则会陷入死锁——配置坏了要通知用户，而
# 读渠道配置又需要先读配置（实测踩坑：notify_error 打日志"读取配置失败，跳过推送"，
# 通知根本发不出去）。进程内缓存，首次成功读取后一直可用。
_LAST_GOOD_NOTIFY: NotifyConfig | None = None


def _notify_cfg() -> NotifyConfig | None:
    """取 notify 配置：正常读取并缓存；读失败时返回上次成功的副本。"""
    global _LAST_GOOD_NOTIFY
    try:
        cfg = load_config().notify
        _LAST_GOOD_NOTIFY = cfg
        return cfg
    except Exception as e:
        if _LAST_GOOD_NOTIFY is not None:
            log(f'通知: 读取配置失败（{e}），改用上次成功的渠道配置发送')
            return _LAST_GOOD_NOTIFY
        log(f'通知: 读取配置失败（{e}）且无可用缓存，跳过推送')
        return None


def remember_config(cfg: NotifyConfig) -> None:
    """Refresh the fallback when the scheduler successfully reloads settings."""
    global _LAST_GOOD_NOTIFY
    _LAST_GOOD_NOTIFY = cfg


def prime_config() -> bool:
    """预热通知配置缓存（调度器启动、配置还正常时调用）。

    这样之后 config.yaml 被改坏也能用这份缓存发错误通知（见 _notify_cfg 的说明）。
    返回是否成功建立缓存。
    """
    return _notify_cfg() is not None


def notify_error(reason: str, key: str = 'default', cooldown: int = ERROR_NOTIFY_COOLDOWN,
                 image_path: str | None = None, log=print) -> bool:
    """发送错误/降级类通知（受 notify.error_notify 开关控制），同一 key 限频。

    用于"出错了但没崩、会静默降级"的场景——用户最需要知道却最容易漏掉，
    例如配置读取失败后一直沿用旧配置（界面改什么都不生效）。

    **配置坏掉时也能发**：走 _notify_cfg() 的"上次成功配置"兜底——否则最需要
    通知的场景（config.yaml 写坏了）反而发不出去。
    返回是否真的发出了（被限频/开关关闭/无可用渠道/发送失败都算未发出）。
    """
    cfg = _notify_cfg()
    if cfg is None:
        return False
    if not getattr(cfg, 'error_notify', True):
        return False
    now = time.time()
    last = _ERROR_SENT_AT.get(key)
    if last is not None and (now - last) < max(0, cooldown):
        left = int((max(0, cooldown) - (now - last)) / 60) + 1
        log(f'错误通知: 同类错误（{key}）{left} 分钟内已推送过，本次跳过')
        return False
    ok = send(ERROR_TITLE, reason, image_path, cfg=cfg)
    if ok:
        _ERROR_SENT_AT[key] = now
    return ok


def _redact(text: str, cfg: NotifyConfig) -> str:
    for name in ('feishu_webhook', 'feishu_secret', 'feishu_access_token',
                 'telegram_token', 'telegram_chat_id', 'onepush_config'):
        secret = str(getattr(cfg, name, '') or '').strip()
        if secret:
            text = text.replace(secret, '[REDACTED]')
    try:
        import yaml
        extra = yaml.safe_load(cfg.onepush_config) or {}
        if isinstance(extra, dict):
            for name, value in extra.items():
                if any(word in name.lower() for word in ('key', 'token', 'secret', 'password')):
                    if isinstance(value, str) and value:
                        text = text.replace(value, '[REDACTED]')
    except Exception:
        pass
    return text


def send_detailed(title: str, content: str, image_path: str | None = None,
                  cfg: NotifyConfig | None = None, *,
                  target: str = 'all', event_key: str | None = None) -> dict:
    """Return channel results. Desktop success never masks a failed remote push."""
    cfg = cfg or _notify_cfg()
    if cfg is None:
        return {'ok': False, 'msg': '读取配置失败且无可用缓存', 'results': {}}
    content = _redact(content, cfg)
    results: dict[str, str] = {}
    successes: set[str] = set()
    remote_enabled: set[str] = set()
    channels = (
        ('飞书', 'feishu', cfg.feishu_enabled,
         bool(str(cfg.feishu_webhook).strip()),
         lambda: _send_feishu_result(cfg, title, content, image_path)),
        ('Telegram', 'telegram', cfg.telegram_enabled,
         bool(str(cfg.telegram_token).strip() and str(cfg.telegram_chat_id).strip()),
         lambda: _send_telegram_result(cfg, title, content, image_path)),
        ('OnePush', 'onepush', bool(str(cfg.onepush_config).strip()), True,
         lambda: _send_onepush(str(cfg.onepush_config), content, image_path, title=title)),
        ('桌面', 'desktop', cfg.win_toast, True,
         lambda: (_send_mac_toast(content, title=title) if sys.platform == 'darwin'
                  else _send_windows_toast(content, image_path, title=title))),
    )
    for label, key, enabled, complete, deliver in channels:
        if target not in ('all', key):
            continue
        if not enabled:
            results[label] = '已关闭'
            continue
        if key != 'desktop':
            remote_enabled.add(label)
        if not complete:
            results[label] = '配置不完整'
            continue
        try:
            response = deliver()
            ok, detail = response if isinstance(response, tuple) else (
                bool(response), '成功' if response else '失败（详见日志）')
            results[label] = _redact(detail, cfg)
            if ok:
                successes.add(label)
        except Exception as e:
            results[label] = f'失败：{_redact(str(e), cfg)}'
            log(f'{label}通知: {results[label]}')
    ok = bool(successes & remote_enabled) if remote_enabled else bool(successes)
    summary = '、'.join(f'{key}: {value}' for key, value in results.items()) or '无可测渠道'
    if not ok:
        log(f'通知未送达: {summary}')
    try:
        with _HISTORY_LOCK:
            append_jsonl(HISTORY_FILE, {
                'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'title': title,
                'content': _redact(content, cfg),
                'image': Path(image_path).name if image_path else None,
                'event_key': event_key, 'ok': ok, 'results': results,
            })
        HISTORY_FILE.chmod(0o600)
    except Exception as e:
        log(f'通知记录写入失败（不影响发送）: {e}')
    return {'ok': ok, 'msg': summary, 'results': results}


def send(title: str, content: str, image_path: str | None = None,
         cfg: NotifyConfig | None = None, *, event_key: str | None = None) -> bool:
    return bool(send_detailed(title, content, image_path, cfg,
                              event_key=event_key)['ok'])


# ---------------------------------------------------------------- 飞书群机器人

def _feishu_sign(secret: str, timestamp: int) -> str:
    """飞书加签：以 "{timestamp}\\n{secret}" 为**密钥**、空串为消息做 HMAC-SHA256。

    注意是拿 timestamp+secret 当 key（不是拿 secret 当 key、timestamp 当消息）——
    飞书文档的写法容易看反，实测按文档示例算才对得上。
    """
    key = f'{timestamp}\n{secret}'.encode('utf-8')
    return base64.b64encode(hmac.new(key, b'', digestmod=hashlib.sha256).digest()).decode()


def _post_json(url: str, payload: dict) -> tuple[bool, str]:
    """POST JSON，返回 (是否成功, 说明)。飞书/Telegram 都用它，统一错误处理。"""
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = Request(url, data=body, method='POST',
                  headers={'Content-Type': 'application/json; charset=utf-8'})
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        data = json.loads(raw) if raw.strip() else {}
    except HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', errors='replace')[:200]
        except Exception:
            pass
        return False, f'HTTP {e.code} {detail}'
    except URLError as e:
        return False, f'网络错误 {e.reason}'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
    # A 200 transport response is not enough: require the provider's success flag.
    if isinstance(data, dict):
        if 'ok' in data:
            return (True, 'ok') if data['ok'] is True else (
                False, str(data.get('description') or 'ok=false'))
        code = data.get('code', data.get('StatusCode'))
        if code is not None:
            return (True, 'ok') if code == 0 else (
                False, f"code={code} {data.get('msg') or data.get('StatusMessage') or ''}".strip())
    return False, '响应缺少成功标记'


def _feishu_upload_image(access_token: str, image_path: str,
                         webhook: str = '') -> str | None:
    """把图片传到飞书拿 image_key（发图必须先上传）。失败返回 None。

    图片上传需要应用 tenant/user access token，不能用 webhook 代替。
    """
    if not access_token:
        log('飞书通知: 未配置图片上传 access token，仅发文本')
        return None
    host = 'open.larksuite.com' if 'open.larksuite.com/' in webhook else 'open.feishu.cn'
    url = f'https://{host}/open-apis/im/v1/images'
    boundary = '----QQPetCopilot' + uuid.uuid4().hex
    try:
        with open(image_path, 'rb') as f:
            data = f.read()
    except OSError as e:
        log(f'飞书通知: 读取截图失败: {e}')
        return None
    ctype = mimetypes.guess_type(image_path)[0] or 'image/png'
    name = os.path.basename(image_path)
    parts = []
    parts.append(f'--{boundary}\r\n'.encode())
    parts.append(b'Content-Disposition: form-data; name="image_type"\r\n\r\n')
    parts.append(b'message\r\n')
    parts.append(f'--{boundary}\r\n'.encode())
    parts.append(
        f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'.encode())
    parts.append(f'Content-Type: {ctype}\r\n\r\n'.encode())
    parts.append(data)
    parts.append(f'\r\n--{boundary}--\r\n'.encode())
    body = b''.join(parts)
    req = Request(url, data=body, method='POST', headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Authorization': f'Bearer {access_token}',
    })
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
        if payload.get('code') == 0:
            return ((payload.get('data') or {}).get('image_key')) or None
        log(f"飞书通知: 图片上传失败 code={payload.get('code')} "
            f"{payload.get('msg') or ''}".strip())
    except Exception as e:
        log(f'飞书通知: 图片上传失败: {type(e).__name__}: {e}')
    return None


def _send_feishu_result(cfg: NotifyConfig, title: str, content: str,
                        image_path: str | None = None) -> tuple[bool, str]:
    """发送飞书群机器人消息。有截图先传图（image 消息），否则发文本。

    飞书自定义机器人支持的 msg_type：text / post / image / interactive / share_chat。
    """
    webhook = str(cfg.feishu_webhook).strip()
    if not webhook:
        log('飞书通知: 未填 webhook，跳过')
        return False, '未填 webhook'
    payload: dict = {}
    secret = str(getattr(cfg, 'feishu_secret', '') or '').strip()
    if secret:
        ts = int(time.time())
        payload['timestamp'] = str(ts)
        payload['sign'] = _feishu_sign(secret, ts)

    text = f'{title}\n{content}'
    # 有截图：先传图，成功则发一条图片 + 一条文本（图单独发，文本带完整信息）
    sent_any = False
    image_note = ''
    if image_path and os.path.exists(image_path):
        key = _feishu_upload_image(
            str(getattr(cfg, 'feishu_access_token', '') or '').strip(), image_path, webhook)
        if key:
            ok, why = _post_json(webhook, {**payload, 'msg_type': 'image',
                                           'content': {'image_key': key}})
            if ok:
                sent_any = True
            else:
                log(f'飞书通知: 图片消息发送失败: {why}')
        else:
            log('飞书通知: 图片上传未成功，仅发文本')
            image_note = '，图片上传未成功' if getattr(cfg, 'feishu_access_token', '') else '，未配置图片上传令牌'
    ok, why = _post_json(webhook, {**payload, 'msg_type': 'text',
                                   'content': {'text': text}})
    if ok:
        log('飞书通知: 发送成功')
        return True, f'成功（文本{image_note}）'
    log(f'飞书通知: 发送失败: {why}')
    return False, f'{"图片已发送，但文本失败" if sent_any else "失败"}：{_redact(why, cfg)}'


# ---------------------------------------------------------------- Telegram Bot

def _send_telegram_result(cfg: NotifyConfig, title: str, content: str,
                          image_path: str | None = None) -> tuple[bool, str]:
    """发送 Telegram 消息（有截图用 sendPhoto，否则 sendMessage）。"""
    token = str(cfg.telegram_token).strip()
    chat_id = str(cfg.telegram_chat_id).strip()
    if not token or not chat_id:
        log('Telegram 通知: token 或 chat_id 未填，跳过')
        return False, '未填 token 或 chat_id'
    text = f'{title}\n{content}'
    photo_failed = False
    if image_path and os.path.exists(image_path):
        ok, why = _telegram_send_photo(token, chat_id, text, image_path)
        if ok:
            log('Telegram 通知: 发送成功（图片）')
            return True, '成功（图片）'
        photo_failed = True
        log(f'Telegram 通知: 图片发送失败（{_redact(why, cfg)}），降级为文本')
    base = f'https://api.telegram.org/bot{token}'
    ok, why = _post_json(f'{base}/sendMessage',
                         {'chat_id': chat_id, 'text': text,
                          'disable_web_page_preview': True})
    if ok:
        log('Telegram 通知: 发送成功')
        return True, '成功（图片失败后已降级文本）' if photo_failed else '成功（文本）'
    log(f'Telegram 通知: 发送失败: {_redact(why, cfg)}')
    return False, f'失败：{_redact(why, cfg)}'


def _telegram_send_photo(token: str, chat_id: str, caption: str,
                         image_path: str) -> tuple[bool, str]:
    """multipart 上传图片 + caption（标准库手工拼包，不依赖 requests）。"""
    boundary = '----QQPetCopilot' + uuid.uuid4().hex
    try:
        with open(image_path, 'rb') as f:
            data = f.read()
    except OSError as e:
        return False, f'读取截图失败 {e}'
    ctype = mimetypes.guess_type(image_path)[0] or 'image/png'
    name = os.path.basename(image_path)
    # caption 上限 1024 字符，超了截断（避免整条发送失败）
    cap = caption if len(caption) <= 1000 else caption[:1000] + '…'
    parts = []
    for k, v in (('chat_id', chat_id), ('caption', cap)):
        parts.append(f'--{boundary}\r\n'.encode())
        parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        parts.append(v.encode('utf-8') + b'\r\n')
    parts.append(f'--{boundary}\r\n'.encode())
    parts.append(
        f'Content-Disposition: form-data; name="photo"; filename="{name}"\r\n'.encode())
    parts.append(f'Content-Type: {ctype}\r\n\r\n'.encode())
    parts.append(data)
    parts.append(f'\r\n--{boundary}--\r\n'.encode())
    req = Request(f'https://api.telegram.org/bot{token}/sendPhoto',
                  data=b''.join(parts), method='POST',
                  headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', errors='replace')[:200]
        except Exception:
            pass
        return False, f'HTTP {e.code} {detail}'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
    if isinstance(payload, dict) and payload.get('ok'):
        return True, 'ok'
    return False, str((payload or {}).get('description') or 'ok=false')


def test_notify(target: str = 'all') -> dict:
    """发送一条测试通知（设置页「测试通知」按钮用），返回每个渠道的结果。

    target: all / feishu / telegram / onepush / desktop。
    """
    if target not in ('all', 'feishu', 'telegram', 'onepush', 'desktop'):
        return {'ok': False, 'msg': '不支持的测试渠道', 'results': {}}
    cfg = _notify_cfg()
    if cfg is None:
        return {'ok': False, 'msg': '读取配置失败且无可用缓存', 'results': {}}
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    text = f'这是一条测试通知（{stamp}）。收到说明配置正确。'
    return send_detailed('[测试] QQ宠物助手', text, cfg=cfg, target=target)



def _send_windows_toast(reason: str, image_path: str | None = None, *,
                        title: str = TITLE) -> bool:
    """发送 Windows Toast 通知。"""
    if not sys.platform.startswith('win'):
        log('告警通知: 非 Windows 平台，跳过 Toast')
        return False
    try:
        from winotify import Notification
    except ImportError:
        log('告警通知: 未安装 winotify，跳过 Windows Toast')
        return False
    except Exception as e:
        log(f'告警通知: winotify 导入失败: {e}，跳过 Windows Toast')
        return False
    icon = str(image_path or '')
    if not icon or not os.path.exists(icon):
        icon = ''
    try:
        toast = Notification(
            app_id='QQPetCopilot', title=title, msg=str(reason), duration='long',
            **({'icon': icon} if icon else {}),
        )
        toast.show()
        log('告警通知: Windows Toast 推送成功')
        return True
    except Exception as e:
        log(f'告警通知: Windows Toast 发送失败: {e}')
        return False


def _send_mac_toast(reason: str, *, title: str = TITLE) -> bool:
    """发送 macOS 桌面通知（osascript display notification）。"""
    if sys.platform != 'darwin':
        return False
    try:
        def escape(value):
            return str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ').replace('\r', ' ')
        script = f'display notification "{escape(reason)}" with title "{escape(title)}"'
        proc = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0:
            log('告警通知: macOS 通知推送成功')
            return True
        log(f'告警通知: macOS 通知发送失败: {proc.stderr.strip()}')
        return False
    except Exception as e:
        log(f'告警通知: macOS 通知发送失败: {e}')
        return False


def _send_onepush(config_text: str, reason: str, image_path: str | None = None, *,
                  title: str = TITLE) -> bool:
    """发送 OnePush 通知。config_text 为单行 YAML（flow 格式），必须含 provider。"""
    import yaml

    try:
        cfg = yaml.safe_load(config_text.strip())
    except yaml.YAMLError as e:
        log(f'告警通知: OnePush 配置 YAML 解析失败: {e}')
        return False
    if not isinstance(cfg, dict):
        log('告警通知: OnePush 配置不是字典，跳过推送')
        return False
    cfg = dict(cfg)
    provider = str(cfg.pop('provider', '') or '').strip()
    if not provider:
        log('告警通知: OnePush 未配置 provider，跳过推送')
        return False
    try:
        from onepush import get_notifier
        from onepush.providers.custom import Custom
    except ImportError:
        log('告警通知: 未安装 onepush，跳过 OnePush 推送')
        return False
    except Exception as e:
        log(f'告警通知: onepush 导入失败: {e}，跳过 OnePush 推送')
        return False
    try:
        notifier = get_notifier(provider)
        payload: dict = dict(cfg)
        payload['title'] = title
        payload['content'] = str(reason)
        if image_path and os.path.exists(image_path):
            payload['image_path'] = image_path
        if isinstance(notifier, Custom):
            # 自定义 webhook：默认 JSON POST，title/content 固定塞进 data
            if str(payload.get('method', 'post')).lower() == 'post':
                payload['datatype'] = 'json'
            data = payload.get('data')
            if not isinstance(data, dict):
                data = {}
            data['title'] = payload['title']
            data['content'] = payload['content']
            payload['data'] = data
        # OnePush's HTTP transport swallows connection errors and returns None.
        # Add a timeout to its per-instance request function without changing SMTP.
        request = notifier.request
        notifier.request = lambda *args, **kwargs: request(
            *args, **{'timeout': HTTP_TIMEOUT, **kwargs})
        response = notifier.notify(**payload)
        if response is None:
            log(f'告警通知: OnePush 无响应（{provider}）')
            return False
        if provider.lower() == 'smtp':
            ok = isinstance(response, dict) and not response
            log(f'告警通知: OnePush SMTP {"成功" if ok else "存在拒收地址"}')
            return ok
        status_code = int(getattr(response, 'status_code', 0) or 0)
        if not 200 <= status_code < 300:
            log(f'告警通知: OnePush 推送失败，状态码={status_code}')
            return False
        try:
            body = response.json()
        except (ValueError, AttributeError):
            body = {}
        if isinstance(body, dict):
            code = body.get('code')
            valid_codes = (200,) if provider.lower() in ('bark', 'pushplus') else (0, 200)
            if (code is not None and code not in valid_codes) or body.get('ok') is False:
                log(f'告警通知: OnePush 业务返回失败（{provider}）')
                return False
        log(f'告警通知: OnePush 推送成功（{provider}）')
        return True
    except Exception as e:
        detail = _redact(str(e).strip() or repr(e), NotifyConfig(onepush_config=config_text))
        log(f'告警通知: OnePush 推送失败（{provider}）: {type(e).__name__}: {detail}')
        return False
