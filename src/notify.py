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
import html
import json
import mimetypes
import os
import re
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
# Telegram 单条消息正文上限是 4096 字符，超了整条被 API 拒收（不是截断）。
# 留一点余量给 title 与省略号：宁可末尾少显示几行，也不能整条丢。
TELEGRAM_TEXT_LIMIT = 4000
HISTORY_FILE = PROJECT_ROOT / 'runs' / 'notifications.jsonl'
_HISTORY_LOCK = threading.Lock()


def _balance_pre(text: str) -> str:
    """补齐被截断的 `<pre>` 收尾标签（标签不配平会被 TG 直接 400 拒收）。"""
    if text.count('<pre>') > text.count('</pre>'):
        text += '</pre>'
    return text


def _fit_telegram(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> tuple[str, bool]:
    """把文本压进 Telegram 的长度上限，返回 (文本, 是否被截断)。

    超限在 Telegram 是**整条拒收**而不是自动截断，所以必须自己砍。
    `<pre>` 被砍剩半截会让标签不配平 → 同样被拒，所以顺手补上收尾标签。
    """
    if len(text) <= limit:
        return text, False
    head = _balance_pre(text[:limit].rstrip())
    return head + '\n…（内容过长已截断，完整内容见仪表盘）', True


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


# 通知正文允许带**受限 HTML**：Telegram 的富消息靠它渲染原生表格（`<table>`）。
# 其它渠道不认这些标签，统一剥成纯文本再发，免得用户看到一堆 `<table>` 字样。
_TAG_RE = re.compile(
    r'</?(?:pre|code|b|strong|i|em|u|s|del|blockquote|table|tr|th|td|ul|ol|li|br)'
    r'\b[^>]*>', re.IGNORECASE)
# 富消息专用标签：出现这些说明必须走 sendRichMessage（parse_mode=HTML 会 400）
_RICH_ONLY_RE = re.compile(r'</?(?:table|tr|th|td)\b', re.IGNORECASE)
# Telegram HTML parse mode 支持的实体：& < > 与数字实体
_ENTITY_RE = re.compile(r'&(?:amp|lt|gt|quot|#\d{1,6});', re.IGNORECASE)


def _table_to_text(block: str) -> str:
    """把一段 `<table>…</table>` 转成一行一条的纯文本（列间制表符、无空行）。

    **只把列边界换成制表符，单元格内部的空格要原样保留** —— 曾经在这里做了
    `[ \\t]+ → \\t` 的全行归一，结果 `491 次` 被拆成 `491\\t次`、`+5465` 之类
    看着像两列（踩过）。列边界靠 `</td><td>` 识别，本来就精确。
    """
    block = re.sub(r'</t[dh]>\s*(?=<t[dh])', '\t', block, flags=re.I)
    block = re.sub(r'</t[dh]>', '', block, flags=re.I)
    block = re.sub(r'</tr>', '\n', block, flags=re.I)
    block = re.sub(r'</?table[^>]*>', '\n', block, flags=re.I)
    block = re.sub(r'</?t[dh][^>]*>', '', block, flags=re.I)
    lines = [ln.strip() for ln in block.splitlines()]
    lines = [re.sub(r'\t{2,}', '\t', ln) for ln in lines]
    return '\n'.join(ln for ln in lines if ln)


def _join_title(title: str, content: str) -> str:
    """标题 + 正文拼一条消息；**空标题不留空行**。

    标题可以为空（如冒险日报已改为不带 `[QQ宠物·冒险日报]` 前缀，靠正文首行
    自报家门）。若照旧 `f'{title}\\n{content}'`，空标题会在最前面顶出一个空行。
    """
    title = str(title or '').strip()
    return f'{title}\n{content}' if title else str(content)


def _escape_plain(content: str) -> str:
    """纯文本渠道：把富消息 HTML 剥成可读纯文本。

    **表格单独处理**：表体剥成一行一条（列间制表符、行间不留空行），其余正文按
    常规保留段落空行 —— 一视同仁地压空行会把标题与正文之间该有的空行也吃掉
    （踩过：整条通知挤成一坨）。
    """
    text = str(content)
    # 先把表格区域抽出来逐块转换，避免表格与正文的换行规则互相干扰
    text = re.sub(r'<table\b[^>]*>.*?</table>', lambda m: _table_to_text(m.group(0)),
                  text, flags=re.I | re.S)
    text = re.sub(r'</(?:pre|blockquote|ul|ol|li)>', '\n', text, flags=re.I)
    text = re.sub(r'<br\b[^>]*/?>', '\n', text, flags=re.I)
    text = _TAG_RE.sub('', text)
    text = _ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip('\n')


def send_detailed(title: str, content: str, image_path: str | None = None,
                  cfg: NotifyConfig | None = None, *,
                  target: str = 'all', event_key: str | None = None) -> dict:
    """Return channel results. Desktop success never masks a failed remote push.

    正文可含受限 HTML：**含 `<table>` 的正文走 Telegram 富消息**（`sendRichMessage`，
    渲染原生表格；`parse_mode=HTML` 不支持 table 会直接 400），其余渠道剥成纯文本。
    """
    cfg = cfg or _notify_cfg()
    if cfg is None:
        return {'ok': False, 'msg': '读取配置失败且无可用缓存', 'results': {}}
    content = _redact(content, cfg)
    plain = _escape_plain(content) if _TAG_RE.search(content) else content
    rich = bool(_RICH_ONLY_RE.search(content))
    results: dict[str, str] = {}
    successes: set[str] = set()
    remote_enabled: set[str] = set()

    def deliver_telegram():
        """含表格先试富消息；富消息不可用（旧服务器/权限）就回落普通消息。

        回落是**保住通知**的关键：表格只是排版，发不出去才是真丢东西。
        """
        if rich:
            ok, why = _send_telegram_rich_result(cfg, title, content)
            if ok:
                return ok, why
            log(f'Telegram 通知: 富消息发送失败（{why}），改用普通消息')
        return _send_telegram_result(cfg, title, plain)

    channels = (
        ('飞书', 'feishu', cfg.feishu_enabled,
         bool(str(cfg.feishu_webhook).strip()),
         lambda: _send_feishu_result(cfg, title, plain, image_path)),
        ('Telegram', 'telegram', cfg.telegram_enabled,
         bool(str(cfg.telegram_token).strip() and str(cfg.telegram_chat_id).strip()),
         deliver_telegram),
        ('OnePush', 'onepush', bool(str(cfg.onepush_config).strip()), True,
         lambda: _send_onepush(str(cfg.onepush_config), plain, image_path, title=title)),
        ('桌面', 'desktop', cfg.win_toast, True,
         lambda: (_send_mac_toast(plain, title=title) if sys.platform == 'darwin'
                  else _send_windows_toast(plain, image_path, title=title))),
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
                # 历史存**剥标签的纯文本**：仪表盘「最近发送的通知」是 pre-wrap 渲染，
                # 存原文会让用户看到一堆 <pre>；表格靠空格对齐，纯文本照样能看
                'content': _redact(plain, cfg),
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


# 网络类失败（TLS 握手超时、连接重置等）的重试次数与间隔。
# **只重试网络错误**：HTTP 4xx/5xx 是对方明确拒绝（确定性失败），重试纯浪费。
NET_RETRY = 2
NET_RETRY_DELAY = 1.0


def _post_json_retry(url: str, payload: dict, *,
                     attempts: int = NET_RETRY) -> tuple[bool, str]:
    """`_post_json` + 网络错误重试。

    为什么需要它：远端偶尔抖动（实测 `_ssl.c:1015 handshake timed out`）会让**首次**
    尝试就失败，而调用方的降级路径（富消息 → 纯文本）是**不可逆**的 —— 一次抖动就
    永久降级，用户看到的表格突然变成一行一条（2026-10-15 23:59 冒险日报事故）。
    宁可多等一两秒，也别白丢排版。
    """
    ok, why = _post_json(url, payload)
    for _ in range(max(0, attempts)):
        if ok or not why.startswith('网络错误'):
            return ok, why
        time.sleep(NET_RETRY_DELAY)
        ok, why = _post_json(url, payload)
    return ok, why


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

    text = _join_title(title, content)
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

# Telegram Bot API 10.1（2026-06）起的 Rich Messages：sendRichMessage 能渲染
# **原生表格**（<table bordered striped>）、32k 字符上限。旧的 sendMessage +
# parse_mode=HTML 明确不支持 <table>（塞进去 400 拒收），所以表格只能走这条路。
RICH_LIMIT = 32000


def _telegram_send_rich(token: str, chat_id: str, html: str) -> tuple[bool, str]:
    """富消息（原生表格）。走重试版 —— 一次网络抖动不该让表格降级成纯文本。"""
    return _post_json_retry(
        f'https://api.telegram.org/bot{token}/sendRichMessage',
        {'chat_id': chat_id, 'rich_message': {'html': html}})


def _fit_rich(html: str, limit: int = RICH_LIMIT) -> str:
    """把富消息压进长度上限，并保证标签配平。

    超限在 Telegram 是**整条拒收**；而按字符硬切会把 `<table>` 切成半截、
    `<td>` 落单，同样拒收。所以：先切到上限，再把未闭合的单元格/行/表格补齐，
    最后仍放不下就退到**丢弃最后一张未闭合的表**（宁可少一张表也不能整条发不出）。
    """
    if len(html) <= limit:
        return html
    cut = html[:limit]
    # 丢掉被切在半途的最后一个标签（形如 `<t...` 的残片，补齐也没法补）
    cut = re.sub(r'<[^<>]*$', '', cut)
    # 补齐未闭合的嵌套标签（由内到外回填，保证配平）
    for open_name, close_tag in (('td', '</td>'), ('th', '</th>'),
                                 ('tr', '</tr>'), ('table', '</table>')):
        if cut.count(f'<{open_name}') > cut.count(close_tag):
            if open_name == 'table' and len(cut) + len(close_tag) > limit:
                # 连收尾标签都放不下：整段丢掉最后这张表，别硬凑
                last = cut.rfind('<table')
                cut = (cut[:last] if last >= 0 else cut).rstrip()
                break
            cut += close_tag
    return cut + '\n…（内容过长已截断，完整内容见仪表盘）'


def _send_telegram_rich_result(cfg: NotifyConfig, title: str, content: str,
                               image_path: str | None = None) -> tuple[bool, str]:
    """用 Rich Message 发正文（表格走这条）。失败返回失败原因，由调用方回落。"""
    token = str(cfg.telegram_token).strip()
    chat_id = str(cfg.telegram_chat_id).strip()
    if not token or not chat_id:
        return False, '未填 token 或 chat_id'
    # 标题为空时不发 `<b></b>`（那会渲染出一行空粗体，还占掉一点行高）
    title = str(title or '').strip()
    head = f'<b>{_escape_rich(title)}</b>\n' if title else ''
    html = _fit_rich(f'{head}{content}')
    ok, why = _telegram_send_rich(token, chat_id, html)
    if ok:
        log('Telegram 通知: 发送成功（富消息·表格）')
        return True, '成功（富消息）'
    return False, _redact(why, cfg)


def _escape_rich(text) -> str:
    return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _send_telegram_result(cfg: NotifyConfig, title: str, content: str,
                          image_path: str | None = None) -> tuple[bool, str]:
    """发送 Telegram 消息（有截图用 sendPhoto，否则 sendMessage）。

    正文若含受限 HTML（等宽表格），走 `parse_mode=HTML`；否则仍是纯文本，
    这样老通知的排版不变。**HTML 解析失败必须能自愈**：TG 对非法实体直接
    400 拒收整条消息，所以失败后用剥标签的纯文本再发一次，宁可丢表格也不丢通知。
    """
    token = str(cfg.telegram_token).strip()
    chat_id = str(cfg.telegram_chat_id).strip()
    if not token or not chat_id:
        log('Telegram 通知: token 或 chat_id 未填，跳过')
        return False, '未填 token 或 chat_id'
    text, clipped = _fit_telegram(_join_title(title, content))
    # 截断可能吃掉末尾的标签，富文本判定要按**实际发出的文本**来
    rich = bool(_TAG_RE.search(text))
    if clipped:
        log(f'Telegram 通知: 正文超过 {TELEGRAM_TEXT_LIMIT} 字符，已截断后发送')
    photo_failed = False
    if image_path and os.path.exists(image_path):
        ok, why = _telegram_send_photo(token, chat_id, text, image_path, html=rich)
        if ok:
            log('Telegram 通知: 发送成功（图片）')
            return True, '成功（图片）'
        photo_failed = True
        log(f'Telegram 通知: 图片发送失败（{_redact(why, cfg)}），降级为文本')
    base = f'https://api.telegram.org/bot{token}'
    ok, why = _telegram_send_message(token, chat_id, text, html=rich)
    if not ok and rich:
        # HTML 被拒（非法实体/标签不被支持）→ 剥标签重发纯文本
        log(f'Telegram 通知: HTML 正文被拒（{_redact(why, cfg)}），改用纯文本重发')
        plain, _ = _fit_telegram(_join_title(title, _escape_plain(content)))
        ok, why = _telegram_send_message(token, chat_id, plain, html=False)
        if ok:
            return True, ('成功（图片失败后已降级文本）' if photo_failed
                          else '成功（文本·HTML 被拒后降级）')
    if ok:
        log('Telegram 通知: 发送成功')
        return True, '成功（图片失败后已降级文本）' if photo_failed else '成功（文本）'
    log(f'Telegram 通知: 发送失败: {_redact(why, cfg)}')
    return False, f'失败：{_redact(why, cfg)}'


def _telegram_send_message(token: str, chat_id: str, text: str, *,
                           html: bool = False) -> tuple[bool, str]:
    """sendMessage；html=True 时用 parse_mode=HTML（等宽 <pre> 表格靠它渲染）。"""
    payload = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
    if html:
        payload['parse_mode'] = 'HTML'
    return _post_json_retry(f'https://api.telegram.org/bot{token}/sendMessage', payload)


def _telegram_send_photo(token: str, chat_id: str, caption: str,
                         image_path: str, *, html: bool = False) -> tuple[bool, str]:
    """multipart 上传图片 + caption（标准库手工拼包，不依赖 requests）。

    caption 带 HTML 时同样要 parse_mode=HTML（字段排在 caption 之前，
    TG 解析 multipart 时才能把 caption 当富文本）。
    """
    boundary = '----QQPetCopilot' + uuid.uuid4().hex
    try:
        with open(image_path, 'rb') as f:
            data = f.read()
    except OSError as e:
        return False, f'读取截图失败 {e}'
    ctype = mimetypes.guess_type(image_path)[0] or 'image/png'
    name = os.path.basename(image_path)
    # caption 上限 1024 字符，超了截断（避免整条发送失败）。
    # 带 HTML 时截断同样可能砍断 <pre>，标签不配平会被 TG 400 拒收整张图，
    # 所以顺手把收尾标签补上（与 _fit_telegram 同一套做法）。
    cap = caption if len(caption) <= 1000 else caption[:1000] + '…'
    cap = _balance_pre(cap)
    parts = []
    fields = [('chat_id', chat_id)]
    if html:
        fields.append(('parse_mode', 'HTML'))
    fields.append(('caption', cap))
    for k, v in fields:
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
            app_id='QQPetCopilot', title=_fallback_title(title), msg=str(reason),
            duration='long', **({'icon': icon} if icon else {}),
        )
        toast.show()
        log('告警通知: Windows Toast 推送成功')
        return True
    except Exception as e:
        log(f'告警通知: Windows Toast 发送失败: {e}')
        return False


def _fallback_title(title: str) -> str:
    """桌面通知的标题兜底。

    桌面通知的标题是**系统级独立字段**（不像消息正文可以靠首行自报家门），
    空标题会让通知栏里那一条没有名字、分不清来源 —— 所以空的时候回落到
    通用名（冒险日报已改为不带标题前缀，就是这条路）。
    """
    return str(title or '').strip() or TITLE


def _send_mac_toast(reason: str, *, title: str = TITLE) -> bool:
    """发送 macOS 桌面通知（osascript display notification）。"""
    if sys.platform != 'darwin':
        return False
    title = _fallback_title(title)
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
        payload['title'] = _fallback_title(title)   # 空标题回落到通用名（同上）
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
