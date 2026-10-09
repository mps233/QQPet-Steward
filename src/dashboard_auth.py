"""Local pairing key and browser sessions for the LAN dashboard."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

COOKIE_NAME = 'qqpet_session'
#: 自定义访问码的最短长度。生成的是 43 位随机串；手填的一般是好记的词组，
#: 放宽到 8 位但配套了登录失败限流（见 dashboard.py 的 AUTH_FAIL_LIMIT），
#: 否则短码在局域网上可以被直接穷举。
MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 128


def validate_new_key(value) -> tuple[bool, str]:
    """校验用户自定义访问码，返回 (是否可用, 修正后的值或原因)。"""
    if not isinstance(value, str):
        return False, '访问码必须是文本'
    key = value.strip()
    if len(key) < MIN_KEY_LENGTH:
        return False, f'访问码至少 {MIN_KEY_LENGTH} 位'
    if len(key) > MAX_KEY_LENGTH:
        return False, f'访问码最长 {MAX_KEY_LENGTH} 位'
    if not key.isprintable() or any(ch.isspace() and ch not in ' ' for ch in key):
        return False, '访问码不能包含换行或控制字符'
    return True, key


def save_access_key(path: Path, key: str) -> None:
    """原子写入访问码文件（0600）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(key + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    path.chmod(0o600)
    # 标记"这是用户自己设的"（HTTP 设置页据此显示状态）
    custom_marker(path).write_text('custom\n', encoding='utf-8')


def custom_marker(key_path: Path) -> Path:
    return key_path.with_suffix('.custom')


def is_custom_key(key_path: Path) -> bool:
    """访问码是不是用户自己设的（而不是自动生成的长随机串）。

    以时间戳兜底：访问码文件比标记新，说明是删掉文件重启后重新生成的，
    此时清掉过期标记。
    """
    marker = custom_marker(key_path)
    try:
        if not marker.is_file():
            return False
        if key_path.is_file() and key_path.stat().st_mtime > marker.stat().st_mtime:
            marker.unlink()
            return False
        return True
    except OSError:
        return False


def load_access_key(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = path.read_text('utf-8').strip()
    else:
        key = secrets.token_urlsafe(32)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(key + '\n')
    if len(key) < MIN_KEY_LENGTH:
        raise ValueError(
            f'访问码文件里的口令不足 {MIN_KEY_LENGTH} 位；'
            f'删掉 {path} 重启仪表盘可重新生成，或在仪表盘 设置 → 访问权限 里重设')
    path.chmod(0o600)
    return key


def session_value(key: str) -> str:
    return hmac.new(key.encode(), b'qqpet-dashboard-browser', hashlib.sha256).hexdigest()


def authorized(headers, key: str | None) -> bool:
    if not key:
        return False
    try:
        bearer = headers.get('Authorization', '')
        if bearer.startswith('Bearer ') and hmac.compare_digest(bearer[7:].strip(), key):
            return True
        cookies = SimpleCookie()
        cookies.load(headers.get('Cookie', ''))
        cookie = cookies.get(COOKIE_NAME)
        return bool(cookie and hmac.compare_digest(cookie.value, session_value(key)))
    except (ValueError, TypeError):
        return False


def same_origin(headers) -> bool:
    origin = headers.get('Origin')
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
        return parsed.scheme in ('http', 'https') and parsed.netloc == headers.get('Host')
    except ValueError:
        return False


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QQ宠物 · 访问验证</title>
<style>
:root{--ink:#59442c;--paper:#fcf7ed;--line:#d8c9b1;--accent:#9d641e}
*{box-sizing:border-box}body{margin:0;min-height:100svh;display:grid;place-items:center;
padding:24px;color:var(--ink);background:radial-gradient(ellipse at 20% 5%,#eed8ac,transparent 55%),
var(--paper);font-family:"PingFang SC","Hiragino Sans GB",sans-serif}
main{width:min(100%,420px);border:1px solid var(--line);border-radius:24px;padding:32px;
background:#fffdf8;box-shadow:0 18px 60px #59442c12}
h1{font-family:Optima,"Songti SC",serif;font-size:28px;font-weight:500;margin:0 0 18px}
p{font-size:14px;line-height:1.8}label{display:block;font-size:13px;margin:24px 0 8px}
input,button{width:100%;min-height:46px;border-radius:12px;font:inherit}
input{border:1px solid var(--line);padding:10px 12px;background:#fff}
button{margin-top:14px;border:0;background:var(--accent);color:white;cursor:pointer}
input:focus-visible,button:focus-visible{outline:3px solid #c89c52;outline-offset:3px}
code{font-size:12px;overflow-wrap:anywhere}small{display:block;line-height:1.7;margin-top:22px}
</style><main><h1>QQ宠物 · 访问验证</h1>
<p>输入运行助手的电脑上保存的访问码。此浏览器验证后会自动记住，不需要填写通知密钥。</p>
<form action="/api/auth" method="post"><label for="key">仪表盘访问码</label>
<input id="key" name="key" type="password" autocomplete="current-password" required>
<button type="submit">验证并进入</button></form>
<small>访问码文件：<code>runs/dashboard_access.token</code>（嫌难记可在「设置 → 仪表盘访问码」里改）<br>
HTTP 仅用于可信内网；远程访问仍请使用私有网络或 HTTPS。</small>
</main></html>"""
