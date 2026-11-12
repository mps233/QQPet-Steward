"""每日福利（衣橱 → 道具）任务的接线与流程单测（不连设备）。

覆盖：
  ① 任务键 / 默认顺序 / 调度名 / 支线语义 / 定位项；
  ② load_config 与仪表盘设置页的读写（含"开关打开但 order 里没有它"时自动补 order）；
  ③ 用假场景跑三条流程：两张都领过（无事可做）、领免费礼包、看广告礼包；
  ④ 广告没给奖励时抛异常（交给调度器重试），不会静默当作成功。
"""
from __future__ import annotations

import pytest

import dashboard
import src.settings as S
import scenarios.daily_gift as dg
from scenarios.daily_gift import DailyGiftScenario
from scenarios.runner import SIDE_TASK_KEYS, TASK_NAMES
from src.config import TASK_KEYS, TasksConfig, load_config
from src.locators import LOCATORS


# ---------------------------------------------------------------------------
# 接线
# ---------------------------------------------------------------------------

def test_daily_gift_is_a_queue_task():
    assert 'daily_gift' in TASK_KEYS
    assert TASK_NAMES['daily_gift'] == '每日福利'
    assert 'daily_gift' in SIDE_TASK_KEYS
    assert 'daily_gift' in TasksConfig().order.split('>')


def test_daily_gift_locators_registered():
    for name in ('wardrobe', 'wardrobe_items_tab', 'daily_gift_free',
                 'daily_gift_ad', 'daily_gift_claimed', 'ad_close', 'reward_popup'):
        assert name in LOCATORS, f'缺少定位项 {name}'


def test_load_config_daily_gift(tmp_path):
    p = tmp_path / 'config.yaml'
    p.write_text(
        'tasks:\n'
        '  order: daily_gift>care>work\n'
        '  daily_gift:\n'
        '    enabled: false\n'
        '    trigger: daily\n'
        '    daily_times: ["00:05"]\n',
        encoding='utf-8',
    )
    cfg = load_config(p)
    assert cfg.tasks.daily_gift.enabled is False
    assert cfg.tasks.daily_gift.daily_times == ['00:05']
    assert cfg.tasks.order.split('>')[0] == 'daily_gift'


def test_dashboard_apply_daily_gift(monkeypatch):
    fake: dict = {}
    monkeypatch.setattr(S, 'load_raw', lambda: fake)
    monkeypatch.setattr(S, 'save_raw', lambda data: None)
    res = dashboard.apply_settings({'daily_gift_enabled': True})
    assert res['ok'], res
    assert fake['tasks']['daily_gift']['enabled'] is True


def test_enabling_daily_gift_appends_it_to_existing_order(monkeypatch):
    # 老 config 的 order 没有 daily_gift：设置页开关打开时必须自动补进 order，
    # 否则 runner 只按 order 建队列，任务永远不跑。
    fake = {'tasks': {'order': 'care>work'}}
    monkeypatch.setattr(S, 'load_raw', lambda: fake)
    monkeypatch.setattr(S, 'save_raw', lambda data: None)
    res = dashboard.apply_settings({'daily_gift_enabled': True})
    assert res['ok'], res
    assert fake['tasks']['order'] == 'care>work>daily_gift'


def test_editable_snapshot_daily_gift(monkeypatch, tmp_path):
    (tmp_path / 'config.yaml').write_text(
        'tasks:\n'
        '  order: daily_gift>care>work\n'
        '  daily_gift:\n'
        '    enabled: true\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(dashboard, 'APP_ROOT', tmp_path)
    ed = dashboard.editable_snapshot()
    assert ed['daily_gift_enabled'] is True


def test_editable_snapshot_daily_gift_hidden_when_not_in_order(monkeypatch, tmp_path):
    (tmp_path / 'config.yaml').write_text(
        'tasks:\n'
        '  order: care>work\n'
        '  daily_gift:\n'
        '    enabled: true\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(dashboard, 'APP_ROOT', tmp_path)
    ed = dashboard.editable_snapshot()
    # order 里没有 = runner 不会调度，设置页也不该显示成"已启用"
    assert ed['daily_gift_enabled'] is False


# ---------------------------------------------------------------------------
# 流程（假场景：不截图、不 OCR、不连设备）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """流程测试不真的等 1~2 秒（dg.time 与全局 time 是同一个模块）。"""
    monkeypatch.setattr(dg.time, 'sleep', lambda *_a, **_k: None)


class _FakeDev:
    @staticmethod
    def window_size():
        return 1080, 2412

    @staticmethod
    def hierarchy():
        return None


class _FakeGift(DailyGiftScenario):
    """只实现每日福利流程需要的设备接口。"""

    def __init__(self, free: bool = False, ad: bool = False,
                 ad_watch_ok: bool = True):
        self.dev = _FakeDev()
        # False = 未领，True = 已领
        self.cards = {'free': free, 'ad': ad}
        self.ad_watch_ok = ad_watch_ok
        self.clicks: list[tuple[int, int]] = []
        self.dismissed = 0
        self.recovered = 0
        self.main_page = False
        self.in_ad = False
        self.reward_popup = False

    # 设备接口
    def see(self, name, screen=None, source=None):
        if name == 'main_sign':
            return (540, 324, 1.0) if self.main_page else None
        if name == 'wardrobe':
            return (957, 171, 1.0)
        if name == 'wardrobe_items_tab':
            return (636, 1036, 1.0)
        if name == 'daily_gift_free':
            return None if self.cards['free'] else (200, 1352, 1.0)
        if name == 'daily_gift_ad':
            return None if self.cards['ad'] else (540, 1352, 1.0)
        if name == 'daily_gift_claimed':
            return (200, 1352, 1.0) if any(self.cards.values()) else None
        if name == 'ad_close':
            return (1004, 243, 1.0) if self.in_ad else None
        if name == 'reward_popup':
            return (540, 1206, 1.0) if self.reward_popup else None
        return None

    def click(self, x, y):
        self.clicks.append((x, y))
        if (x, y) == (957, 171):          # 点「装扮」进入衣橱：主页标志消失
            self.main_page = False
        elif (x, y) == (200, 1352) and not self.cards['free']:
            self.cards['free'] = True
            self.reward_popup = True
        elif (x, y) == (540, 1352) and not self.cards['ad']:
            self.in_ad = True

    def ensure_main_page(self):
        self.main_page = True

    def go_back(self):
        self.main_page = True

    def screen(self):
        return None

    # 卡片状态：直接按卡片布尔值返回，跳过控件树 XML 解析
    def _read_cards(self):
        free = None if self.cards['free'] else (200, 1352, 1.0)
        ad = None if self.cards['ad'] else (540, 1352, 1.0)
        return free, ad, bool(self.cards['free']), bool(self.cards['ad'])

    # 等待类：假场景直接按当前状态返回，避免真截图/OCR
    def _wait_cards(self, timeout: float = dg.CARD_WAIT) -> bool:
        return True

    def _wait_reward_popup(self, timeout: float = dg.REWARD_WAIT) -> bool:
        return self.reward_popup

    def _wait_ad_claimed(self, timeout: float = dg.AD_CLAIMED_WAIT) -> bool:
        return self.cards['ad']

    def _dismiss_reward_popup(self):
        self.dismissed += 1
        self.reward_popup = False

    def _recover_to_items_page(self):
        self.recovered += 1
        self.in_ad = False


@pytest.fixture
def fake_ad_watch(monkeypatch):
    """把 daily_gift 里 import 的 watch_ad 换成假实现（按卡片状态给结果）。"""
    def _watch(scen, prefix='', **_kw):
        scen.in_ad = False
        if scen.ad_watch_ok:
            scen.cards['ad'] = True
            scen.reward_popup = True
            scen._dismiss_reward_popup()
            return True
        return False
    monkeypatch.setattr(dg, 'watch_ad', _watch)
    return _watch


def test_run_when_both_already_claimed(fake_ad_watch):
    scen = _FakeGift(free=True, ad=True)
    assert scen.run() is False
    # 没点过任何卡片（只点了装扮/道具页签）
    assert (200, 1352) not in scen.clicks
    assert (540, 1352) not in scen.clicks
    assert scen.main_page is True


def test_claim_free(fake_ad_watch):
    scen = _FakeGift(free=False, ad=True)
    scen._claim_free()
    assert (200, 1352) in scen.clicks
    assert scen.cards['free'] is True
    assert scen.dismissed == 1
    assert scen.reward_popup is False


def test_claim_ad(fake_ad_watch):
    scen = _FakeGift(free=True, ad=False)
    scen._claim_ad()
    assert (540, 1352) in scen.clicks
    assert scen.cards['ad'] is True
    assert scen.dismissed == 1


def test_run_claims_free_then_ad(fake_ad_watch):
    scen = _FakeGift(free=False, ad=False)
    assert scen.run() is False
    assert (200, 1352) in scen.clicks
    assert (540, 1352) in scen.clicks
    assert scen.cards == {'free': True, 'ad': True}
    assert scen.main_page is True


def test_ad_without_reward_defers_and_retries(monkeypatch):
    """广告没给奖励：重试一次仍失败 -> TaskDeferred（延后 30 分钟再试）。

    不能用 RuntimeError：那会走"回主页面重试 -> 重启设备恢复"，为一次广告网络
    抖动重启手机不值得；也不能静默当成功（今天的礼包还没领到）。
    """
    from src.scenario import TaskDeferred

    scen = _FakeGift(free=True, ad=False, ad_watch_ok=False)
    calls = []

    def _watch(s, prefix='', **_kw):
        calls.append(1)
        s.in_ad = False
        return False

    monkeypatch.setattr(dg, 'watch_ad', _watch)
    with pytest.raises(TaskDeferred):
        scen._claim_ad()
    assert len(calls) == dg.AD_RETRY
    assert scen.recovered == dg.AD_RETRY - 1
    assert scen.cards['ad'] is False


class _XmlDev:
    def __init__(self, xml: str):
        self.xml = xml

    def hierarchy(self):
        # 真机 U2Device.hierarchy() 返回的也是 uiautomator2 的 PageSource
        from uiautomator2.xpath import PageSource
        return PageSource(self.xml)

    @staticmethod
    def window_size():
        return 1080, 2412


def _parse_cards(xml: str):
    class _ParserGift(DailyGiftScenario):
        def __init__(self):
            self.dev = _XmlDev(xml)

    return _ParserGift()._read_cards()


def test_read_cards_unclaimed():
    free, ad, cf, ca = _parse_cards(
        '<hierarchy>'
        '<node content-desc="领取 高钙奶 x 1" bounds="[48,1133][351,1571]"/>'
        '<node content-desc="看视频领取 高钙奶 x 3" bounds="[388,1133][691,1571]"/>'
        '</hierarchy>'
    )
    assert free and ad
    assert not cf and not ca


def test_read_cards_claimed_splits_by_side():
    free, ad, cf, ca = _parse_cards(
        '<hierarchy>'
        '<node content-desc="高钙奶 x 1，已领取" bounds="[48,1133][351,1571]"/>'
        '<node content-desc="高钙奶 x 3，已领取" bounds="[388,1133][691,1571]"/>'
        '</hierarchy>'
    )
    assert free is None and ad is None
    assert cf and ca


def test_read_cards_mixed_and_ignores_small_toast():
    free, ad, cf, ca = _parse_cards(
        '<hierarchy>'
        '<node content-desc="高钙奶 x 1，已领取" bounds="[48,1133][351,1571]"/>'
        '<node content-desc="看视频领取 高钙奶 x 3" bounds="[388,1133][691,1571]"/>'
        '<node content-desc="已领取" bounds="[500,2200][700,2260]"/>'  # 小字提示，不算卡
        '</hierarchy>'
    )
    assert free is None and ad
    assert cf and not ca


def test_free_without_popup_defers(monkeypatch):
    """点免费卡没弹奖励（也没变成已领取）：延后重试，不当作成功。"""
    from src.scenario import TaskDeferred

    class _NoPopup(_FakeGift):
        def click(self, x, y):
            self.clicks.append((x, y))   # 点了卡片但不弹奖励、状态也不变

        def _wait_reward_popup(self, timeout: float = dg.REWARD_WAIT) -> bool:
            return False

    scen = _NoPopup(free=False, ad=True)
    with pytest.raises(TaskDeferred):
        scen._claim_free()
