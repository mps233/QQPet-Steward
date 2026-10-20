"""Telegram 控制机器人：在 TG 里改设置、查状态、启停调度器。

**这是项目里唯一的"反向通道"**。`src/notify.py` 只做单向推送（程序 → 你），
这里做双向：仪表盘起一个后台线程长轮询 `getUpdates`，把消息解析成动作。

两条轨道，同一个执行器：

1. **确定性指令**（永远可用，零外部依赖、零成本）
   `/set 字段 值` `/get [字段]` `/status` `/log [N]`
   `/start` `/stop` `/pause` `/resume` `/help`
2. **自然语言**（`tgbot.llm_enabled` 打开且配好 OpenAI 兼容接口时）
   自由文本交给模型做 tool calling，工具集合与上面那批指令**完全同一套**
   （`list_settings` / `get_settings` / `set_setting` / `get_status` /
   `control_scheduler` / `get_logs`），所以模型再"自由发挥"也越不出确定性指令的边界。

**边界（刻意划死的三条）**

- **只认白名单 chat**：`tgbot.allowed_chats`，留空则只认 `notify.telegram_chat_id`。
  其它 chat 发来什么都不做、不回复（不泄露 bot 存在）。token 一旦泄露，这里是唯一闸门。
- **凭据类字段不能通过 TG 修改**（见 `SECRET_FIELDS`）：bot token、模型 key、
  通知 webhook/secret 这些一律不在 TG 的可改范围内 —— 聊天记录会永久留存，
  而且"能改控制通道自身凭据"等于把一次泄露升级成永久接管。这些请用仪表盘设置页
  或直接改 config.yaml。
- **不碰设备、不碰场景**：这里只改配置、看状态、开关调度器进程。游戏操作仍归调度器。

模块与 dashboard 之间靠 `Bridge` 依赖注入解耦（`dashboard.py` 是脚本不是包，
反向 import 会循环），所以这个文件可以脱离 dashboard 单独测试 —— 见 `tests/test_tg_bot.py`。
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import PROJECT_ROOT, load_config

#: getUpdates 的游标（重启后不重放旧消息）
STATE_FILE = PROJECT_ROOT / 'runs' / 'tgbot_state.json'

#: 长轮询挂起时长；HTTP 超时必须比它大，否则每轮都是"超时当成错误"
POLL_TIMEOUT = 25
HTTP_TIMEOUT = POLL_TIMEOUT + 15
#: 普通 API（sendMessage 等）
API_TIMEOUT = 20

#: Telegram 单条正文上限 4096；留余量避免边界上被整条拒收
MAX_TEXT = 3900

#: **不允许通过 TG 修改的字段**（理由见模块 docstring 的"边界"一节）：
#: 凭据本身 + 通知渠道的 webhook/secret。另外所有 `tgbot_*` 前缀字段一律挡掉
#: （见 `blocked_reason`）—— 用控制通道改控制通道自己的配置，等于给一次泄露
#: 配一把永久钥匙。
SECRET_FIELDS = frozenset({
    'notify_telegram_token', 'notify_feishu_webhook',
    'notify_feishu_secret', 'notify_feishu_access_token',
})


def blocked_reason(key: str) -> str:
    """该字段是否禁止通过 TG 修改；允许则返回空串。"""
    if key.startswith('tgbot_'):
        return '这是控制机器人自己的配置，只能去仪表盘设置页改（免得把控制通道改坏）'
    if key in SECRET_FIELDS:
        return '这是凭据类字段，聊天记录会永久留存，请到仪表盘设置页改'
    if key.endswith('_set'):
        return '这是「是否已配置」的状态位，不能直接改'
    return ''

#: 枚举字段的可选值。**只用于给模型/用户提示**，真正的校验仍在
#: `src/settings.py` 的 `validate_field`（两边不重复实现规则，避免漂移）。
OPTIONS: dict[str, list[str]] = {
    'school_attribute': ['力量', '智力', '魅力', '夏令营'],
    # 档位是"短课/长课"（按课程轮播卡位选），不是分钟数：各学园时长不同
    # （进修学院实测短课 45 分钟 / 长课 135 分钟）。旧值 10/30 分钟仍能写，
    # 但会被 course_kind() 归成长课（除 10分钟），提示里不再给这两个假选项。
    'school_duration': ['短课', '长课'],
    'work_duration': ['10分钟', '45分钟', '2小时'],
    'care_method': ['ocr检测', '一键护理'],
    'friend_care_method': ['ocr检测', '一键护理'],
    'adventure_type': ['附近走走', '诗和远方'],
    'hire_wait_action': ['不做任何事情', '去冒险', '继续打工'],
    'employed_action': ['等到25/75（小于45min）', '等到25/75', '立刻召回',
                        '让利雇主（不召回）'],
}

#: 字段的中文名（给 /help、/get、以及模型的 system prompt 用）。
#: 没登记的新字段照样能读写（白名单来自 dashboard 的 editable_snapshot），只是没有中文名。
LABELS: dict[str, str] = {
    'school_enabled': '启用学习',
    'school_attribute': '学习科目',
    'school_duration': '课时档位',
    'school_times': '每天学习次数',
    'work_enabled': '启用打工',
    'work_location': '打工地点',
    'work_duration': '打工时长',
    'hire_name': '优先雇佣',
    'hire_wait': '等TA空闲',
    'hire_wait_action': '等他空闲时',
    'hire_daily_hours': '每人每天被雇上限',
    'claim_ad_bonus': '结算领补贴',
    'care_enabled': '启用护理',
    'care_energy': '体力阈值',
    'care_clean': '清洁阈值',
    'care_method': '护理方式',
    'care_exchange': '补货数量',
    'care_interval': '护理检查间隔',
    'friend_care_enabled': '启用好友护理',
    'friend_care_name': '好友护理对象',
    'friend_care_range': '好友护理时间段',
    'friend_care_interval': '好友护理间隔',
    'friend_care_method': '好友护理方式',
    'visit_enabled': '启用踩踩',
    'visit_times': '踩踩次数/天',
    'pk_enabled': '启用PK',
    'pk_times': 'PK次数/天',
    'pk_only': '只打',
    'pk_skip': '跳过',
    'pk_helper': '打手',
    'pk_helper_fallback': '打手兜底',
    'pk_max_level': '等级上限',
    'adventure_enabled': '启用冒险',
    'adventure_times': '冒险次数/天',
    'adventure_type': '冒险类型',
    'employed_enabled': '被雇佣托管',
    'employed_action': '被雇佣处理方式',
    'employed_interval': '被雇佣检查间隔',
    'gift_bag_enabled': '启用福袋',
    'gift_bag_range': '福袋时间段',
    'gift_bag_interval': '福袋扫描间隔',
    'career_watch': '隐藏职业解锁监控',
    'career_stop_study': '解锁后自动停学',
    'career_interval': '职业兜底检查间隔',
    'coin_threshold': '金币阈值',
    'stop_total_hours': '合计满则停止',
    'daily_hour_limit': '停学习点',
    'work_stop_hours': '停打工点',
    'study_quota_hours': '今日学习配额',
    'work_quota_hours': '今日打工配额',
    'main_order': '主任务优先级',
    'task_order': '任务队列顺序',
    'adb_path': 'adb 路径',
    'adb_serial': '设备序列号',
}

#: 布尔值的口语写法
_TRUE = {'1', 'true', 'yes', 'on', 'y', 't', '开', '启用', '打开', '是', '真'}
_FALSE = {'0', 'false', 'no', 'off', 'n', 'f', '关', '关闭', '禁用', '停', '否', '假'}

HELP_TEXT = """QQ宠物托管 · 控制机器人

【改设置】
/set 字段 值        例：/set school_duration 短课
/get                列出全部可改字段与当前值
/get 字段           只看一个，例：/get coin_threshold

【看状态】
/status             调度器、队列、今日时长、属性、宠物状态
/attrs              只看属性与宠物状态（力量/智力/魅力、体力/清洁/心情、金币）
/log 50             最近 50 行日志

【调度器】
/run    启动调度器
/halt   停止调度器（会等当前任务安全收尾）
/pause  让路（暂停调度，不中断任务；5 分钟无操作自动恢复）
/resume 交还控制

【其它】
/help               本说明

直接说人话也行（如「把学习改成 30 分钟」「今天先别打工了」），
前提是设置页里开了"自然语言"，且配好了模型接口。

凭据类（bot token、模型 key、通知 webhook）只能去仪表盘设置页改。"""


# ---------------------------------------------------------------- 依赖注入


@dataclass
class Bridge:
    """dashboard 提供给机器人的能力集合（全部由 dashboard 注入，模块本身不 import 它）。

    这样 `src/tg_bot.py` 可以脱离 dashboard 单独测试：
    `tests/test_tg_bot.py` 用一个假 Bridge 就能跑完整条指令链路。
    """
    #: () -> dict，可改字段的当前值（dashboard.editable_snapshot）
    settings_snapshot: Callable[[], dict]
    #: (updates: dict) -> dict，写配置（dashboard.apply_settings）
    apply_settings: Callable[[dict], dict]
    #: () -> dict，调度器状态（dashboard.scheduler_info）
    scheduler_info: Callable[[], dict]
    #: (action: str) -> dict，start / stop / pause / resume
    scheduler_action: Callable[[str], dict]
    #: () -> dict，总览摘要
    status: Callable[[], dict]
    #: (n: int) -> list[str]，日志尾部
    logs: Callable[[int], list[str]]
    #: 写日志（dashboard 传 audit）
    log: Callable[[str], None] = print


# ---------------------------------------------------------------- HTTP


class _ApiError(Exception):
    """Telegram API 返回 ok=false。"""

    def __init__(self, description: str, code: int = 0, retry_after: float = 0):
        super().__init__(description)
        self.description = description
        self.code = code
        self.retry_after = retry_after


def _post(url: str, payload: dict, timeout: float = API_TIMEOUT) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json; charset=utf-8'})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
    except HTTPError as e:
        # Telegram 的错误体是 JSON：{"ok":false,"description":"...","parameters":{...}}
        detail, retry_after, code = '', 0.0, e.code
        try:
            parsed = json.loads(e.read().decode('utf-8', errors='replace') or '{}')
            detail = str(parsed.get('description') or '')
            retry_after = float((parsed.get('parameters') or {}).get('retry_after') or 0)
        except Exception:
            pass
        raise _ApiError(detail or f'HTTP {e.code}', code, retry_after) from e
    data = json.loads(raw or '{}')
    if not data.get('ok'):
        raise _ApiError(str(data.get('description') or 'ok=false'))
    return data


def _api_base(token: str) -> str:
    return f'https://api.telegram.org/bot{token}'


def send_message(token: str, chat_id: str, text: str) -> None:
    """发一条纯文本（不设 parse_mode：避免正文里的 `<` `&` 被当成实体而整条拒收）。"""
    text = str(text or '').strip() or '(空)'
    for chunk in _split_text(text):
        _post(f'{_api_base(token)}/sendMessage',
              {'chat_id': chat_id, 'text': chunk,
               'disable_web_page_preview': True})


def _split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """按 Telegram 上限切片，**尽量不断在行中间**。"""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ''
    for line in text.split('\n'):
        # 单行本身就超限：只能硬切
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ''
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f'{cur}\n{line}' if cur else line
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------- 字段与校验


def writable_fields(snapshot: dict) -> dict[str, Any]:
    """从快照里挑出**允许 TG 改**的字段。

    白名单不硬编码：`editable_snapshot()` 的键就是 dashboard 承认的全部可改字段，
    dashboard 加了新字段这里自动跟上（只是未必有中文名）。
    排除三类：凭据（`SECRET_FIELDS`）、`*_set` 状态位、选项列表（值不是标量）。
    """
    out: dict[str, Any] = {}
    for key, value in (snapshot or {}).items():
        if blocked_reason(key):
            continue
        # 只留标量：`work_locations` 这类选项列表是给界面下拉用的，不是可写配置
        if isinstance(value, bool) or isinstance(value, (int, float)) or isinstance(value, str):
            out[key] = value
    return out


def parse_value(key: str, raw: str, current: Any) -> Any:
    """把用户输入的字符串转成 config.yaml 里该有的类型。

    类型**按当前值推断**（`editable_snapshot` 里 bool 就是 Python bool），
    所以 dashboard 的字段表变了这边不用跟着改。
    """
    text = str(raw).strip()
    if isinstance(current, bool):
        low = text.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f'{key} 需要布尔值（开/关、on/off、true/false）')
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(float(text))
        except (TypeError, ValueError):
            raise ValueError(f'{key} 需要整数，收到 {text!r}') from None
    return text


def field_label(key: str) -> str:
    return LABELS.get(key, key)


def _hint(key: str) -> str:
    opts = OPTIONS.get(key)
    return f'（可选：{" / ".join(opts)}）' if opts else ''


def describe_field(key: str, value: Any) -> str:
    if isinstance(value, bool):
        shown = '开' if value else '关'
    elif value == '':
        shown = '（空）'
    else:
        shown = str(value)
    return f'{key} = {shown}  —  {field_label(key)}{_hint(key)}'


# ---------------------------------------------------------------- 指令执行


def _fmt_status(st: dict) -> str:
    sched = st.get('scheduler') or {}
    queue = st.get('queue') or {}
    cfg = st.get('config') or {}
    dur = st.get('today_duration') or {}
    eta = st.get('work_eta') or {}
    lines = []
    if sched.get('alive'):
        lines.append(f"调度器：运行中（PID {sched.get('pid')}"
                      + (f"，已运行 {sched['uptime']}" if sched.get('uptime') else '') + '）')
    else:
        lines.append('调度器：未运行')
    if queue.get('current'):
        lines.append(f"当前：{queue['current']}")
    if queue.get('pending'):
        lines.append(f"延时收尾：{queue['pending']}")
    if queue.get('next'):
        when = queue.get('next_at') or ''
        reason = queue.get('next_reason') or ''
        lines.append(f"下一项：{queue['next']}" + (f" @ {when}" if when else '')
                     + (f"（{reason}）" if reason else ''))
    if eta and eta.get('kind'):
        lines.append(f"进行中：{eta['kind']}，剩余 {eta.get('remaining', '?')} 秒"
                     f"（约 {eta.get('eta_clock', '?')} 收尾）")
    if dur:
        lines.append(f"今日时长：学习 {dur.get('learn_min', 0)} 分 + "
                     f"打工 {dur.get('work_min', 0)} 分（效率 {dur.get('eff_pct', 100)}%）")
    if cfg.get('strategy'):
        lines.append(f"策略：{cfg['strategy']}")
    attrs = st.get('attrs') or {}
    if attrs:
        lines.append(f"属性：力量 {attrs.get('force', 0)} · 智力 {attrs.get('intel', 0)} · "
                     f"魅力 {attrs.get('charm', 0)}")
    pet = st.get('pet') or {}
    ring = [f'{label} {pet[key]}' for key, label in
            (('energy', '体力'), ('clean', '清洁'), ('mood', '心情'))
            if isinstance(pet.get(key), (int, float))]
    if ring or isinstance(pet.get('coins'), (int, float)):
        tail = ' · '.join(ring + ([f"金币 {pet['coins']}"] if isinstance(pet.get('coins'), (int, float)) else []))
        lines.append('宠物：' + tail)
    if st.get('last_line'):
        lines.append(f"日志末行：{st['last_line']}")
    return '\n'.join(lines) or '（没有可显示的状态）'


def _fmt_attrs(st: dict) -> str:
    """宠物属性 + 当前状态。**这是用户最常问的一类**（"我现在力量多少"）。

    早先机器人只有设置项与调度状态，被问属性时只能回"我看不到"——数据其实在
    `career_plan.json`（dashboard 的 `_tg_attrs()` 读它喂进来）。
    """
    a = st.get('attrs') or {}
    pet = st.get('pet') or {}
    lines: list[str] = []
    if a:
        lines.append(f"力量 {a.get('force', 0)} · 智力 {a.get('intel', 0)} · "
                     f"魅力 {a.get('charm', 0)}（合计 {a.get('total', 0)}）")
        # 「金币记录」是职业页手工登记的门槛值，**不是宠物当前金币**
        # （当前金币在下面 pet 段）。两个都叫"金币"容易混，这里标注清楚。
        lines.append(f"工分 {a.get('workpoints', 0)} · 进阶门槛金币 {a.get('coins', 0)}"
                     "（职业页登记值）")
        gates = a.get('gates') or {}
        if gates:
            on = [k for k, v in gates.items() if v]
            off = [k for k, v in gates.items() if not v]
            parts = []
            if on:
                parts.append('已满足：' + '、'.join(on))
            if off:
                parts.append('未满足：' + '、'.join(off))
            lines.append('进阶门槛 ' + '；'.join(parts))
        steps = a.get('steps') or []
        pend = [s for s in steps if isinstance(s, (list, tuple)) and len(s) >= 4 and not s[2]]
        if pend:
            lines.append('待达成路线：')
            for step in pend[:4]:
                lines.append(f"  {step[0]} {step[1]} —— {step[3]}")
        if a.get('updated'):
            lines.append(f"（职业数据更新于 {a['updated']}）")
    if pet:
        ring = []
        for key, label in (('energy', '体力'), ('clean', '清洁'), ('mood', '心情')):
            if isinstance(pet.get(key), (int, float)):
                ring.append(f'{label} {pet[key]}')
        if ring:
            lines.append('当前状态：' + ' · '.join(ring))
        if isinstance(pet.get('coins'), (int, float)):
            lines.append(f"金币 {pet['coins']}")
        for key, label in (('soap', '香皂'), ('cookie', '饼干')):
            if pet.get(key) not in (None, ''):
                lines.append(f'{label} 库存 {pet[key]}')
    return '\n'.join(lines) or '（还没有属性数据：去仪表盘「职业」页同步一次，或等一次结算）'


def _fmt_apply(result: dict) -> str:
    applied = result.get('applied') or {}
    rejected = result.get('rejected') or []
    parts = []
    if applied:
        parts.append('已生效：' + '、'.join(f'{k}={v}' for k, v in applied.items()))
    if rejected:
        parts.append('被拒绝：' + '；'.join(str(r) for r in rejected))
    if not parts:
        parts.append('没有任何改动')
    return '\n'.join(parts)


class Bot:
    """指令执行器。**不持有线程**，`Worker` 负责轮询与投递。"""

    def __init__(self, bridge: Bridge, *, log: Callable[[str], None] | None = None):
        self.bridge = bridge
        self.log = log or bridge.log

    # -- 对外唯一入口 --------------------------------------------------

    def handle_text(self, text: str) -> str:
        """处理一条消息，返回要回复的文本（空串 = 不回复）。"""
        raw = str(text or '').strip()
        if not raw:
            return ''
        if raw.startswith('/'):
            return self._handle_command(raw)
        return self._handle_natural(raw)

    # -- 指令 ----------------------------------------------------------

    def _handle_command(self, raw: str) -> str:
        # `/set@my_bot a b` 在群里要带 @botname，先剥掉
        head, _, rest = raw.partition(' ')
        cmd = head.split('@', 1)[0].lower()
        rest = rest.strip()
        try:
            # `/start` 是 Telegram 的"开始"按钮，用户点开机器人就会发它 ——
            # **绝不能**绑成「启动调度器」（那是误触，还会让挂机凭空多跑一堆）。
            # 调度器启停走 /run /halt /pause /resume。
            if cmd in ('/start', '/help'):
                return HELP_TEXT
            if cmd == '/set':
                return self._cmd_set(rest)
            if cmd == '/get':
                return self._cmd_get(rest)
            if cmd == '/status':
                return _fmt_status(self.bridge.status())
            if cmd in ('/attrs', '/attr', '/属性'):
                return _fmt_attrs(self.bridge.status())
            if cmd == '/log':
                return self._cmd_log(rest)
            if cmd in ('/run', '/halt', '/pause', '/resume'):
                return self._cmd_scheduler({'/run': 'start', '/halt': 'stop'}.get(cmd, cmd[1:]))
        except _ApiError:
            raise
        except Exception as e:  # noqa: BLE001 - 回复错因，绝不把线程弄崩
            self.log(f'[TG] 指令 {cmd} 失败: {type(e).__name__}: {e}')
            return f'执行失败：{type(e).__name__}: {e}'
        return f'不认识的指令：{cmd}\n\n{HELP_TEXT}'

    def _cmd_set(self, rest: str) -> str:
        if not rest:
            return '用法：/set 字段 值\n先 /get 看有哪些字段。'
        # 值里可能有空格（好友名），所以只切两刀
        parts = rest.split(None, 1)
        key = parts[0].lstrip('/')
        if len(parts) < 2:
            return f'缺值：/set {key} 值'
        raw_value = parts[1].strip()
        fields = writable_fields(self.bridge.settings_snapshot())
        if key not in fields:
            why = blocked_reason(key)
            if why:
                return f'{key}：{why}'
            close = [k for k in fields if key in k or k in key]
            tail = f'\n最接近的：{"、".join(close[:5])}' if close else ''
            return f'没有这个字段：{key}{tail}\n/get 看全部。'
        try:
            value = parse_value(key, raw_value, fields[key])
        except ValueError as e:
            return str(e) + _hint(key)
        result = self.bridge.apply_settings({key: value})
        return _fmt_apply(result)

    def _cmd_get(self, rest: str) -> str:
        fields = writable_fields(self.bridge.settings_snapshot())
        key = rest.strip().lstrip('/')
        if key:
            if key not in fields:
                close = [k for k in fields if key in k or k in key]
                tail = f'\n最接近的：{"、".join(close[:5])}' if close else ''
                return f'没有这个字段：{key}{tail}'
            return describe_field(key, fields[key])
        # 全量：字段太多，按"可改字段名"紧凑列，值用短格式
        rows = []
        for k in sorted(fields):
            v = fields[k]
            if isinstance(v, bool):
                shown = '开' if v else '关'
            else:
                shown = str(v) if v != '' else '（空）'
            rows.append(f'{k} = {shown}')
        return ('可改字段（/get 字段名 看说明）：\n' + '\n'.join(rows) +
                '\n\n凭据类字段只能在仪表盘设置页改。')

    def _cmd_log(self, rest: str) -> str:
        try:
            n = int(rest.strip() or '30')
        except ValueError:
            n = 30
        n = max(1, min(n, 200))
        lines = self.bridge.logs(n) or []
        if not lines:
            return '（今天还没有日志）'
        return '\n'.join(lines)[-MAX_TEXT:]

    def _cmd_scheduler(self, action: str) -> str:
        result = self.bridge.scheduler_action(action) or {}
        info = self.bridge.scheduler_info() or {}
        head = ('已启动' if action == 'start' else
                '已停止' if action == 'stop' else
                '已让路（不中断任务）' if action == 'pause' else '已交还控制')
        if result.get('ok') is False:
            return f"{result.get('msg') or '操作失败'}"
        tail = f"\n调度器：{'运行中 PID ' + str(info['pid']) if info.get('alive') else '未运行'}"
        return f"{result.get('msg') or head}{tail}"

    # -- 自然语言 ------------------------------------------------------

    def _handle_natural(self, text: str) -> str:
        cfg = load_config().tgbot
        if not cfg.llm_enabled:
            return ('现在是纯指令模式。发 /help 看指令，'
                    '或在仪表盘设置里打开「自然语言」并配好模型接口。')
        # **API Key 不是必填**：本地网关（如 magpie 127.0.0.1:3425）和 ollama
        # 这类端点根本不需要鉴权，硬要求填 key 会让它们接不上。
        if not (cfg.llm_base_url and cfg.llm_model):
            return '自然语言已打开，但 base_url / 模型名 没配全。'
        try:
            return self._llm_loop(cfg, text)
        except _ApiError as e:
            return f'模型接口报错：{e.description}'
        except (URLError, TimeoutError) as e:
            return f'连不上模型接口：{e}'
        except Exception as e:  # noqa: BLE001
            self.log(f'[TG] 自然语言处理失败: {type(e).__name__}: {e}')
            return f'处理失败：{type(e).__name__}: {e}'

    def _system_prompt(self, cfg=None) -> str:
        fields = writable_fields(self.bridge.settings_snapshot())
        rows = []
        for k in sorted(fields):
            v = fields[k]
            kind = ('布尔（开/关）' if isinstance(v, bool)
                    else '整数' if isinstance(v, int) else '文本')
            rows.append(f'- {k} [{kind}] {field_label(k)}{_hint(k)}')
        # **身份必须交代清楚**（用户实报"问它是什么模型，它说不方便透露"）：
        # 早先的 prompt 只说"你是 QQ 宠物托管助手"，没说自己跑在什么模型上，
        # 又同时被要求"不要凭空承诺" —— 模型对不确定的事就选了回避话术。
        # 这是 prompt 的缺陷，不是模型的毛病。把实际配置的接口地址与模型名写进去，
        # 并明确允许如实回答身份问题。
        base = str(getattr(cfg, 'llm_base_url', '') or '').strip() or '（未配置）'
        model = str(getattr(cfg, 'llm_model', '') or '').strip() or '（未配置）'
        return (
            '你是 QQ 宠物自动化托管助手，用户在 Telegram 里让你改运行设置、查状态、'
            '启停调度器。\n\n'
            '## 你的身份（问到就如实说，不要回避）\n'
            '你是一个接在 QQ 宠物托管仪表盘上的 Telegram 控制机器人，'
            f'当前由本地/自建的 OpenAI 兼容接口驱动：接口地址 {base}，模型名 {model}。\n'
            '用户问"你是什么模型""谁在驱动你"这类问题时，**直接把这些说出来** —— '
            '这是用户自己的网关，没有任何保密必要；说"不便透露"只会让用户以为接错了。\n'
            '注意：模型名只是网关上的路由标识。如果你确实不知道底层实现细节，'
            '就说"我经由 <接口地址> 的 <模型名> 驱动"，不要编造参数量或训练信息。\n\n'
            '## 做事规则\n'
            '1. 只能用提供的工具去改，不要凭空承诺。\n'
            '2. 改完用一句话说清楚改了什么、改成了什么值。\n'
            '3. 用户想要的字段不在下面的清单里时，直接说明没有这个设置项，别猜。\n'
            '4. 凭据类（bot token / 模型 key / webhook）改不了，让用户去仪表盘设置页。\n'
            '5. 回复简短，用中文，不要用 markdown 表格（Telegram 纯文本下会很乱）。\n'
            '6. 涉及破坏性操作（停调度器会让挂机暂停）先确认再执行。\n'
            '7. 与改设置、查状态无关的闲聊可以正常回答，不必强行拐回任务。\n\n'
            '## 你能看到的数据（用户问起时先查，别急着说"看不到"）\n'
            '- **宠物属性**：力量/智力/魅力、工分、金币记录、进阶门槛、解锁路线 → `get_attributes`\n'
            '- **当前宠物状态**：体力/清洁/心情、金币、香皂/饼干库存 → `get_attributes`\n'
            '- **运行状态**：调度器、队列、今日时长、进行中的活动 → `get_status`\n'
            '- **设置项**：见下面的可改字段清单 → `get_settings` / `list_settings`\n'
            '用户问属性、体力、金币这类数值时**必须先用 `get_attributes` 查**，'
            '查完再答；只有在工具返回空值时才可以说明"还没有数据"。\n\n'
            '可改字段：\n' + '\n'.join(rows)
        )

    def _llm_tools(self) -> list[dict]:
        return [
            {'type': 'function', 'function': {
                'name': 'list_settings',
                'description': '列出所有可改的设置字段名（不知道有哪些字段时先调这个）',
                'parameters': {'type': 'object', 'properties': {}}}},
            {'type': 'function', 'function': {
                'name': 'get_settings',
                'description': '读取若干设置字段的当前值',
                'parameters': {'type': 'object', 'properties': {
                    'fields': {'type': 'array', 'items': {'type': 'string'},
                               'description': '字段名列表；留空表示全部'}},
                    'required': []}}},
            {'type': 'function', 'function': {
                'name': 'set_setting',
                'description': '修改一个设置项并写盘（下一轮调度生效）',
                'parameters': {'type': 'object', 'properties': {
                    'field': {'type': 'string', 'description': '字段名'},
                    'value': {'type': 'string',
                              'description': '新值；布尔用 开/关，整数直接写数字'}},
                    'required': ['field', 'value']}}},
            {'type': 'function', 'function': {
                'name': 'get_attributes',
                'description': '宠物的属性数值（力量/智力/魅力、工分、金币、进阶门槛、解锁路线）'
                               '与当前状态（体力/清洁/心情/金币库存）。用户问「我现在力量多少」'
                               '「属性怎么样」「体力还剩多少」时用这个',
                'parameters': {'type': 'object', 'properties': {}}}},
            {'type': 'function', 'function': {
                'name': 'get_status',
                'description': '调度器/队列/今日时长/当前活动等运行状态',
                'parameters': {'type': 'object', 'properties': {}}}},
            {'type': 'function', 'function': {
                'name': 'control_scheduler',
                'description': '启停调度器',
                'parameters': {'type': 'object', 'properties': {
                    'action': {'type': 'string',
                               'enum': ['start', 'stop', 'pause', 'resume']}},
                    'required': ['action']}}},
            {'type': 'function', 'function': {
                'name': 'get_logs',
                'description': '读最近的运行日志',
                'parameters': {'type': 'object', 'properties': {
                    'lines': {'type': 'integer', 'description': '行数，默认 30'}},
                    'required': []}}},
        ]

    def _call_tool(self, name: str, args: dict) -> str:
        if name == 'list_settings':
            return '\n'.join(sorted(writable_fields(self.bridge.settings_snapshot())))
        if name == 'get_settings':
            fields = writable_fields(self.bridge.settings_snapshot())
            want = args.get('fields') or list(fields)
            return '\n'.join(describe_field(k, fields[k]) for k in want if k in fields) \
                or '（没有匹配的字段）'
        if name == 'set_setting':
            field = str(args.get('field') or '').strip()
            raw = str(args.get('value') or '').strip()
            return self._cmd_set(f'{field} {raw}')
        if name == 'get_attributes':
            return _fmt_attrs(self.bridge.status())
        if name == 'get_status':
            return _fmt_status(self.bridge.status())
        if name == 'control_scheduler':
            return self._cmd_scheduler(str(args.get('action') or ''))
        if name == 'get_logs':
            return self._cmd_log(str(args.get('lines') or '30'))
        return f'未知工具：{name}'

    def _llm_loop(self, cfg, text: str) -> str:
        messages: list[dict] = [
            {'role': 'system', 'content': self._system_prompt(cfg)},
            {'role': 'user', 'content': text},
        ]
        tools = self._llm_tools()
        base = str(cfg.llm_base_url).strip().rstrip('/')
        url = base + '/chat/completions'
        for _ in range(max(1, int(cfg.llm_max_steps))):
            payload = {'model': cfg.llm_model, 'messages': messages,
                       'tools': tools, 'tool_choice': 'auto', 'temperature': 0.1}
            data = _post_with_auth(url, payload, cfg.llm_api_key)
            try:
                msg = data['choices'][0]['message']
            except (KeyError, IndexError, TypeError):
                raise ValueError('模型返回结构不对（缺少 choices[0].message）') from None
            calls = msg.get('tool_calls') or []
            if not calls:
                return str(msg.get('content') or '（模型没有返回内容）').strip()
            messages.append({'role': 'assistant',
                             'content': msg.get('content') or '',
                             'tool_calls': calls})
            for call in calls:
                fn = (call.get('function') or {})
                name = str(fn.get('name') or '')
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except ValueError:
                    args = {}
                try:
                    result = self._call_tool(name, args if isinstance(args, dict) else {})
                except Exception as e:  # noqa: BLE001 - 把错误回灌给模型，让它自己修正
                    result = f'执行失败：{type(e).__name__}: {e}'
                messages.append({'role': 'tool', 'tool_call_id': call.get('id') or name,
                                 'content': result})
        return '模型连续调了太多次工具，已停止。请把要求说得更具体些。'


def _post_with_auth(url: str, payload: dict, api_key: str) -> dict:
    """OpenAI 兼容的 chat/completions（DeepSeek / 智谱 / 通义 / OpenRouter / ollama 通用）。

    `api_key` 留空时**不发 Authorization 头**：本地网关（magpie、ollama、LM Studio）
    不需要鉴权，发一个空的 `Bearer ` 反而可能被某些实现判成非法认证而拒绝。
    """
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    key = str(api_key or '').strip()
    if key:
        headers['Authorization'] = f'Bearer {key}'
    req = Request(url, data=body, method='POST', headers=headers)
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        raise _ApiError(f'HTTP {e.code} {detail}'.strip(), e.code) from e


# ---------------------------------------------------------------- 轮询线程


class Worker:
    """后台长轮询。**跟着仪表盘进程活**（launchd 托管，随进程退出）。

    - `enabled` 每轮重读（设置页改完立即生效，不用重启仪表盘）
    - token 变了就用新的（同一套 offset，不会重复处理）
    - 任何异常都不许把线程弄死：记日志 + 退避后续命
    """

    def __init__(self, bot: Bot, *, log: Callable[[str], None] | None = None):
        self.bot = bot
        self.log = log or bot.log
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._offset = 0
        self.last_error = ''
        self.last_ok = 0.0
        self.handled = 0
        self._load_offset()

    # -- 游标持久化 ----------------------------------------------------

    def _load_offset(self) -> None:
        try:
            data = json.loads(STATE_FILE.read_text('utf-8'))
            self._offset = int(data.get('offset') or 0)
        except (OSError, ValueError, TypeError):
            self._offset = 0

    def _save_offset(self) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_name(STATE_FILE.name + '.tmp')
            tmp.write_text(json.dumps({'offset': self._offset}), encoding='utf-8')
            tmp.replace(STATE_FILE)
            STATE_FILE.chmod(0o600)
        except OSError as e:
            self.log(f'[TG] 游标写入失败（不影响运行）: {e}')

    # -- 生命周期 ------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='tg-bot', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        cfg = load_config().tgbot
        return {
            'running': bool(self._thread and self._thread.is_alive()),
            'enabled': bool(cfg.enabled),
            'llm_enabled': bool(cfg.llm_enabled),
            'offset': self._offset,
            'handled': self.handled,
            'last_ok': (time.strftime('%H:%M:%S', time.localtime(self.last_ok))
                        if self.last_ok else ''),
            'last_error': self.last_error,
            'allowed': len(_allowed_chats(cfg)),
        }

    # -- 主循环 --------------------------------------------------------

    def _run(self) -> None:
        self.log('[TG] 控制机器人线程已启动')
        while not self._stop.is_set():
            try:
                cfg = load_config().tgbot
            except Exception as e:  # noqa: BLE001
                self.last_error = f'读取配置失败: {e}'
                self._stop.wait(30)
                continue
            token, chats = _resolve_token(cfg), _allowed_chats(cfg)
            if not cfg.enabled or not token:
                self._stop.wait(5)
                continue
            if not chats:
                self.last_error = '没有白名单 chat（tgbot.allowed_chats 与 notify.telegram_chat_id 都为空）'
                self._stop.wait(30)
                continue
            try:
                self._poll_once(token, chats)
            except _ApiError as e:
                self.last_error = e.description
                if e.code == 401:
                    self.log('[TG] token 无效（401），停止轮询 60 秒')
                    self._stop.wait(60)
                elif e.retry_after:
                    self.log(f'[TG] 被限流，等 {e.retry_after:.0f} 秒')
                    self._stop.wait(e.retry_after)
                else:
                    self._stop.wait(5)
            except (URLError, TimeoutError, OSError) as e:
                # 长轮询超时是**正常现象**（服务端挂起 25 秒没消息），不当错误刷屏
                self.last_error = ''
                self._stop.wait(1)
            except Exception as e:  # noqa: BLE001
                self.last_error = f'{type(e).__name__}: {e}'
                self.log(f'[TG] 轮询异常: {self.last_error}')
                self._stop.wait(10)
        self.log('[TG] 控制机器人线程已退出')

    def _poll_once(self, token: str, chats: set[str]) -> None:
        data = _post(f'{_api_base(token)}/getUpdates', {
            'offset': self._offset,
            'timeout': POLL_TIMEOUT,
            'allowed_updates': ['message'],
        }, timeout=HTTP_TIMEOUT)
        self.last_ok = time.time()
        self.last_error = ''
        for update in data.get('result') or []:
            uid = int(update.get('update_id') or 0)
            if uid:
                self._offset = max(self._offset, uid + 1)
            self._dispatch(token, chats, update)
        if data.get('result'):
            self._save_offset()

    def _dispatch(self, token: str, chats: set[str], update: dict) -> None:
        msg = update.get('message') or {}
        text = msg.get('text') or msg.get('caption') or ''
        chat = msg.get('chat') or {}
        chat_id = str(chat.get('id') or '')
        if not text or not chat_id:
            return
        if chat_id not in chats:
            # **静默忽略**：不回话。回了等于告诉陌生人这个 bot 在跑什么。
            self.log(f'[TG] 忽略非白名单 chat {chat_id} 的消息')
            return
        try:
            reply = self.bot.handle_text(text)
        except Exception as e:  # noqa: BLE001
            reply = f'处理失败：{type(e).__name__}: {e}'
            self.log(f'[TG] 处理消息失败: {type(e).__name__}: {e}')
        self.handled += 1
        self.log(f'[TG] {_mask(text)}')
        if reply:
            try:
                send_message(token, chat_id, reply)
            except _ApiError as e:
                self.log(f'[TG] 回复失败: {e.description}')


def _mask(text: str, limit: int = 80) -> str:
    """日志里记指令内容，但截断（好友名/参数可能很长）。"""
    one = ' '.join(str(text or '').split())
    return one[:limit] + ('…' if len(one) > limit else '')


def _resolve_token(cfg) -> str:
    """`tgbot.token` 优先，留空复用 `notify.telegram_token`（同一个 bot 既能通知又能控制）。"""
    token = str(getattr(cfg, 'token', '') or '').strip()
    if token:
        return token
    try:
        return str(load_config().notify.telegram_token or '').strip()
    except Exception:  # noqa: BLE001
        return ''


def _allowed_chats(cfg) -> set[str]:
    """白名单：`tgbot.allowed_chats`；留空则只认 `notify.telegram_chat_id`。"""
    raw = str(getattr(cfg, 'allowed_chats', '') or '')
    chats = {c.strip() for c in re.split(r'[,，;；\s]+', raw) if c.strip()}
    if chats:
        return chats
    try:
        fallback = str(load_config().notify.telegram_chat_id or '').strip()
    except Exception:  # noqa: BLE001
        fallback = ''
    return {fallback} if fallback else set()


# ---------------------------------------------------------------- 对外装配

_WORKER: Worker | None = None
_LOCK = threading.Lock()


def start(bridge: Bridge) -> Worker | None:
    """由 dashboard 在启动时调用一次。重复调用返回同一个 Worker。"""
    global _WORKER
    with _LOCK:
        if _WORKER is not None:
            return _WORKER
        bot = Bot(bridge, log=bridge.log)
        _WORKER = Worker(bot, log=bridge.log)
        _WORKER.start()
        return _WORKER


def current() -> Worker | None:
    return _WORKER


def status() -> dict:
    if _WORKER is None:
        return {'running': False, 'enabled': False, 'llm_enabled': False,
                'offset': 0, 'handled': 0, 'last_ok': '', 'last_error': '', 'allowed': 0}
    return _WORKER.status()
