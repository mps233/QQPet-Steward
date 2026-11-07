"""摸摸场景：只摸有火花的好友，碰到没有火花的好友就停止。

玩法（2026-11 官方）：访问“火花好友”的宠物家，摸摸（点宠物）会触发
金币彩蛋；火花关系越多，彩蛋越多。好友列表按互动排序，火花好友连续排在
前面，后面是没有火花的，所以从第一个好友开始沿底部好友卡依次往后走，
一旦当前好友页右上角的「火花」按钮显示“待重燃”就停止，不再进后面的好友家。

流程：
1. 主页面点「好友」开好友列表，并选「互动」排序（保证火花好友连续）
2. 点第一个「访问」进好友家
3. 读右上角「火花」按钮：
   - “N天 / 火花点亮中” = 有火花 → 点宠物（默认 3 下）
   - “待重燃” = 没火花 → 按规则停止
4. 用页面底部的好友卡切到当前好友右边的下一个，重复第 3 步
5. 回主页面

定位：
- 好友列表里的「访问」按钮有 content-desc（visit）
- 好友页右上角「火花」按钮有 content-desc（friend_spark），
  按钮里的文字（N天 / 待重燃）用 OCR 读，已实测两种状态互斥
- 底部好友卡 content-desc 形如 "好友 <主人昵称>"（visit_friend_item）
- 好友家的宠物没有控件，点画面中央的宠物（720x1280 参考坐标）

只接 task_queue 引擎（与 gift_bag 相同）。一次完整扫描后 run() 返回 False，
调度器会把任务标成“当天不可继续”，明天自动重开。
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import PROJECT_ROOT
from src.jsonio import read_json, write_json
from src.locators import LOCATORS, see_bounds
from src.ocr import ocr_texts
from src.progress import log
from src.scenario import CLICK_INTERVAL, NAV_TIMEOUT, DeviceScenario

# 当天完成标记：调度器重启会重建内存里的 task.dead，不落盘的话每次重启
# 都会把 7 位火花好友重新摸一遍（一次完整扫描约 1 分钟、还会重复点宠物）。
TOUCH_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'touch_progress.json'

# 好友页底部好友卡（content-desc "好友 <主人昵称>"）
FRIEND_ITEM_XPATH = LOCATORS['visit_friend_item']['xpath'][0]

# 好友家宠物位置（720x1280 参考坐标）：宠物站在画面中央偏下的垫子上。
# 体、头两个点交替点，兼容不同宠物/房间的站位差异；实测两个点都能触发摸摸动作。
PET_TAP_POINTS = ((360, 700), (360, 630))
# 每次点宠物后的等待（秒）：触摸动作有动画，连点太快容易被合并成一次
PET_TAP_INTERVAL = 1.0
# 切换好友后等页面渲染的时间（秒）
FRIEND_SWITCH_SETTLE = 1.2
# 火花按钮文字：有火花显示 "54天"（N 天）或 "火花点亮中"，没有火花显示 "待重燃"
SPARK_DAYS_RE = re.compile(r'\d+\s*[天夭]')


def spark_state_from_texts(texts) -> bool | None:
    """从「火花」按钮的 OCR 文本判断好友是否有火花。

    True = 有火花（N天 / 火花点亮中）；False = 没有火花（待重燃）；
    None = 没读到有效状态（调用方保守停止）。
    """
    text = ''.join(str(t or '') for t in texts)
    text = ''.join(text.split())          # OCR 可能把 "54 天" 拆开/带空格
    if not text:
        return None
    if '待重燃' in text:
        return False
    if SPARK_DAYS_RE.search(text):
        return True
    if '火花点亮中' in text or '点亮中' in text:
        # 「火花点亮中」是游戏内的活跃态文案（pet_home 源码里的 status==3 分支）
        return True
    return None


def _today_text() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def touch_progress(path=None) -> dict:
    """读取当天完成标记（不存在/损坏返回空字典）。"""
    p = Path(path) if path else TOUCH_PROGRESS_FILE
    data = read_json(p, {}) or {}
    return data if isinstance(data, dict) else {}


def touch_done_today(path=None, today: str | None = None) -> bool:
    """今天是否已经完整扫过一轮（调度器重启后据此跳过，避免重复摸）。"""
    day = today or _today_text()
    return str(touch_progress(path).get('date') or '') == day


def save_touch_progress(petted: int, path=None, today: str | None = None) -> None:
    """记下今天已完成（原子写；失败由调用方只记日志）。"""
    p = Path(path) if path else TOUCH_PROGRESS_FILE
    write_json(p, {
        'date': today or _today_text(),
        'petted': int(petted),
        'finished_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }, indent=1)


_NAME_JUNK_RE = re.compile(r'[\s　…\.·•\-—~～]+')


def _norm_name(text: str) -> str:
    """昵称归一化：去空白、省略号、点、波浪线等对匹配无意义的符号。"""
    return _NAME_JUNK_RE.sub('', str(text or ''))


def same_name(a: str, b: str) -> bool:
    """归一化后是否**完全相同**（用于"页面还停在上一个好友"这类判断）。"""
    na, nb = _norm_name(a), _norm_name(b)
    return bool(na) and na == nb


def names_match(a: str, b: str) -> bool:
    """好友昵称模糊匹配：OCR 常把长昵称截断成前半段，按前缀认。"""
    a, b = _norm_name(a), _norm_name(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 2 and long.startswith(short)


def current_card_index(cards, profile_texts) -> int | None:
    """在底部好友卡里找当前好友的下标：cards=[昵称...]，profile_texts=资料卡 OCR。

    优先精确匹配：昵称互相是前缀时（"凌晨" / "凌晨吃早饭~"），截断 OCR 的
    "凌晨" 可能同时命中两张卡，精确那一个才是当前好友。精确都不中再按前缀。
    返回 None = 没对上（页面结构/昵称识别异常，调用方停止本轮）。
    """
    normalized = [(i, _norm_name(name)) for i, name in enumerate(cards)]
    # 1) 精确匹配（去符号后相等）
    for text in profile_texts:
        norm = _norm_name(text)
        if not norm:
            continue
        for i, name in normalized:
            if name == norm:
                return i
    # 2) 前缀匹配：OCR 截断时 "喵帕…" 认成 "喵帕斯～"。
    #    如果有多个卡都以前缀命中，无法判断是哪一张（"喵帕斯～" / "喵帕斯～二号"），
    #    返回 None 交给金色描边/重试，不要按"更长的那张"瞎猜（会点错下一位）。
    for text in profile_texts:
        matches = [i for i, name in normalized if names_match(text, name)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


# 摸摸保护上限：好友卡异常（识别错位/页面卡住）时不要把一天耗在来回切好友上
MAX_FRIENDS_PER_RUN = 50
# QQ 允许两个好友用同一个昵称；同一个昵称最多按两位不同好友处理，
# 出现第 3 次才判定为"在两张卡之间来回切"（真正的循环由 MAX 兜底）。
MAX_SAME_NAME = 2

# 当前好友卡的金色描边（好友页底部选中的那张卡有金色边框，其它卡没有）。
# 用它判断"我在哪张卡上"比按昵称匹配稳：昵称可能被 OCR 截断、还可能存在
# "凌晨" 和 "凌晨吃早饭~" 这种前缀相同的两个好友（会把下标认错，来回切）。
_GOLD_RING_BAND = 8         # 720x1280 参考坐标下的周界带宽（按屏幕宽度等比缩放）


def _gold_border_pixels(screen, bounds) -> int:
    """卡片周界带里的"金色描边"像素数（选中卡有金边，未选中为 0）。"""
    import numpy as np
    x1, y1, x2, y2 = bounds
    h, w = screen.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return 0
    band = max(2, round(_GOLD_RING_BAND * w / 1080))
    crop = screen[y1:y2, x1:x2]
    r = crop[:, :, 0].astype(int)
    g = crop[:, :, 1].astype(int)
    b = crop[:, :, 2].astype(int)
    gold = (r > 170) & (g > 120) & (b < 130) & (r - b > 60) & (g - b > 30)
    ring = np.zeros_like(gold)
    ring[:band, :] = gold[:band, :]
    ring[-band:, :] = gold[-band:, :]
    ring[:, :band] = gold[:, :band]
    ring[:, -band:] = gold[:, -band:]
    return int(ring.sum())


def card_visited(name: str, visited) -> bool:
    """本次已摸过的好友卡里是否有它（**精确**比较，不做归一化/前缀匹配）。

    这里不能复用 names_match：真实好友里存在 "凌晨" 和 "凌晨吃早饭~"
    这种前缀名，前缀判断会把下一位正当成已摸过而提前停止。卡片昵称来自
    content-desc（游戏原生完整昵称，不是 OCR），本身准确，精确比较即可；
    OCR 截断只影响日志/兜底，真正的当前卡由金色描边判定。
    """
    target = str(name or '').strip()
    return any(str(v or '').strip() == target for v in visited)


def card_visit_count(name: str, visited) -> int:
    """同一昵称在本次已摸列表里出现了几次（用来区分"重名好友"和真循环）。"""
    target = str(name or '').strip()
    return sum(1 for v in visited if str(v or '').strip() == target)


class TouchScenario(DeviceScenario):
    """摸摸：沿好友列表摸有火花的好友，遇到没火花的就停止。"""

    def __init__(self, dev=None):
        super().__init__(dev)

    # ---- 配置 ----

    def _taps(self) -> int:
        """每个好友点几下宠物：运行时重读 cfg.touch，设置页改完下一轮生效。"""
        try:
            n = int(getattr(self.cfg.touch, 'taps_per_friend', 3) or 3)
        except (TypeError, ValueError):
            n = 3
        return max(1, min(20, n))

    # ---- 好友列表/好友页 ----

    def _open_friend_panel(self) -> None:
        """主页面 → 好友列表；选「互动」排序（火花好友连续排在最前面）。"""
        self.click_until_gone_or_see('main_friend_btn', 'visit', '打开好友列表')
        hit = None
        for attempt in range(1, 4):
            screen = self.screen()
            h = screen.shape[0]
            hit = self.see('friend_tab_interact', screen=screen)
            # 只认好友列表筛选行里的「互动」：整屏 OCR 可能撞到别处的同名文字
            if hit and 0.34 * h <= hit[1] <= 0.46 * h:
                break
            hit = None
            log(f'摸摸: 未找到好友列表的「互动」排序标签，等待重试 ({attempt}/3)')
            time.sleep(0.6)
        if not hit:
            # 火花好友"连续排在前面"依赖互动排序；认不出排序时不猜顺序，
            # 抛出交给执行器重试（否则可能按等级/战力顺序，第一位就没火花，
            # 整轮直接停掉且一个人都没摸）。
            raise RuntimeError('好友列表未找到「互动」排序标签，无法保证火花好友连续')
        log('摸摸: 选择「互动」排序')
        self.click(hit[0], hit[1])
        time.sleep(0.6)

    def _enter_first_friend(self) -> None:
        hit = self.see('visit')
        if not hit:
            raise RuntimeError('好友列表没有「访问」按钮（可能没有好友）')
        log('摸摸: 访问好友列表第一个')
        self.click(hit[0], hit[1])
        if not self._wait_friend_page():
            raise RuntimeError('进入好友宠物页超时')

    def _wait_friend_page(self) -> bool:
        """等好友页渲染。

        不能只等「火花」按钮：游戏在 SparkBrief 为 null / status=0（新好友、
        从未互动、没有火花）时会把 SparkBar 宽度设 0 **根本不渲染**该节点
        （pet_home 源码 d6/a.java、d6/r0.java），只等火花条会把这种好友页
        误判成加载失败 → 重试/recover（默认还会 adb reboot）。踩踩/已踩按钮
        在任何好友页都有，用它判断页面已加载。
        """
        for _ in range(NAV_TIMEOUT):
            source = self.dev.hierarchy()
            if (self.see('friend_spark', source=source)
                    or self.see('visit_step', source=source)
                    or self.see('visit_stepped', source=source)):
                time.sleep(0.8)
                return True
            time.sleep(CLICK_INTERVAL)
        return False

    def _friend_cards(self) -> list[tuple[str, int, int, tuple[int, int, int, int]]]:
        """底部好友卡：[(昵称, 中心x, 中心y, bounds)]，按从左到右排序。"""
        cards = []
        for e in self.dev.d.xpath(FRIEND_ITEM_XPATH).all():
            desc = str(e.attrib.get('content-desc') or '')
            name = desc[3:].strip() if desc.startswith('好友 ') else desc.strip()
            if not name:
                continue
            left, top, right, bottom = e.bounds
            cards.append((name, (left + right) // 2, (top + bottom) // 2,
                          (left, top, right, bottom)))
        cards.sort(key=lambda it: it[3][0])
        return cards

    def _friend_cards_wait(self, timeout: float = 3.0):
        """等底部好友卡渲染：好友页刚切过去时 xpath 可能短暂为空。"""
        deadline = time.time() + timeout
        cards = []
        while time.time() < deadline:
            cards = self._friend_cards()
            if cards:
                return cards
            time.sleep(0.5)
        return cards

    def _current_card_index(self, cards, profile: list[str]) -> tuple[int | None, str]:
        """判断当前好友卡下标：优先看金色描边，描边认不出再按昵称匹配。

        返回 (下标, 依据)；下标 None = 认不出（调用方停止本轮）。
        """
        if cards:
            screen = self.screen()
            counts = [_gold_border_pixels(screen, c[3]) for c in cards]
            best = max(range(len(counts)), key=counts.__getitem__)
            second = max([0] + counts[:best] + counts[best + 1:])
            # 选中卡的金边实测 ~4700 px，未选中 0；要求明显高于第二名，避免误认
            min_px = max(20, round(30 * screen.shape[1] / 1080))
            if counts[best] >= min_px and counts[best] >= second * 3:
                return best, f'金边({counts[best]}px)'
        return current_card_index([c[0] for c in cards], profile), '昵称'

    def _locate_current_card(self):
        """返回 (cards, 当前下标, 依据)；找不到就抛异常（交给执行器重试）。

        当前卡优先靠金色描边认，昵称只作兜底；页面刚渲染/换好友动画期间
        可能认不出，先等好友卡、再重试一轮再放弃。
        """
        cards = self._friend_cards_wait()
        if not cards:
            raise RuntimeError('好友页底部没有好友卡（页面未渲染完或结构变化）')
        profile = self._profile_texts()
        idx, basis = self._current_card_index(cards, profile)
        if idx is None:
            time.sleep(0.8)
            cards = self._friend_cards()
            profile = self._profile_texts()
            idx, basis = self._current_card_index(cards, profile)
        if idx is None or not cards:
            raise RuntimeError(f'无法确认当前好友卡（资料卡={profile!r}）')
        return cards, idx, basis

    def _profile_texts(self) -> list[str]:
        """好友页顶部资料卡（宠物资料按钮）第一行 OCR：当前好友的主人昵称。

        只取最上面一行（主人昵称），下面那行是宠物名，拿宠物名匹配底部好友卡
        会撞到别人家的宠物名。OCR 常把长昵称截断，所以配合 names_match 前缀比较。
        """
        screen = self.screen()
        bounds = see_bounds(self.dev, 'pet_profile')
        if bounds:
            x1, y1, x2, y2 = bounds
        else:
            h, w = screen.shape[:2]
            x1, y1, x2, y2 = int(w * 0.20), int(h * 0.05), int(w * 0.66), int(h * 0.10)
        results = ocr_texts(screen[y1:y2, x1:x2])
        if not results:
            return []
        results.sort(key=lambda r: r[2])
        top_y = results[0][2]
        return [t for t, _x, y, _s in results if y <= top_y + 36]

    def _profile_name(self) -> str:
        texts = self._profile_texts()
        return texts[0] if texts else ''

    def _read_spark_texts(self) -> tuple[list[str], bool]:
        """读好友页右上角「火花」按钮文字；返回 (texts, 按钮是否存在)。

        没有火花时游戏可能**不渲染**这个按钮（SparkBrief=null / status=0），
        所以"按钮不存在"本身是有意义的状态，不能只当成 OCR 失败。
        """
        bounds = see_bounds(self.dev, 'friend_spark')
        if not bounds:
            return [], False
        x1, y1, x2, y2 = bounds
        screen = self.screen()
        return [t for t, *_ in ocr_texts(screen[y1:y2, x1:x2])], True

    def _spark_state(self) -> bool | None:
        """读一次火花按钮并判断；按钮不存在返回 None（调用方需区分状态 0）。"""
        texts, has_button = self._read_spark_texts()
        if not has_button:
            return None
        state = spark_state_from_texts(texts)
        log(f'摸摸: 火花按钮 OCR={texts!r} -> '
            + ('有火花' if state is True else '没有火花' if state is False else '识别失败'))
        return state

    def _spark_state_checked(self) -> bool:
        """火花状态；OCR 瞬时失败先重试，仍读不出再抛异常交给执行器重试。

        两种"没有火花"的正常情况按停止处理、不重试：
        - 按钮存在但 OCR 读到「待重燃」（status=2）；
        - 按钮**根本不存在**（SparkBrief 为空 / status=0：新好友、从未互动），
          此时好友页仍有踩踩/已踩按钮，据此确认页面已加载。
        """
        texts: list[str] = []
        saw_button = False
        for attempt in range(1, 4):
            texts, has_button = self._read_spark_texts()
            saw_button = saw_button or has_button
            if has_button:
                state = spark_state_from_texts(texts)
                if state is not None:
                    log(f'摸摸: 火花按钮 OCR={texts!r} -> '
                        + ('有火花' if state else '没有火花'))
                    return state
            log(f'摸摸: 火花按钮 OCR={texts!r}'
                + ('' if has_button else '（按钮未渲染）')
                + f' 识别失败，等待重试 ({attempt}/3)')
            time.sleep(0.8)
        if not saw_button and (self.see('visit_step') or self.see('visit_stepped')):
            log('摸摸: 火花条未渲染（status 0 / 无火花），按没有火花停止')
            return False
        if any('加好友' in t for t in texts):
            log('摸摸: 好友页显示「加好友」（非好友/系统推荐），按没有火花停止')
            return False
        raise RuntimeError(f'火花状态识别失败（OCR={texts!r}，按钮={saw_button}）')

    # ---- 摸宠物 / 切好友 ----

    def _pet(self, taps: int) -> None:
        for i in range(taps):
            x, y = self.dev.rel(*PET_TAP_POINTS[i % len(PET_TAP_POINTS)])
            self.click(x, y)
            time.sleep(PET_TAP_INTERVAL)
        # 金币是服务端 push 的彩蛋，留一点时间让动画/入账走完
        time.sleep(0.6)

    def _advance_to_next(self, cards, idx: int, visited: list[str]) -> bool:
        """切到当前好友右边的好友卡。

        返回 False = 确认本轮结束（当前是最后一位 / 下一位本次已摸过）；
        切换点击失败抛异常，交给执行器重试（不要静默当成“今天做完了”）。
        """
        if idx + 1 >= len(cards):
            log('摸摸: 当前好友已是底部好友卡最后一位，结束本轮')
            return False
        next_name, x, y, _bounds = cards[idx + 1]
        if card_visit_count(next_name, visited) >= MAX_SAME_NAME:
            # 底部好友卡窗口/识别错位时下标可能往回跳；同名好友最多允许出现
            # MAX_SAME_NAME 次（QQ 允许重名），超过才是真的在两张卡之间来回切。
            log(f'摸摸: 下一位 {next_name} 本次已摸过 {MAX_SAME_NAME} 次，'
                '停止（防止来回切好友）')
            return False
        log(f'摸摸: 切换好友 {next_name}')
        prev_name = cards[idx][0]
        click_x, click_y = x, y
        for attempt in (1, 2):
            if attempt == 2:
                # 第一次点击后卡条可能已经重排（选中项换位），旧坐标会点到
                # 别的卡；补点前重新按完整昵称找目标卡的新位置。
                fresh = self._friend_cards()
                target = next((c for c in fresh
                               if str(c[0]).strip() == str(next_name).strip()), None)
                if target is None:
                    # 目标卡不在可见窗口里：可能第一次点击其实已经切过去了
                    if self._wait_profile_match(next_name, prev_name, timeout=2.0):
                        return True
                    raise RuntimeError(f'补点时找不到好友卡 {next_name}')
                click_x, click_y = target[1], target[2]
            self.click(click_x, click_y)
            if self._wait_profile_match(next_name, prev_name):
                if attempt > 1:
                    log(f'摸摸: 第 {attempt} 次点击后切换成功')
                return True
            if attempt == 1:
                log('摸摸: 好友页还没切过去，按目标卡新位置补点一次')
        raise RuntimeError(f'切换到好友 {next_name} 超时')

    def _wait_profile_match(self, name: str, prev_name: str = '',
                            timeout: float = 6.0) -> bool:
        """确认已经切到指定好友。

        **必须等资料卡内容更新**，不能只看底部卡的金色描边：点击卡片时游戏
        控制器会先移动金边、再异步加载新好友页，金边先到位时资料卡/火花条/
        宠物还是上一位好友的，此时去读火花/点宠物会摸错页。
        正常昵称以资料卡为准；只有重名好友（OCR 无法区分）才用金边兜底。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(FRIEND_SWITCH_SETTLE)
            profile = self._profile_texts()
            matched = any(names_match(t, name) for t in profile)
            # 精确判断"还停在上一个好友"：不能用前缀，否则
            # "凌晨" → "凌晨吃早饭~" 时新页面也会被误判成 still_prev。
            still_prev = bool(prev_name) and any(
                same_name(t, prev_name) for t in profile)
            if matched and not still_prev:
                return True
            if same_name(name, prev_name):
                # 重名好友：资料卡名字完全相同，只能看金边是否移到目标卡
                cards = self._friend_cards()
                if cards:
                    idx, _basis = self._current_card_index(cards, [])
                    if (idx is not None
                            and str(cards[idx][0]).strip() == str(name).strip()):
                        return True
        return False

    # ---- 入口 ----

    def run(self, max_times: int | None = None, max_rounds: int = 0) -> bool:
        """沿好友列表摸火花好友。max_times/max_rounds 仅为与其他场景签名一致。

        只有确认走完才返回 False（待重燃 / 最后一位 / 本次已摸过）；
        火花 OCR、好友卡定位、切换超时等瞬时/结构问题抛异常，
        由执行器按支线任务分级重试，避免一次抖动就把任务判成“今天做完了”。
        """
        taps = self._taps()
        today = _today_text()
        if touch_done_today(today=today):
            # 调度器重启/更新会重建内存里的 task.dead；没有落盘标记的话每次
            # 重启都会把火花好友重新摸一遍（一轮约 1 分钟，重复点宠物）。
            log('摸摸: 今天已经完整扫过一轮，跳过（次日自动重开）')
            self.ensure_main_page()
            return False
        self.ensure_main_page()
        self._open_friend_panel()
        self._enter_first_friend()
        petted = 0
        stopped_no_spark = False
        visited: list[str] = []        # 本次已摸过的好友卡昵称（content-desc，精确比较）
        while True:
            state = self._spark_state_checked()
            if state is False:
                log('摸摸: 当前好友没有火花（待重燃），按规则停止')
                stopped_no_spark = True
                break
            cards, idx, basis = self._locate_current_card()
            current_name = cards[idx][0]
            if card_visit_count(current_name, visited) >= MAX_SAME_NAME:
                log(f'摸摸: 又回到已摸过 {MAX_SAME_NAME} 次的 {current_name}，'
                    '停止（防止来回切好友）')
                break
            log(f'摸摸: 当前好友卡 #{idx + 1}/{len(cards)}（按{basis}）')
            name = self._profile_name() or current_name
            log(f'摸摸: {name} 有火花，点宠物 {taps} 下')
            self._pet(taps)
            petted += 1
            visited.append(current_name)
            if petted >= MAX_FRIENDS_PER_RUN:
                log(f'摸摸: 达到单轮上限 {MAX_FRIENDS_PER_RUN} 位，结束本轮')
                break
            if not self._advance_to_next(cards, idx, visited):
                break
        self.ensure_main_page()
        try:
            save_touch_progress(petted, today=today)
        except OSError as e:
            # 标记写不进去只影响"重启后会不会重复摸"，不影响今天的结果
            log(f'摸摸: 写当天完成标记失败（不影响本次结果）: {e}')
        if petted:
            log(f'摸摸完成: 摸了 {petted} 位火花好友'
                + ('（遇到没有火花的好友，已停止）' if stopped_no_spark
                   else '（没有更多好友）'))
        else:
            log('摸摸: 没有可摸的火花好友')
        # 返回 False = 今天的完整扫描已经做完，调度器标记“当天不可继续”；
        # 明天跨天清 dead 后自动重开（与 visit.py 好友走完时的语义一致）。
        return False


if __name__ == '__main__':
    import argparse

    ap = argparse.ArgumentParser(description='摸摸场景：只摸有火花的好友')
    args = ap.parse_args()
    try:
        TouchScenario().run()
    except KeyboardInterrupt:
        log('手动停止')