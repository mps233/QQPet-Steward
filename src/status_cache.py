"""宠物状态缓存：体力/清洁/心情/金币/香皂/饼干，供仪表盘总览页状态环显示。

runs/status_cache.json 单条目（曾经按账号名称组织兼容多账号，但账号名靠
状态面板 OCR 识别不稳定——时钟会被误识别成账号，状态条出现多行——已取消
多账号区分，固定写 default 条目）：
{
  "accounts": {"default": {"pet_name": ..., "energy": ..., "clean": ...,
               "mood": ..., "coins": ..., "soap": ..., "biscuit": ...,
               "updated": "..."}}
}

写入点：
- care.check_and_care：状态面板 OCR 后写 体力/清洁/心情/宠物名称
- care 喂食/洗澡结束：OCR 控件附近区域写 香皂/饼干 库存（同时刷新刚喂完/洗完的数值）
- runner.read_main_coins：主页金币 OCR 后写 金币
消费方是仪表盘（`dashboard.py` 直接读这份 JSON）。缓存只是展示用途，
读写失败都不影响调度。
"""
from __future__ import annotations

import json
import time

from .config import PROJECT_ROOT
from .jsonio import write_json
from .progress import log

STATUS_CACHE_FILE = PROJECT_ROOT / 'runs' / 'status_cache.json'

DEFAULT_ACCOUNT = 'default'


def _load() -> dict:
    try:
        data = json.loads(STATUS_CACHE_FILE.read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('accounts'), dict):
            return data
    except (OSError, ValueError):
        pass
    return {'accounts': {}}


def _save(data: dict) -> None:
    try:
        # 原子写（见 docs/review/07-进程间契约.md B 节）
        write_json(STATUS_CACHE_FILE, data, indent=2)
    except OSError as e:
        log(f'状态缓存写入失败: {e}')


def clear_status_fields(*keys: str) -> None:
    """删除缓存条目里的指定字段（GUI 显示回 '-'）。

    一键护理后不读状态面板，体力/清洁/饼干/香皂的缓存值不再可信，调用方清空。
    """
    data = _load()
    entry = data['accounts'].get(DEFAULT_ACCOUNT)
    if not entry:
        return
    for key in keys:
        entry.pop(key, None)
    _save(data)


def update_status(account: str | None = None, **fields) -> None:
    """合并写入状态字段（值为 None 的跳过）。

    account 参数保留仅为兼容旧调用（care 曾传状态面板 OCR 的账号名称），
    已取消多账号区分，一律写 default 条目；老缓存文件里的多账号条目
    在第一次写入时丢弃（自愈，避免状态条一直显示残留的旧账号行）。
    """
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    data = _load()
    entry = data['accounts'].get(DEFAULT_ACCOUNT, {})
    entry.update(fields)
    entry['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    data['accounts'] = {DEFAULT_ACCOUNT: entry}
    _save(data)
