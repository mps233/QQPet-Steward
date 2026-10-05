"""打工结算页上的「看视频 +N」金币补贴领取流程。

**每次打工（含雇佣好友打工）结束都在结算页上顺手领一次**：打工的结算页标题就是
「工资明细」，上面就带着这个入口（带「限时补贴」角标），不点就白丢一笔。
挂载点是 `DeviceScenario.claim_settlement_ad_bonus()`，在三处收尾路径上调用
（`_detect_settlement` / `finish_pending` / `wait_end`，都在点 quit 之前）。
开关：`schedule.claim_ad_bonus`。

页面文案（2026-09-29 真机实测）：

    未领 = 灰色锁图标 +「看视频 +49」+ 右上角「限时补贴」角标
    已领 = 角标变「已补贴」、卡片变「金币 +49」

点「看视频 +N」→ 全屏激励视频广告（穿山甲/优量汇）：左上「观看 15 秒后即可
获得奖励」倒计时（**会递减**），右上两个圆形按钮（静音 + 关闭 X）。等满倒计时
点 X → 回明细页，弹「恭喜获得 +总额」+ 金币图标 +「点击空白处关闭」。

关闭 X 的定位：实测 (1017, 255)，而同一行的倒计时文案 OCR 落在 (300, 258) ——
**两者 y 几乎相同**，所以用「倒计时文案的 y + 固定 x 比例 0.9417」定位，比写死
坐标稳（不同机型状态栏高度不同，y 比例会漂）。这个 x 比例在两台不同机型/分辨率
的截图上完全一致，已验证。

广告有约 1/6 概率点完 X 仍是未领状态（平台没回执/加载失败）→ 重试一次；
重试前先复查页面是否已变「已补贴」（变了说明上一次其实领到了，别重复看广告）。
"""
from __future__ import annotations

import re
import time

from .locators import ocr_screen
from .progress import log

# 「看视频 +49」（未领卡片文字）；兼容官方另一种文案「看视频获得49金币」
_AD_TEXT_RE = re.compile(r'看视频[^0-9\n]{0,8}?(\d{1,6})')
# 已领标记（卡片角标）
SUBSIDIZED_KEY = '已补贴'
# 广告页左上角倒计时：「观看 15 秒后即可获得奖励」
_AD_WATCH_RE = re.compile(r'观看\s*(\d{1,3})\s*秒')
# 奖励到账弹层
REWARD_KEY = '恭喜获得'
REWARD_DISMISS_KEY = '点击空白处关闭'
# 关闭 X 的 x 比例（两机型实测一致）；y 取倒计时文案那行的 y
AD_CLOSE_X_PCT = 0.9417
# 倒计时文案识别不到时的 y 兜底比例
AD_CLOSE_Y_PCT = 0.1057
# 单条广告最长等待（秒）：正常 13~15 秒，留足缓冲
AD_MAX_WAIT = 40
# 点开看视频后等广告出现的最长时间；等不到说明没进广告页（网络/无广告库存）
AD_APPEAR_WAIT = 6
# 同一条补贴最多试几次
AD_RETRY = 2
# 关掉奖励弹层时点的位置（弹层外的安全区：标题行一带，不碰"分享/继续打工"按钮）
DISMISS_POINT = (0.5, 0.13)


def claim_ad_bonus(scen, *, tag: str = '', retry: int = AD_RETRY) -> int:
    """在当前页（工资明细 / 打工结算页）领「看视频 +N」补贴。

    返回领到的金币数；**0 = 没领到**（已补贴 / 没有入口 / 广告没给奖励）。
    只记日志、不抛异常：调用方多在收尾路径上，不能被它挡住。

    scen 需要提供：`screen()`、`click(x, y)`、`dev.window_size()`。
    """
    prefix = f'{tag}: ' if tag else ''
    last_bonus = 0
    for attempt in range(1, retry + 1):
        try:
            texts = ocr_screen(scen.screen())
        except Exception as e:  # noqa: BLE001 - 截图抖动当"没有入口"处理
            log(f'{prefix}页面识别失败（{type(e).__name__}），跳过补贴')
            return 0
        if any(SUBSIDIZED_KEY in t for t, *_ in texts):
            if attempt > 1 and last_bonus:
                # 上一次其实领到了，只是没抓到奖励弹层
                return last_bonus
            return 0
        target = None
        for t, x, y, _s in texts:
            m = _AD_TEXT_RE.search(t)
            if m:
                target = (x, y, int(m.group(1)))
                break
        if target is None:
            if attempt < retry:
                # 明细页标题先出来、补贴卡片后渲染，紧接着 OCR 可能两者都没读到
                # （实测踩过：判成"没有入口"直接跳过，白丢一笔）。稍等重看一次。
                log(f'{prefix}页面还没渲染出补贴卡片，稍等重看')
                time.sleep(1.5)
                continue
            log(f'{prefix}没有"看视频"补贴入口（已过期/非打工页），跳过')
            return 0
        x, y, bonus = target
        last_bonus = bonus
        log(f'{prefix}点"看视频 +{bonus}"看广告领补贴（第 {attempt}/{retry} 次）')
        scen.click(x, y)
        if _watch_ad(scen, prefix):
            log(f'{prefix}领取成功，+{bonus} 金币')
            return bonus
        log(f'{prefix}第 {attempt} 次广告没拿到奖励'
            + ('，重试一次' if attempt < retry else ''))
        time.sleep(1.5)
    return 0


def _watch_ad(scen, prefix: str) -> bool:
    """等广告倒计时走完 → 点右上角 X → 关掉奖励弹层。返回是否拿到奖励。"""
    w, h = scen.dev.window_size()
    close_y = int(AD_CLOSE_Y_PCT * h)
    # 先等广告页出现（等不到说明点击没生效/没有广告库存，别白等 40 秒）
    deadline = time.time() + AD_APPEAR_WAIT
    appeared = False
    while time.time() < deadline:
        time.sleep(1.0)
        for t, _x, y, _s in ocr_screen(scen.screen()):
            if _AD_WATCH_RE.search(t):
                close_y = y
                appeared = True
                break
        if appeared:
            break
    if not appeared:
        log(f'{prefix}没等到广告页（无广告库存/点击未生效）')
        return False
    # 等倒计时走完：文案消失 = 播完（倒计时数字递减，不能只看首帧的 N）
    deadline = time.time() + AD_MAX_WAIT
    while time.time() < deadline:
        time.sleep(1.2)
        texts = ocr_screen(scen.screen())
        if any(REWARD_KEY in t or REWARD_DISMISS_KEY in t for t, *_ in texts):
            break  # 奖励弹层已经出现（有的广告直接发奖）
        hit = None
        for t, _x, y, _s in texts:
            m = _AD_WATCH_RE.search(t)
            if m:
                hit = (int(m.group(1)), y)
                break
        if hit is None:
            break  # 倒计时文案消失 = 广告播完
        close_y = hit[1]
        if hit[0] <= 1:
            break
    log(f'{prefix}广告结束，点右上角关闭领奖励')
    scen.click(int(AD_CLOSE_X_PCT * w), close_y)
    time.sleep(1.8)
    return _confirm_reward(scen, prefix)


def _confirm_reward(scen, prefix: str) -> bool:
    """确认奖励到账并关掉弹层（「恭喜获得」+「点击空白处关闭」）。"""
    deadline = time.time() + 8
    seen = False
    while time.time() < deadline:
        texts = [t for t, *_ in ocr_screen(scen.screen())]
        if any(REWARD_KEY in t for t in texts):
            seen = True
            break
        if any(SUBSIDIZED_KEY in t for t in texts):
            # 没抓到弹层文字但状态已变：也算领到了（OCR 偶尔漏「恭喜获得」）
            seen = True
            break
        time.sleep(0.8)
    if not seen:
        return False
    # 弹层挡住页面，点弹层外的安全区关掉它
    w, h = scen.dev.window_size()
    scen.click(int(DISMISS_POINT[0] * w), int(DISMISS_POINT[1] * h))
    time.sleep(0.6)
    return True
