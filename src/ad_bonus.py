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

**通用广告流程**：`watch_ad()` / `confirm_reward()` 不只服务打工结算——衣橱
「看广告礼包」（`scenarios/daily_gift.py`）复用同一套「倒计时 → 播完横幅 / 点 X
→ 奖励弹层」处理。通用版额外认「恭喜已获得奖励」横幅、优先按 X 的
content-desc 定位、奖励弹层优先按全屏节点 `关闭奖励弹窗` 关闭；没有倒计时文字
的广告会一直等到播完横幅（或超时）再点 X，避免提前关掉白丢奖励。
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
# 广告播完时的横幅文案（点 X **之前**就出现；与点 X 之后的「恭喜获得」弹层不同）
AD_DONE_KEY = '恭喜已获得奖励'
# 广告页右上角 X / 衣橱奖励弹层的定位见 src/locators.py 的 'ad_close' /
# 'reward_popup'（那边不能反向 import 本模块，字符串各写一份，注释互相指认）。
# 关闭 X 的 x 比例（两机型实测一致）；y 取倒计时文案那行的 y
AD_CLOSE_X_PCT = 0.9417
# 倒计时文案识别不到时的 y 兜底比例
AD_CLOSE_Y_PCT = 0.1057
# 单条广告最长等待（秒）：打工补贴的广告正常 13~16 秒；每日福利的广告实测约
# 30 秒（用户记忆），留足缓冲。超时也会点 X，再按奖励是否到账判定成败。
AD_MAX_WAIT = 60
# 点开看视频后等广告出现的最长时间；等不到说明没进广告页（网络/无广告库存）
AD_APPEAR_WAIT = 8
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
        if watch_ad(scen, prefix):
            log(f'{prefix}领取成功，+{bonus} 金币')
            return bonus
        log(f'{prefix}第 {attempt} 次广告没拿到奖励'
            + ('，重试一次' if attempt < retry else ''))
        time.sleep(1.5)
    return 0


def watch_ad(scen, prefix: str = '', *, max_wait: float = AD_MAX_WAIT,
             appear_wait: float = AD_APPEAR_WAIT) -> bool:
    """等广告倒计时走完 → 点右上角 X → 关掉奖励弹层。返回是否拿到奖励。

    适用于所有激励视频（打工结算页的「看视频 +N」补贴、衣橱「看广告礼包」）：
    点开广告后左上角是「观看 N 秒后即可获得奖励」倒计时；播完变成
    「恭喜已获得奖励」（或直接弹奖励）。右上角 X 有 content-desc 时优先点控件，
    没有才退回「倒计时那行的 y + 固定 x 比例」坐标。

    scen 需要提供：`screen()`、`click(x, y)`、`see(name)`、`dev.window_size()`。
    """
    w, h = scen.dev.window_size()
    close_y = int(AD_CLOSE_Y_PCT * h)
    # 先等广告页出现（等不到说明点击没生效/没有广告库存，别白等 max_wait 秒）。
    # 少数广告不显示倒计时但有 X 按钮，兜底按 X 的 content-desc 认。
    deadline = time.time() + appear_wait
    appeared = False
    saw_countdown = False
    while time.time() < deadline:
        time.sleep(1.0)
        for t, _x, y, _s in ocr_screen(scen.screen()):
            if _AD_WATCH_RE.search(t):
                close_y = y
                saw_countdown = appeared = True
                break
            if AD_DONE_KEY in t:
                appeared = True
                break
        if appeared:
            break
    if not appeared and scen.see('ad_close'):
        appeared = True
    if not appeared:
        log(f'{prefix}没等到广告页（无广告库存/点击未生效）')
        return False
    # 等倒计时走完：文案消失 = 播完（倒计时数字递减，不能只看首帧的 N）。
    # 少数广告不显示倒计时文字（只靠 X 按钮认出来的）：不能"没看到倒计时"就
    # 当成播完，必须等播完横幅/奖励弹层或超时，否则会提前点 X 白丢奖励。
    saw_done = False
    misses = 0  # 连续没识别到倒计时文案的次数
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(1.2)
        texts = ocr_screen(scen.screen())
        if any(AD_DONE_KEY in t for t, *_ in texts):
            saw_done = True
            break  # 播完横幅已出现（点 X 之后才会弹「恭喜获得」明细）
        if any(REWARD_KEY in t or REWARD_DISMISS_KEY in t for t, *_ in texts):
            break  # 奖励弹层已经出现（有的广告直接发奖）
        hit = None
        for t, _x, y, _s in texts:
            m = _AD_WATCH_RE.search(t)
            if m:
                hit = (int(m.group(1)), y)
                break
        if hit is None:
            if saw_countdown:
                # 连续两次认不到才当作播完：长广告（30 秒）里 OCR 偶尔漏读一行，
                # 一次漏读就点 X 会提前关掉、白丢奖励。
                misses += 1
                if misses >= 2:
                    break
            continue   # 没出现过倒计时：继续等播完横幅/奖励弹层，别提前点 X
        saw_countdown = True
        misses = 0
        close_y = hit[1]
        if hit[0] <= 1:
            break
    log(f'{prefix}广告结束，点右上角关闭领奖励')
    hit = scen.see('ad_close')
    if hit:
        scen.click(hit[0], hit[1])
    else:
        scen.click(int(AD_CLOSE_X_PCT * w), close_y)
    time.sleep(1.8)
    # 有的广告播完横幅已经算成功（「恭喜已获得奖励」），即使奖励弹层 OCR 没抓到
    return confirm_reward(scen, prefix) or saw_done


def confirm_reward(scen, prefix: str = '') -> bool:
    """确认奖励到账并关掉弹层（「恭喜获得」+「点击空白处关闭」）。

    「已补贴」/「恭喜已获得奖励」也算成功（OCR 偶尔漏掉弹层文字）。
    弹层若是全屏节点 `关闭奖励弹窗`（衣橱每日福利/广告礼包），点它下沿的空白区
    关闭；否则退回打工结算页实测的顶部安全点。
    """
    deadline = time.time() + 8
    seen = False
    checks = 0
    while time.time() < deadline:
        texts = [t for t, *_ in ocr_screen(scen.screen())]
        if any(REWARD_KEY in t or AD_DONE_KEY in t or REWARD_DISMISS_KEY in t
               for t in texts):
            seen = True
            break
        if any(SUBSIDIZED_KEY in t for t in texts):
            # 没抓到弹层文字但状态已变：也算领到了（OCR 偶尔漏「恭喜获得」）
            seen = True
            break
        # 弹层可能只有节点描述、OCR 还没读出文字：隔一次查一下控件（dump 较慢）
        checks += 1
        if checks % 2 == 0 and scen.see('reward_popup'):
            seen = True
            break
        time.sleep(0.8)
    if not seen:
        return False
    # 弹层挡住页面，点弹层外的安全区关掉它
    w, h = scen.dev.window_size()
    if scen.see('reward_popup'):
        # 全屏节点中心可能落在奖励卡片上，点它的下沿空白区（实测 0.83h 可关）
        scen.click(w // 2, int(h * 0.83))
    else:
        scen.click(int(DISMISS_POINT[0] * w), int(DISMISS_POINT[1] * h))
    time.sleep(0.6)
    return True
