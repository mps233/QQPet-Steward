"""衣橱「道具」页的每日福利 + 看广告礼包领取。

2026-11 真机实测（1080x2412）的完整链路：

  主页面右上角「装扮」按钮（content-desc="装扮"）
    → 衣橱（顶部页签「服饰 / 背景 / 道具」，canvas 自绘，只能 OCR 点「道具」）
    → 道具页两张大卡（content-desc 可读，物品名随活动会变，只锚前缀）：
        左：`领取 <物品> x 1`        = 每日福利（免费）
        右：`看视频领取 <物品> x 3`  = 看广告礼包
    → 领完后两张卡都变成 `<物品> x N，已领取`

两条领取流程：

  免费：点卡片 → 弹「恭喜获得 <物品> x N」+「点击空白处关闭」
        （控件树里是全屏节点 content-desc="关闭奖励弹窗"）→ 点空白处关闭。

  广告：点卡片 → 激励视频（左上「观看 N 秒后即可获得奖励」，实测本条 16 秒、
        用户记忆约 30 秒，按 60 秒等）→ 播完变「恭喜已获得奖励」→ 点右上角
        X（content-desc="点击退出关闭视频"）→ 回道具页弹「恭喜获得 …」→ 关闭。

判断「今天领没领过」**只看卡片自己的 content-desc**：未领的免费卡以「领取 」开头、
未领的广告卡含「看视频领取」；领取后都会变成「…，已领取」。道具页签上的红点
只是提示、不是控件，不参与判定。

调度：任务队列的 `daily_gift` 任务（daily 触发，默认 00:05）。两个都处理完
（含"本来就已经领过"）后 `run()` 返回 False，调度器标记当天不可继续，次日
跨天自动重开。页面打不开抛异常，走调度器既有的重试与恢复；广告没库存/没发奖
抛 `TaskDeferred` 延后 30 分钟再试（不为一次广告网络抖动去重启设备）。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from src.ad_bonus import REWARD_DISMISS_KEY, REWARD_KEY, watch_ad
from src.locators import ocr_screen
from src.progress import log
from src.scenario import DeviceScenario, TaskDeferred

# 等衣橱页渲染出「道具」页签的时间（慢设备/首次打开可能 2~3 秒）
TAB_WAIT = 4
# 等道具页两张大卡渲染出来的时间
CARD_WAIT = 6
# 点免费卡后等奖励弹层出现的时间
REWARD_WAIT = 8
# 广告关掉后等卡片变成「已领取」的时间
AD_CLAIMED_WAIT = 6
# 看广告最多试几次（第一次没给奖励时重试一次）
AD_RETRY = 2
# 广告最长等待（秒）：正常 16 秒，用户记忆约 30 秒，留足缓冲
AD_MAX_WAIT = 60
# 广告页出现的最长等待（秒）
AD_APPEAR_WAIT = 8
# 领取失败（广告没库存/没发奖、点了免费卡却没弹奖励）后的下次尝试间隔（分钟）：
# 用 TaskDeferred 让路，**不抛失败**——否则会走"回主页面重试 → 重启设备恢复"，
# 为一次广告网络抖动重启手机不值得。页面打不开这类导航问题仍然抛异常走恢复链路。
RETRY_MINUTES = 30


class DailyGiftScenario(DeviceScenario):
    """主页面 → 装扮 → 道具 → 领免费礼包 + 看广告礼包。"""

    # ---- 入口 ----

    def _wait_see(self, name: str, timeout: float):
        """轮询 see(name)，命中返回定位，超时返回 None（页面渲染慢时用）。"""
        deadline = time.time() + max(0.1, timeout)
        while time.time() < deadline:
            hit = self.see(name)
            if hit:
                return hit
            time.sleep(0.7)
        return None

    def _open_items_page(self) -> None:
        """从主页面打开「装扮 → 道具」页；失败重试几次后抛异常。"""
        for attempt in range(1, 4):
            hit = self._wait_see('wardrobe', 3)
            if not hit:
                log(f'每日福利: 主页面没找到「装扮」按钮（第 {attempt}/3 次），回主页面重试')
                self.ensure_main_page()
                time.sleep(1.0)
                continue
            log(f'每日福利: 点「装扮」({hit[0]}, {hit[1]})')
            self.click(hit[0], hit[1])
            time.sleep(1.2)
            tab = self._wait_see('wardrobe_items_tab', TAB_WAIT)
            if not tab:
                # **不能直接 go_back()**：万一「装扮」那一下被弹窗吞掉、页面还在
                # 主页面，系统返回会把 QQ 退出（主页面点 back = 退游戏）。
                # ensure_main_page 先认 main_sign，已经在主页面就不按 back。
                log(f'每日福利: 衣橱里没识别到「道具」页签（第 {attempt}/3 次），'
                    '先确保回主页面再重试')
                self.ensure_main_page()
                time.sleep(1.0)
                continue
            log(f'每日福利: 点「道具」页签 ({tab[0]}, {tab[1]})')
            self.click(tab[0], tab[1])
            time.sleep(1.2)
            if self._wait_cards(CARD_WAIT):
                return
            log(f'每日福利: 道具页卡片没出现（第 {attempt}/3 次），重试')
        raise RuntimeError('每日福利: 未能打开衣橱「道具」页')

    def _read_cards(self):
        """读道具页的两张卡：返回 (未领免费, 未领广告, 免费已领, 广告已领)。

        控件树 dump 一次，在 XML 里直接解析（不再用 `see` 逐个 XPath）：
          - 免费卡未领：content-desc 以「领取 」开头（如「领取 高钙奶 x 1」）
          - 广告卡未领：content-desc 含「看视频领取」（如「看视频领取 高钙奶 x 3」）
          - 已领：content-desc 含「已领取」（如「高钙奶 x 1，已领取」）
        「已领」必须按**卡片在屏幕左/右半边**区分免费/广告：只认大尺寸节点
        （排除别处的小字提示），x 中心 < 35% 屏宽 = 左卡（免费），否则右卡（广告）。
        """
        source = self.dev.hierarchy()  # uiautomator2 PageSource（失败返回 None）
        if source is None:
            return None, None, False, False
        w, h = self.dev.window_size()
        free = ad = None
        claimed_free = claimed_ad = False
        # PageSource.root 会把每个节点的 tag 改成它的 class 名（不再叫 'node'），
        # 所以遍历全部元素、按 content-desc 过滤，不能 iter('node')。
        for node in source.root.iter():
            desc = node.get('content-desc') or ''
            if not desc:
                continue
            b = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',
                         node.get('bounds') or '')
            if not b:
                continue
            x1, y1, x2, y2 = (int(b.group(i)) for i in (1, 2, 3, 4))
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            if desc.startswith('领取 '):
                free = free or (cx, cy, 1.0)
            elif '看视频领取' in desc:
                ad = ad or (cx, cy, 1.0)
            elif '已领取' in desc:
                # 只认卡片尺寸的节点，避免把横幅/提示里的小字「已领取」算进来
                if x2 - x1 < w * 0.12 or y2 - y1 < h * 0.05:
                    continue
                if cx < w * 0.35:
                    claimed_free = True
                else:
                    claimed_ad = True
        return free, ad, claimed_free, claimed_ad

    def _wait_cards(self, timeout: float = CARD_WAIT) -> bool:
        """等道具页渲染出卡片（未领/已领都算）；超时返回 False。"""
        deadline = time.time() + max(0.1, timeout)
        while time.time() < deadline:
            free, ad, claimed_free, claimed_ad = self._read_cards()
            if free or ad or claimed_free or claimed_ad:
                return True
            time.sleep(0.8)
        return False

    # ---- 免费礼包 ----

    def _claim_free(self) -> None:
        free, _ad, claimed_free, _claimed_ad = self._read_cards()
        if not free and not claimed_free:
            # 免费卡既没未领态也没已领态：可能还没渲染完，不能当成"已领过"。
            # （只看到广告卡不代表免费卡领过——两张卡是各自独立的 content-desc）
            if self._wait_cards(2):
                free, _ad, claimed_free, _claimed_ad = self._read_cards()
            if not free and not claimed_free:
                raise TaskDeferred(
                    datetime.now() + timedelta(minutes=RETRY_MINUTES),
                    f'道具页没读到免费礼包卡，{RETRY_MINUTES} 分钟后再试')
        if not free:
            log('每日福利: 免费礼包今天已经领过了（卡片是「已领取」态），跳过')
            return
        log(f'每日福利: 点免费礼包 ({free[0]}, {free[1]})')
        self.click(free[0], free[1])
        if self._wait_reward_popup(REWARD_WAIT):
            self._dismiss_reward_popup()
            log('每日福利: 免费礼包领取成功')
            return
        # 弹层没抓到（OCR/控件都可能抖）：再读一次卡片，已变成「已领取」也算成功
        time.sleep(1.0)
        free2, _ad2, claimed_free2, _claimed_ad2 = self._read_cards()
        if not free2 and claimed_free2:
            log('每日福利: 免费礼包已领取（卡片已变「已领取」）')
            return
        raise TaskDeferred(
            datetime.now() + timedelta(minutes=RETRY_MINUTES),
            f'免费礼包点击后没弹奖励，{RETRY_MINUTES} 分钟后再试')

    def _wait_reward_popup(self, timeout: float = REWARD_WAIT) -> bool:
        """等奖励弹层出现（OCR 文案或全屏节点 `关闭奖励弹窗` 任一命中）。"""
        deadline = time.time() + max(0.1, timeout)
        checks = 0
        while time.time() < deadline:
            texts = [t for t, *_ in ocr_screen(self.screen())]
            if any(REWARD_KEY in t or REWARD_DISMISS_KEY in t for t in texts):
                return True
            checks += 1
            if checks % 2 == 0 and self.see('reward_popup'):
                return True
            time.sleep(0.7)
        return False

    def _dismiss_reward_popup(self) -> None:
        """关掉奖励弹层：全屏节点存在时点它下沿的空白区（实测 0.83h 可关）。"""
        w, h = self.dev.window_size()
        if self.see('reward_popup'):
            self.click(w // 2, int(h * 0.83))
        else:
            # 打工结算页实测的顶部安全点（弹层外的标题行）
            self.click(w // 2, int(h * 0.13))
        time.sleep(0.6)

    # ---- 看广告礼包 ----

    def _claim_ad(self) -> None:
        for attempt in range(1, AD_RETRY + 1):
            _free, ad, _claimed_free, claimed_ad = self._read_cards()
            if not ad and not claimed_ad:
                # 广告卡既没未领态也没已领态：可能还没渲染完，不能当成已领
                if self._wait_cards(2):
                    _free, ad, _claimed_free, claimed_ad = self._read_cards()
            if not ad:
                if claimed_ad:
                    if attempt == 1:
                        log('每日福利: 看广告礼包今天已经领过了'
                            '（卡片是「已领取」态），跳过')
                    else:
                        log('每日福利: 看广告礼包已领取')
                    return
                raise TaskDeferred(
                    datetime.now() + timedelta(minutes=RETRY_MINUTES),
                    f'道具页没读到看广告礼包卡，{RETRY_MINUTES} 分钟后再试')
            log(f'每日福利: 点看广告礼包 ({ad[0]}, {ad[1]})'
                f'（第 {attempt}/{AD_RETRY} 次）')
            self.click(ad[0], ad[1])
            ok = watch_ad(self, '每日福利: ', max_wait=AD_MAX_WAIT,
                          appear_wait=AD_APPEAR_WAIT)
            if ok and self._wait_ad_claimed(AD_CLAIMED_WAIT):
                log('每日福利: 看广告礼包领取成功')
                return
            if ok:
                log('每日福利: 广告看完但卡片仍显示未领取，重试')
            else:
                log('每日福利: 广告没有给奖励，重试')
            if attempt < AD_RETRY:
                self._recover_to_items_page()
                time.sleep(1.0)
        raise TaskDeferred(
            datetime.now() + timedelta(minutes=RETRY_MINUTES),
            f'看广告礼包没拿到奖励（广告无库存/未发奖），{RETRY_MINUTES} 分钟后再试')

    def _wait_ad_claimed(self, timeout: float = AD_CLAIMED_WAIT) -> bool:
        """广告关闭后等广告卡变成「已领取」。"""
        deadline = time.time() + max(0.1, timeout)
        while time.time() < deadline:
            _free, ad, _claimed_free, claimed_ad = self._read_cards()
            if not ad and claimed_ad:
                return True
            time.sleep(0.7)
        return False

    def _recover_to_items_page(self) -> None:
        """广告失败后把页面恢复到道具页（可能停在广告页/奖励弹层/未知页）。"""
        for _ in range(3):
            hit = self.see('ad_close')
            if hit:
                log('每日福利: 关闭残留的广告页')
                self.click(hit[0], hit[1])
                time.sleep(1.5)
                continue
            if self.see('reward_popup'):
                self._dismiss_reward_popup()
                continue
            free, ad, claimed_free, claimed_ad = self._read_cards()
            if free or ad or claimed_free or claimed_ad:
                return
            # 页面不对（可能被退回主页面了）：回主页面重新进道具页
            log('每日福利: 当前不在道具页，回主页面重新进入')
            self.ensure_main_page()
            self._open_items_page()
            return
        raise TaskDeferred(
            datetime.now() + timedelta(minutes=RETRY_MINUTES),
            f'无法从广告/弹层恢复到道具页，{RETRY_MINUTES} 分钟后再试')

    # ---- 收尾 ----

    def _leave_wardrobe(self) -> None:
        """从道具页/广告页/奖励弹层逐层退回主页面（异常路径也要能收尾）。"""
        for _ in range(4):
            try:
                if self.see('main_sign'):
                    return
            except Exception:  # noqa: BLE001 - 收尾阶段识别失败不挡返回
                pass
            hit = self.see('ad_close')
            if hit:
                self.click(hit[0], hit[1])
                time.sleep(1.5)
                continue
            if self.see('reward_popup'):
                self._dismiss_reward_popup()
                continue
            # 衣橱/道具页：按配置的返回方式退一层（系统返回实测可直接回主页面）
            self.go_back()
            time.sleep(1.2)
        try:
            self.ensure_main_page()
        except Exception as e:  # noqa: BLE001 - 收尾失败交给调度器的回主页面逻辑
            log(f'每日福利: 收尾回主页面失败（不影响任务结果）: {e}')

    # ---- 任务入口 ----

    def run(self, max_times: int | None = None, max_rounds: int = 0) -> bool:
        """一轮 = 领免费礼包 + 看广告礼包。处理完返回 False（当天不再跑）。"""
        log('每日福利: 开始（主页面 → 装扮 → 道具）')
        self.ensure_main_page()
        try:
            self._open_items_page()
            self._claim_free()
            self._claim_ad()
        finally:
            self._leave_wardrobe()
        log('每日福利: 完成（当天不再执行）')
        return False
