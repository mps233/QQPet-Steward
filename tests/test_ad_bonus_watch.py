"""激励视频观看流程（src/ad_bonus.watch_ad）的离线测试。

不连设备：OCR 结果用序列喂入，控件定位用假场景。重点锁住两条：
  ① 倒计时走完 → 点 X（优先用 content-desc 定位）→ 关掉奖励弹层；
  ② 没有倒计时文字的广告不能提前点 X，要等「恭喜已获得奖励」再关。
"""
from __future__ import annotations

import src.ad_bonus as ab


class _FakeTime:
    """只给 src.ad_bonus 用的假时钟：每次 time() 前进 1 秒，sleep 不等待。"""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        self.now += 1.0
        return self.now

    def sleep(self, *_a, **_k):
        pass


class _FakeScen:
    class dev:
        @staticmethod
        def window_size():
            return 1080, 2412

    def __init__(self, close_point=(1004, 243), popup=True):
        self.clicks: list[tuple[int, int, bool]] = []
        self._close_point = close_point
        self._popup = popup
        self.ad_seen = False       # 已经通过 content-desc 认出广告页
        self.banner_seen = False   # OCR 已经读到「恭喜已获得奖励」
        self.post_ad_calls = 0

    def screen(self):
        return object()

    def see(self, name):
        if name == 'ad_close' and self._close_point:
            self.ad_seen = True
            return (*self._close_point, 1.0)
        if name == 'reward_popup' and self._popup:
            return (540, 1206, 1.0)
        return None

    def click(self, x, y):
        self.clicks.append((x, y, self.banner_seen))


def _install(monkeypatch, scen, *, countdown_frames=None):
    """装假时钟 + OCR：countdown_frames 用于"有倒计时"的用例。"""
    monkeypatch.setattr(ab, 'time', _FakeTime())
    state = {'i': 0}

    def fake_ocr(_screen):
        if countdown_frames is not None:
            i = min(state['i'], len(countdown_frames) - 1)
            state['i'] += 1
            return countdown_frames[i]
        # 无倒计时用例：认出广告页后先空几帧，再给播完横幅
        if not scen.ad_seen:
            return []
        scen.post_ad_calls += 1
        if scen.post_ad_calls <= 3:
            return []
        scen.banner_seen = True
        return [('恭喜已获得奖励', 232, 246, 1.0)]

    monkeypatch.setattr(ab, 'ocr_screen', fake_ocr)


def test_watch_ad_countdown_then_close(monkeypatch):
    scen = _FakeScen()
    _install(monkeypatch, scen, countdown_frames=[
        [('观看2秒后即可获得奖励', 300, 246, 1.0)],
        [('观看2秒后即可获得奖励', 300, 246, 1.0)],
        [('观看1秒后即可获得奖励', 300, 246, 1.0)],
        [('恭喜获得', 543, 860, 1.0)],
    ])
    assert ab.watch_ad(scen) is True
    # 优先用 content-desc（ad_close）点 X，而不是坐标
    assert scen.clicks[0] == (1004, 243, False)
    # 奖励弹层：点 0.83h 的空白区关闭
    assert scen.clicks[-1][:2] == (540, int(2412 * 0.83))


def test_watch_ad_without_countdown_does_not_click_early(monkeypatch):
    """没有倒计时文字时（只靠 X 按钮认出的广告）不能当成"已播完"提前点 X。"""
    scen = _FakeScen()
    _install(monkeypatch, scen)
    assert ab.watch_ad(scen, max_wait=20) is True
    # 点 X 的那一刻，OCR 已经读到「恭喜已获得奖励」
    assert scen.clicks[0] == (1004, 243, True)


def test_watch_ad_no_ad_page_returns_false(monkeypatch):
    scen = _FakeScen(close_point=None)
    _install(monkeypatch, scen)
    # 等广告出现的时间调小，避免真等 8 秒（假时钟下 1 秒/次）
    assert ab.watch_ad(scen, appear_wait=0.01) is False
    assert scen.clicks == []
