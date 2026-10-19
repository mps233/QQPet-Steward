"""TG 控制机器人的离线测试：不联网、不碰真实 config.yaml、不依赖 dashboard 进程。

覆盖三块最容易出错、且出错代价最高的逻辑：

1. **白名单**：非白名单 chat 必须静默丢弃（不回话、不执行）。这是 token 泄露时
   唯一的闸门，测不住就等于没有。
2. **字段白名单**：凭据、`tgbot_*` 自身配置、`*_set` 状态位必须不可写。
   否则一条 `/set tgbot_token xxx` 就把控制通道改掉了。
3. **类型转换**：`/set` 的字符串要按当前值的类型转换（bool / int / str），
   转错会写到 config.yaml 里变成 "true"/"60" 这种字符串，引擎读出来行为诡异。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import tg_bot  # noqa: E402


# ---------------------------------------------------------------- 测试替身


class FakeBridge:
    """dashboard 那套能力的替身：内存里存一份"配置"，记录所有调用。"""

    def __init__(self, settings: dict | None = None):
        self.settings = dict(settings or {
            'school_enabled': True,
            'school_duration': '10分钟',
            'school_attribute': '力量',
            'coin_threshold': 2000,
            'hire_name': '',
            'work_locations': ['风铃旅社', '彩虹酒馆'],   # 选项列表：不可写
            'care_enabled': True,
        })
        self.applied: list[dict] = []
        self.actions: list[str] = []
        self.scheduler_alive = False
        self.status_calls = 0
        self.logs_calls: list[int] = []

    # -- Bridge 接口 ---------------------------------------------------

    def settings_snapshot(self) -> dict:
        return dict(self.settings)

    def apply_settings(self, updates: dict) -> dict:
        self.applied.append(dict(updates))
        applied, rejected = {}, []
        for key, value in updates.items():
            if key not in self.settings:
                rejected.append(f'{key}: 不支持')
                continue
            self.settings[key] = value
            applied[key] = value
        return {'ok': not rejected, 'applied': applied, 'rejected': rejected}

    def scheduler_info(self) -> dict:
        return {'alive': self.scheduler_alive,
                'pid': 4242 if self.scheduler_alive else None,
                'uptime': '00:01:00' if self.scheduler_alive else ''}

    def scheduler_action(self, action: str) -> dict:
        self.actions.append(action)
        if action == 'start':
            self.scheduler_alive = True
            return {'ok': True, 'msg': '已启动（PID 4242）'}
        if action == 'stop':
            self.scheduler_alive = False
            return {'ok': True, 'msg': '已停止'}
        return {'ok': True, 'msg': f'{action} 完成'}

    def status(self) -> dict:
        self.status_calls += 1
        return {
            'scheduler': self.scheduler_info(),
            'queue': {'current': '打工', 'pending': '', 'next': '护理',
                      'next_at': '12:00:00', 'next_reason': '间隔未到'},
            'config': {'strategy': '学习 8h + 打工 8h'},
            'today_duration': {'learn_min': 120, 'work_min': 90, 'eff_pct': 100},
            'work_eta': {'kind': '上课', 'remaining': 300, 'eta_clock': '12:05:00'},
            'last_line': '[12:00:00] 测试日志行',
        }

    def logs(self, n: int) -> list[str]:
        self.logs_calls.append(n)
        return [f'[12:00:0{i % 10}] 第 {i} 行' for i in range(n)]

    def log(self, msg: str) -> None:
        pass


def make_bot(bridge: FakeBridge | None = None) -> tuple[tg_bot.Bot, FakeBridge]:
    bridge = bridge or FakeBridge()
    return tg_bot.Bot(tg_bot.Bridge(
        settings_snapshot=bridge.settings_snapshot,
        apply_settings=bridge.apply_settings,
        scheduler_info=bridge.scheduler_info,
        scheduler_action=bridge.scheduler_action,
        status=bridge.status,
        logs=bridge.logs,
        log=bridge.log,
    ), log=bridge.log), bridge


# ---------------------------------------------------------------- 字段白名单


def test_writable_fields_excludes_secrets_and_state_flags():
    snapshot = {
        'school_duration': '10分钟',
        'work_locations': ['风铃旅社'],            # 列表：不是可写配置
        'tgbot_token': '',
        'tgbot_token_set': True,
        'tgbot_llm_api_key': '',
        'notify_telegram_token': '',
        'notify_feishu_webhook': '',
    }
    fields = tg_bot.writable_fields(snapshot)
    assert fields == {'school_duration': '10分钟'}


@pytest.mark.parametrize('key', [
    'tgbot_token',
    'tgbot_enabled',
    'tgbot_allowed_chats',
    'notify_telegram_token',
    'notify_feishu_secret',
    'notify_telegram_token_set',
])
def test_blocked_fields_rejected_with_reason(key):
    assert tg_bot.blocked_reason(key), f'{key} 应该被挡住'


def test_allowed_field_has_no_blocked_reason():
    assert tg_bot.blocked_reason('school_duration') == ''


def test_set_on_secret_field_is_refused():
    bot, bridge = make_bot()
    reply = bot.handle_text('/set notify_telegram_token leaked')
    assert '仪表盘' in reply
    assert bridge.applied == [], '凭据字段不该被写盘'


def test_set_on_tgbot_field_is_refused():
    bot, bridge = make_bot()
    reply = bot.handle_text('/set tgbot_enabled 开')
    assert '仪表盘' in reply
    assert bridge.applied == []


# ---------------------------------------------------------------- 类型转换


def test_parse_value_bool_variants():
    for raw in ('开', 'on', 'true', '1', '启用', 'yes'):
        assert tg_bot.parse_value('x', raw, False) is True
    for raw in ('关', 'off', 'false', '0', '禁用', 'no'):
        assert tg_bot.parse_value('x', raw, True) is False


def test_parse_value_int():
    assert tg_bot.parse_value('coin_threshold', '3500', 2000) == 3500
    assert isinstance(tg_bot.parse_value('coin_threshold', '3500', 2000), int)


def test_parse_value_str_keeps_text():
    assert tg_bot.parse_value('hire_name', '  喵帕斯～  ', '') == '喵帕斯～'


def test_parse_value_rejects_bad_bool_and_int():
    with pytest.raises(ValueError):
        tg_bot.parse_value('x', '也许', False)
    with pytest.raises(ValueError):
        tg_bot.parse_value('coin_threshold', '很多', 2000)


# ---------------------------------------------------------------- /set 与 /get


def test_set_writes_through_bridge():
    bot, bridge = make_bot()
    reply = bot.handle_text('/set school_duration 30分钟')
    assert bridge.applied == [{'school_duration': '30分钟'}]
    assert '已生效' in reply
    assert bridge.settings['school_duration'] == '30分钟'


def test_set_value_with_spaces():
    bot, bridge = make_bot()
    bot.handle_text('/set hire_name 柠檬 家的 猫')
    assert bridge.applied == [{'hire_name': '柠檬 家的 猫'}]


def test_set_unknown_field_suggests_close_match():
    bot, bridge = make_bot()
    reply = bot.handle_text('/set school 力量')
    assert '没有这个字段' in reply
    assert 'school_enabled' in reply or 'school_duration' in reply
    assert bridge.applied == []


def test_set_without_value_shows_usage():
    bot, _ = make_bot()
    assert '缺值' in bot.handle_text('/set school_duration')


def test_get_single_field():
    bot, _ = make_bot()
    reply = bot.handle_text('/get coin_threshold')
    assert '2000' in reply
    assert '金币阈值' in reply


def test_get_all_lists_fields_but_not_secrets():
    bot, _ = make_bot()
    reply = bot.handle_text('/get')
    assert 'school_duration' in reply
    assert 'work_locations' not in reply, '选项列表不是可写配置，不该出现在清单里'


# ---------------------------------------------------------------- 状态与调度器


def test_status_includes_scheduler_and_duration():
    bot, bridge = make_bot()
    reply = bot.handle_text('/status')
    assert '调度器：未运行' in reply
    assert '学习 120 分' in reply
    assert '上课' in reply
    assert bridge.status_calls == 1


def test_log_returns_requested_lines():
    bot, bridge = make_bot()
    reply = bot.handle_text('/log 5')
    assert bridge.logs_calls == [5]
    assert len(reply.splitlines()) == 5


def test_log_clamps_absurd_count():
    bot, bridge = make_bot()
    bot.handle_text('/log 99999')
    assert bridge.logs_calls == [200], '行数要夹到 200 以内，别把 TG 撑爆'


def test_run_starts_scheduler_and_halt_stops():
    bot, bridge = make_bot()
    bot.handle_text('/run')
    assert bridge.actions == ['start']
    assert bridge.scheduler_alive
    bot.handle_text('/halt')
    assert bridge.actions == ['start', 'stop']
    assert not bridge.scheduler_alive


def test_slash_start_is_help_not_scheduler_start():
    """`/start` 是 Telegram 的"开始"按钮，绝不能绑成启动调度器（会误触）。"""
    bot, bridge = make_bot()
    reply = bot.handle_text('/start')
    assert bridge.actions == []
    assert '/set' in reply and '/run' in reply


def test_unknown_command_returns_help():
    bot, _ = make_bot()
    assert '不认识的指令' in bot.handle_text('/what')


def test_command_with_bot_suffix_is_handled():
    """群里必须发 `/set@my_bot`，@ 后缀要能剥掉。"""
    bot, bridge = make_bot()
    bot.handle_text('/set@qqpet_bot school_duration 30分钟')
    assert bridge.applied == [{'school_duration': '30分钟'}]


# ---------------------------------------------------------------- 自然语言开关


def test_natural_language_off_returns_hint(monkeypatch):
    class Cfg:
        llm_enabled = False
        llm_base_url = ''
        llm_model = ''
        llm_api_key = ''

    monkeypatch.setattr(tg_bot, 'load_config',
                        lambda: type('C', (), {'tgbot': Cfg})())
    bot, bridge = make_bot()
    reply = bot.handle_text('把学习改成三十分钟')
    assert '纯指令模式' in reply
    assert bridge.applied == []


def test_natural_language_requires_base_url_and_model(monkeypatch):
    """base_url / 模型名缺一不可。"""
    class Cfg:
        llm_enabled = True
        llm_base_url = ''                 # 缺 base_url
        llm_model = 'deepseek-chat'
        llm_api_key = 'sk-x'
        llm_max_steps = 6

    monkeypatch.setattr(tg_bot, 'load_config',
                        lambda: type('C', (), {'tgbot': Cfg})())
    bot, _ = make_bot()
    assert '没配全' in bot.handle_text('把学习改成三十分钟')


def test_natural_language_does_not_require_api_key(monkeypatch):
    """**API Key 不是必填**：本地网关（magpie / ollama / LM Studio）不需要鉴权。

    早先的实现把 key 也列进前置校验，导致本机网关明明能通却被挡在门外。
    这里锁住"没有 key 也会真的发出请求"（而不是提前 return 一句报错）。
    """
    class Cfg:
        llm_enabled = True
        llm_base_url = 'http://127.0.0.1:3425/v1'
        llm_model = 'group/auto-deepseek-v4-1-flash'
        llm_api_key = ''                  # 故意不填
        llm_max_steps = 6

    monkeypatch.setattr(tg_bot, 'load_config',
                        lambda: type('C', (), {'tgbot': Cfg})())

    seen: list[tuple[str, dict]] = []

    def fake_post(url, payload, api_key):
        seen.append((url, payload))
        return {'choices': [{'message': {'content': '好，已改成 30 分钟'}}]}

    monkeypatch.setattr(tg_bot, '_post_with_auth', fake_post)
    bot, bridge = make_bot()
    reply = bot.handle_text('把学习改成三十分钟')
    assert seen, '没有 key 也必须真的发请求'
    assert seen[0][0] == 'http://127.0.0.1:3425/v1/chat/completions'
    assert '30 分钟' in reply


def test_no_authorization_header_when_key_empty(monkeypatch):
    """key 为空时**不发** Authorization 头 —— 空的 `Bearer ` 会被部分网关判非法。"""
    captured: dict = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(req, timeout=None):
        captured['headers'] = dict(req.header_items())
        return FakeResp()

    monkeypatch.setattr(tg_bot, 'urlopen', fake_urlopen)
    tg_bot._post_with_auth('http://127.0.0.1:3425/v1/chat/completions',
                           {'model': 'm', 'messages': []}, '')
    keys = {k.lower() for k in captured['headers']}
    assert 'authorization' not in keys


def test_authorization_header_sent_when_key_present(monkeypatch):
    captured: dict = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(req, timeout=None):
        captured['headers'] = dict(req.header_items())
        return FakeResp()

    monkeypatch.setattr(tg_bot, 'urlopen', fake_urlopen)
    tg_bot._post_with_auth('https://api.deepseek.com/v1/chat/completions',
                           {'model': 'm', 'messages': []}, 'sk-secret')
    headers = {k.lower(): v for k, v in captured['headers'].items()}
    assert headers.get('authorization') == 'Bearer sk-secret'


# ---------------------------------------------------------------- 白名单


class _Cfg:
    def __init__(self, allowed='', token=''):
        self.allowed_chats = allowed
        self.token = token


class _FullCfg:
    """`load_config()` 的替身：同时带 tgbot 与 notify（token 可以填在任一边）。"""

    def __init__(self, allowed='', token='', chat_id='', telegram_token=''):
        self.tgbot = _Cfg(allowed, token)
        self.notify = type('N', (), {'telegram_chat_id': chat_id,
                                     'telegram_token': telegram_token})()


def test_allowed_chats_explicit_wins(monkeypatch):
    monkeypatch.setattr(tg_bot, 'load_config',
                        lambda: _FullCfg(allowed='111,222', chat_id='999'))
    assert tg_bot._allowed_chats(_Cfg('111, 222')) == {'111', '222'}


def test_allowed_chats_falls_back_to_notify_chat_id(monkeypatch):
    monkeypatch.setattr(tg_bot, 'load_config',
                        lambda: _FullCfg(allowed='', chat_id='999'))
    assert tg_bot._allowed_chats(_Cfg('')) == {'999'}


def test_allowed_chats_empty_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(tg_bot, 'load_config', lambda: _FullCfg('', ''))
    assert tg_bot._allowed_chats(_Cfg('')) == set()


def test_resolve_token_prefers_tgbot_over_notify(monkeypatch):
    """tgbot.token 优先：同一个 bot 也能配成两个 token 的话，以控制通道为准。"""
    monkeypatch.setattr(tg_bot, 'load_config',
                        lambda: _FullCfg(telegram_token='notify-token'))
    assert tg_bot._resolve_token(_Cfg(token='tgbot-token')) == 'tgbot-token'


def test_resolve_token_falls_back_to_notify(monkeypatch):
    """tgbot.token 留空 → 复用通知里那个 token（一个 bot 既能通知又能控制）。"""
    monkeypatch.setattr(tg_bot, 'load_config',
                        lambda: _FullCfg(telegram_token='notify-token'))
    assert tg_bot._resolve_token(_Cfg(token='')) == 'notify-token'


def test_resolve_token_empty_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(tg_bot, 'load_config', lambda: _FullCfg())
    assert tg_bot._resolve_token(_Cfg(token='')) == ''


# ---------------------------------------------------------------- 投递（非白名单静默）


class _Recorder:
    """替换 tg_bot.send_message，记录到底回了谁。"""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def __call__(self, token, chat_id, text):
        self.sent.append((token, chat_id, text))


def _update(chat_id: str, text: str, uid: int = 1) -> dict:
    return {'update_id': uid,
            'message': {'chat': {'id': int(chat_id)}, 'text': text}}


def test_non_whitelisted_chat_is_silently_dropped(monkeypatch):
    bot, bridge = make_bot()
    worker = tg_bot.Worker(bot, log=bridge.log)
    rec = _Recorder()
    monkeypatch.setattr(tg_bot, 'send_message', rec)
    monkeypatch.setattr(worker, '_save_offset', lambda: None)

    worker._dispatch('tok', {'111'}, _update('999', '/set coin_threshold 1'))

    assert rec.sent == [], '非白名单 chat 绝不能收到任何回复'
    assert bridge.applied == [], '非白名单 chat 的指令绝不能被执行'


def test_whitelisted_chat_gets_reply(monkeypatch):
    bot, bridge = make_bot()
    worker = tg_bot.Worker(bot, log=bridge.log)
    rec = _Recorder()
    monkeypatch.setattr(tg_bot, 'send_message', rec)
    monkeypatch.setattr(worker, '_save_offset', lambda: None)

    worker._dispatch('tok', {'111'}, _update('111', '/set coin_threshold 3000'))

    assert bridge.applied == [{'coin_threshold': 3000}]
    assert len(rec.sent) == 1
    assert rec.sent[0][1] == '111'


# ---------------------------------------------------------------- 文本切片


def test_split_text_keeps_lines_intact():
    text = '\n'.join(f'第{i}行内容' for i in range(500))
    chunks = tg_bot._split_text(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert '\n'.join(chunks) == text, '切片后拼回去必须与原文一致'


def test_split_text_hard_splits_overlong_line():
    chunks = tg_bot._split_text('x' * 250, limit=100)
    assert [len(c) for c in chunks] == [100, 100, 50]


def test_split_text_short_text_untouched():
    assert tg_bot._split_text('短消息', limit=100) == ['短消息']


# ---------------------------------------------------------------- 审计（改配置必须留痕）

def test_worker_state_file_is_isolated_by_conftest():
    """`runs/tgbot_state.json` 必须被 conftest 重定向。

    不重定向的话，测试里的 Worker 会读到线上 bot 的真实 offset，写测试时还可能
    把线上游标写坏（表现为"重启后重放一堆旧消息"或"新消息收不到"）。
    """
    assert 'tmp' in str(tg_bot.STATE_FILE) or str(tg_bot.STATE_FILE).startswith('/private/var')


def test_apply_through_bridge_records_audit_line():
    """TG 改配置必须留审计 —— 这是 `dashboard._tg_apply` 的不变量。

    HTTP 那条路的 audit 写在 `/api/settings` 路由里，TG 走 Bridge 不经过它；
    这里用一个带审计钩子的 Bridge 证明"经 Bridge 改配置"这条路径本身可被记账，
    并锁住"审计里同时含提交值与生效值"这个格式（排障时要能回答改了什么）。
    """
    lines: list[str] = []

    class AuditedBridge(FakeBridge):
        def apply_settings(self, updates: dict) -> dict:
            result = super().apply_settings(updates)
            lines.append(f'设置保存(来自 TG 机器人): 提交 {updates} → 生效 {result["applied"]}')
            return result

    bridge = AuditedBridge()
    bot = tg_bot.Bot(tg_bot.Bridge(
        settings_snapshot=bridge.settings_snapshot,
        apply_settings=bridge.apply_settings,
        scheduler_info=bridge.scheduler_info,
        scheduler_action=bridge.scheduler_action,
        status=bridge.status,
        logs=bridge.logs,
        log=bridge.log,
    ), log=bridge.log)

    bot.handle_text('/set coin_threshold 3500')

    assert len(lines) == 1
    assert '来自 TG 机器人' in lines[0]
    assert 'coin_threshold' in lines[0] and '3500' in lines[0]


# ---------------------------------------------------------------- 身份（用户实报回归）

def test_system_prompt_states_actual_model_identity():
    """system prompt 必须写清「接口地址 + 模型名」，并明确允许如实回答身份。

    回归背景：用户问机器人"你是什么模型"，它答"具体底层模型不便透露"。
    根因是 prompt 只说"你是 QQ 宠物托管助手"，**没告诉它自己跑在什么模型上**，
    同时又有"不要凭空承诺"这条约束 —— 模型对不确定的事就选了回避话术。
    这不是模型的问题，是 prompt 缺信息。
    """
    class Cfg:
        llm_base_url = 'http://127.0.0.1:3425/v1'
        llm_model = 'group/auto-deepseek-v4-1-flash'

    bot, _ = make_bot()
    prompt = bot._system_prompt(Cfg)

    assert 'http://127.0.0.1:3425/v1' in prompt, '接口地址必须写进 prompt'
    assert 'group/auto-deepseek-v4-1-flash' in prompt, '模型名必须写进 prompt'
    assert '不便透露' in prompt, '要显式点名禁止这句回避话术'
    assert '如实' in prompt or '直接把这些说出来' in prompt


def test_system_prompt_handles_unconfigured_identity():
    """没配模型时不能拼出 "None" 这种东西塞给模型。"""
    class Cfg:
        llm_base_url = ''
        llm_model = ''

    bot, _ = make_bot()
    prompt = bot._system_prompt(Cfg)
    assert '（未配置）' in prompt
    assert 'None' not in prompt


def test_llm_loop_passes_configured_model_into_prompt(monkeypatch):
    """接线检查：`_llm_loop` 必须把 cfg 传进 `_system_prompt`，否则身份段是空的。"""
    class Cfg:
        llm_enabled = True
        llm_base_url = 'http://127.0.0.1:3425/v1'
        llm_model = 'group/auto-deepseek-v4-1-flash'
        llm_api_key = ''
        llm_max_steps = 2

    monkeypatch.setattr(tg_bot, 'load_config',
                        lambda: type('C', (), {'tgbot': Cfg})())

    seen_prompt: list[str] = []

    def fake_post(url, payload, api_key):
        system = payload['messages'][0]
        assert system['role'] == 'system'
        seen_prompt.append(system['content'])
        return {'choices': [{'message': {'content': '好的'}}]}

    monkeypatch.setattr(tg_bot, '_post_with_auth', fake_post)
    bot, _ = make_bot()
    bot.handle_text('你是什么模型')

    assert seen_prompt, '应该真的发了一次请求'
    assert 'group/auto-deepseek-v4-1-flash' in seen_prompt[0], \
        'cfg 没传进 _system_prompt，身份段会是「（未配置）」'


# ---------------------------------------------------------------- 属性查询（用户实报回归）

def _attrs_status() -> dict:
    return {
        'attrs': {'force': 8726, 'intel': 222, 'charm': 589, 'total': 9537,
                  'workpoints': 0, 'coins': 0,
                  'gates': {'工分': False, '金币': False, '初级毕业': False},
                  'steps': [['S6', '三维各 750 → 全 8 线初级', False, '最低 222/750']],
                  'updated': '01:20:43'},
        'pet': {'energy': 75, 'clean': 74, 'mood': 98, 'coins': 111000},
        'scheduler': {'alive': True, 'pid': 1, 'uptime': '1:00:00'},
        'queue': {}, 'config': {}, 'today_duration': {}, 'work_eta': None,
        'last_line': '',
    }


def _attr_bot() -> tuple[tg_bot.Bot, dict]:
    seen = {'status': 0}

    def status():
        seen['status'] += 1
        return _attrs_status()

    bot = tg_bot.Bot(tg_bot.Bridge(
        settings_snapshot=lambda: {'school_duration': '10分钟'},
        apply_settings=lambda u: {'ok': True, 'applied': u, 'rejected': []},
        scheduler_info=lambda: {'alive': True}, scheduler_action=lambda a: {'ok': True},
        status=status, logs=lambda n: [], log=lambda m: None))
    return bot, seen


def test_attrs_command_shows_three_attributes():
    """回归：用户问「力量多少」，机器人早先只能回"我看不到这个数值"。

    根因不是模型笨——它只被喂了设置项与调度状态，属性数据（career_plan.json）
    从来没进过它的可见范围。`/attrs` 与 `get_attributes` 工具就是补这个洞。
    """
    bot, _ = _attr_bot()
    reply = bot.handle_text('/attrs')
    assert '8726' in reply          # 力量
    assert '222' in reply           # 智力
    assert '589' in reply           # 魅力
    assert '体力 75' in reply
    assert '金币 111000' in reply   # 宠物当前金币，不是门槛登记值


def test_attrs_command_marks_progress_gates():
    bot, _ = _attr_bot()
    reply = bot.handle_text('/attrs')
    assert '未满足' in reply and '工分' in reply
    assert 'S6' in reply, '待达成路线要列出来'


def test_status_includes_attrs_and_pet():
    """`/status` 也要带属性：用户往往直接用 /status 而不是 /attrs。"""
    bot, _ = _attr_bot()
    reply = bot.handle_text('/status')
    assert '属性：力量 8726' in reply
    assert '宠物：体力 75' in reply


def test_attrs_handles_missing_data():
    """没有属性数据时不能崩，也不能编数字。"""
    bot = tg_bot.Bot(tg_bot.Bridge(
        settings_snapshot=lambda: {}, apply_settings=lambda u: {'ok': True},
        scheduler_info=lambda: {'alive': False}, scheduler_action=lambda a: {'ok': True},
        status=lambda: {'attrs': {}, 'pet': {}}, logs=lambda n: [], log=lambda m: None))
    reply = bot.handle_text('/attrs')
    assert '还没有属性数据' in reply


def test_llm_tools_include_get_attributes():
    bot, _ = _attr_bot()
    names = [t['function']['name'] for t in bot._llm_tools()]
    assert 'get_attributes' in names


def test_get_attributes_tool_returns_force_value():
    bot, _ = _attr_bot()
    assert '8726' in bot._call_tool('get_attributes', {})


def test_system_prompt_lists_visible_data_sources():
    """prompt 必须告诉模型「数据是能查的，别急着说看不到」。"""
    bot, _ = _attr_bot()
    prompt = bot._system_prompt(None)
    assert 'get_attributes' in prompt
    assert '别急着说' in prompt or '必须先用' in prompt
