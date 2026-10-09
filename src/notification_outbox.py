"""Persistent notification retries, separate from gameplay progress."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .config import NotifyConfig, PROJECT_ROOT
from .jsonio import write_json
from .notify import send_detailed

OUTBOX_FILE = PROJECT_ROOT / 'runs' / 'notification_outbox.json'
_LOCK = threading.RLock()
_WORKER: threading.Thread | None = None
RETRY_SECONDS = (60, 300, 900, 1800)


def _load() -> dict:
    try:
        data = json.loads(OUTBOX_FILE.read_text('utf-8'))
    except FileNotFoundError:
        return {'events': {}}
    if not isinstance(data, dict) or not isinstance(data.get('events'), dict):
        raise ValueError('Invalid notification outbox')
    return data


def _save(data: dict) -> None:
    write_json(OUTBOX_FILE, data, indent=1)
    OUTBOX_FILE.chmod(0o600)


def delivered_at(key: str) -> str | None:
    with _LOCK:
        return (_load()['events'].get(key) or {}).get('delivered_at')


def delivery_stamps() -> dict[str, str]:
    with _LOCK:
        return {key: event['delivered_at'] for key, event in _load()['events'].items()
                if event.get('delivered_at')}


def contains(key: str) -> bool:
    with _LOCK:
        return key in _load()['events']


def enqueue(key: str, kind: str, title: str, content: str,
            image_path: str | None = None, *, expires_at: float | None = None,
            delivered: str | None = None) -> None:
    with _LOCK:
        data = _load()
        if key in data['events']:
            return
        data['events'][key] = {
            'kind': kind, 'title': title, 'content': content, 'image_path': image_path,
            'expires_at': expires_at, 'attempts': 0, 'next_attempt': 0,
            'created_at': time.time(), 'delivered_at': delivered,
        }
        _save(data)


def _enabled(event: dict, cfg: NotifyConfig) -> bool:
    if event['kind'] == 'quota' and not (cfg.quota_done and cfg.event_notify):
        return False
    # 日内收益汇总（收工总账 / 冒险日报）：与配额达成同属"完成类告知"，
    # 一起受 quota_done + event_notify 两个开关控制
    if event['kind'] == 'daily' and not (cfg.quota_done and cfg.event_notify):
        return False
    if event['kind'] == 'career' and not cfg.career_notify:
        return False
    return bool(cfg.feishu_enabled or cfg.telegram_enabled
                or str(cfg.onepush_config).strip() or cfg.win_toast)


def flush(cfg: NotifyConfig, *, now: float | None = None) -> dict[str, str]:
    """Retry due events once; retain failures across scheduler restarts."""
    now = time.time() if now is None else now
    with _LOCK:
        keys = list(_load()['events'])
    for key in keys:
        with _LOCK:
            data = _load()
            event = data['events'][key]
            if event.get('delivered_at'):
                continue
            expiry = event.get('expires_at')
            if expiry is not None and now >= expiry:
                continue
            if now < event.get('next_attempt', 0) or not _enabled(event, cfg):
                continue
            # Persist a lease first: an unexpected crash must not cause a tight resend loop.
            attempts = int(event.get('attempts', 0))
            event['next_attempt'] = now + RETRY_SECONDS[min(attempts, len(RETRY_SECONDS) - 1)]
            event['attempts'] = attempts + 1
            _save(data)
        # Do not hold the file lock while waiting for the network.
        try:
            result = send_detailed(event['title'], event['content'], event.get('image_path'),
                                   cfg=cfg, event_key=key)
        except Exception as e:
            result = {'ok': False, 'results': {}, 'msg': type(e).__name__}
        with _LOCK:
            data = _load()
            event = data['events'][key]
            event['results'] = result.get('results', {})
            if result.get('ok'):
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))
                event['delivered_at'] = stamp
            _save(data)
    return delivery_stamps()


def start_flush(cfg: NotifyConfig) -> None:
    """One background sender; the scheduler never waits on HTTP or desktop IPC."""
    global _WORKER
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        def run():
            try:
                flush(cfg)
            except Exception:
                # A broken outbox is reported by the scheduler's next read, not overwritten.
                pass
        _WORKER = threading.Thread(target=run, name='notification-sender', daemon=True)
        _WORKER.start()
