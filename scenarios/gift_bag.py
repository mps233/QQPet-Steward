# 福袋场景：遍历好友列表，领取"系着绳结"的福袋。
#
# 机制（2026-09 实测，1080x2412）：
#  - 好友家宠物左侧地板固定位置可能出现福袋（约 x150-390, y1390-1670），
#    系着蓝绳结 = 可领取；敞口/摊平/无袋 = 已领取或没有——只点系绳的；
#  - 点击后弹"5个福袋共 N 金币，已领 M 个"公告弹窗，点小 × 关闭
#    （!! 全屏遮罩节点重心落在公告列表的好友行上，点它会误触跳转到别人家）；
#  - 非好友的福袋点击后弹"加好友"提示——只关闭跳过，绝不点加好友；
#  - 检测 = 袋口两个模板（系绳/敞口）在固定槽位多尺度模板匹配：
#    系绳分 >= TIED_MIN_SCORE 且高于敞口分 → 可领；敞口分高 → 已领跳过。
#    实测 20 家样本全部分类正确（可领 0.68~1.0 / 非可领的系绳分最高 0.53）。
#
# 自己家的「钱袋」（主页面 `content-desc="钱袋"`）是同一套机制、**另一份素材**：
#  未领 = 袋口收拢 + 橙色绳结 + 周围星星闪光；已领 = 袋口外翻、没有绳结（用户指出）。
#  它也落在 BAG_REGION 里（控件 bounds [60,1356][360,1656]），所以复用 `_match`；
#  但**不能复用好友家的模板**——跨素材 + 通道错位会误判（实测把敞口判成"可领" 0.679）。
#
# 限时皮肤（2026-10 万圣节）：官方把福袋换成了「戴巫婆帽的南瓜」——
#  未领 = 南瓜 + 紫色巫婆帽 + 四周光芒；已领 = 帽子消失只剩发光南瓜（10-30 版），
#  10-31 起又变成**帽子倒在旁边**（用户截图确认），所以已领态两种模板都留着。
#  这是限时的，活动结束后米黄袋子还会回来，所以**多套模板全部保留**（列表里逐个匹配
#  取最高分），不要用新模板覆盖旧文件。素材：home_bag_{tied,open}_pumpkin*.png，
#  好友列表的「有福袋」徽章同理新增 friend_icon_bag_pumpkin.png（同位置，节点 57x57、
#  按 ±17 外扩裁 77x77）。
#  ⚠ 模板带房间背景，跨房间分数会掉（实测同一张袋在紫色房间里 0.77、原来的木地板房间
#  1.00）——所以同一个状态存了不同房间的两套，取最高分；新增模板时优先用暖色地板/房间截图。
#
# !! 模板通道：`screenshot()` 返回 **RGB**，而 `cv2.imread` 读回来是 **BGR**，
#    偏偏 cv2.matchTemplate 是逐通道比对的 —— 通道错位会让分数虚低。新模板一律用
#    `_load_rgb_tpl()`（PIL，按 RGB）读，与 screen 对齐；好友家那两个老模板是 cv2
#    读的（历史原因）不能动，它靠"两态取高分"的相对比较还能用。
import os
import re
import time
from datetime import datetime

import cv2
import numpy as np
from PIL import Image

from src.progress import log
from src.ocr import ocr_texts
from src.scenario import CLICK_INTERVAL, DeviceScenario
from scenarios.visit import VisitScenario

# 袋口检测搜索区（1080x2412 基准坐标）。
# 原来只有 (100,1330,480,1650)，但袋子的实际摆位能更靠左——自己和好友家的袋子
# 都在 x=85 左右（控件 bounds [60,1356][360,1656]），x=100 的左边会把模板裁掉一截、
# 分数从 0.85 掉到 0.46。放宽到覆盖整个左下角地面。
BAG_REGION = (30, 1270, 500, 1710)
# 自己家「钱袋」用同一片区域（保留别名，语义上区分调用点）
HOME_BAG_REGION = BAG_REGION
# 非好友标志文字区：好友页=“点亮中1/3”，非好友=“加好友”（实测稳定互斥）
FRIEND_MARK_REGION = (700, 160, 980, 280)
# 单轮扫描最多检查的好友数（好友轮播内容会轮换、总量不定，防无限循环）
MAX_SWEEP = 45
# 系绳模板判定阈值：可领样本 0.68~1.0，非可领样本的系绳分最高 0.53
TIED_MIN_SCORE = 0.62
# 多尺度（袋子有生动画，渲染尺寸会小幅变化）
MATCH_SCALES = (0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5)
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')

# 自己家「钱袋」两态模板阈值：自匹配约 1.00，另一态 0.48~0.64。
# 取较高的下限 + 要求两态拉开余量，避免阈值卡在边界（敞口图上"系绳分"有 0.639，
# 只比 0.62 高一点点，光看下限会误判）。
HOME_BAG_MIN_SCORE = 0.70
HOME_BAG_MARGIN = 0.05

# 好友列表里的「有可领福袋」图标（用户指出的判据）：位置固定、无 content-desc。
# 常规款 bounds 是 43x43、x=789..832；2026-10 万圣节南瓜徽章是 57x57、x=782..839
# （1080 基准，中心同为 x≈810），y 就是好友行——所以节点宽度过滤要覆盖 40~70。
# 模板裁的是 bounds 上下左右各外扩 17px 的 77x77，所以这里必须用同样的外扩量裁图。
# 判定：福袋图标分（自匹配 0.99~1.00）要 >= 下限且高于另一个图标（粉爱心魔法棒，约 0.51）。
FRIEND_ICON_PAD = 17
FRIEND_ICON_MIN_SCORE = 0.80


def _load_rgb_tpl(path: str):
    """按 **RGB** 读模板 —— 与 `screenshot()` 同通道（原因见文件头的通道说明）。"""
    return np.array(Image.open(path).convert('RGB'))


class GiftBagScenario(VisitScenario):
    @staticmethod
    def _load_tpls(*names: str) -> list:
        """按名字加载一组模板（缺失的跳过，一个都没有才算缺失）。"""
        tpls = []
        for name in names:
            path = os.path.join(TEMPLATE_DIR, name)
            if os.path.isfile(path):
                tpls.append(_load_rgb_tpl(path))
        if not tpls:
            raise FileNotFoundError('、'.join(names))
        return tpls

    def __init__(self, dev=None):
        DeviceScenario.__init__(self, dev)  # 跳过 VisitScenario 的踩踩字段/日志
        gb = self.cfg.gift_bag
        self.last_sweep_at: datetime | None = None  # 上次扫描完成时间（调度间隔用）
        # 袋子的两态模板：**自己和好友家是同一份素材**，用一套即可
        # （实测跨房间自匹配 0.85、另一态 0.51；旧的浅蓝"颈口"模板是
        # 早期从别的素材裁的，跨房间只剩 0.55，判不出袋子——已弃用）。
        # 常规款（米黄袋子）与 2026-10 限时的万圣节南瓜款**两套都保留**，逐个匹配取最高分
        # （限时皮肤结束后旧模板继续有效，新模板也不碍事）。
        # 必须按 RGB 读：screenshot() 是 RGB，而 cv2.imread 返回 BGR，通道错位会拉低分数。
        self._tpl_tied = self._load_tpls(
            'home_bag_tied.png', 'home_bag_tied_pumpkin.png',
            'home_bag_tied_pumpkin2.png')
        self._tpl_open = self._load_tpls(
            'home_bag_open.png', 'home_bag_open_pumpkin.png',
            'home_bag_open_pumpkin_hat.png')
        self._tpl_home_tied = self._tpl_tied
        self._tpl_home_open = self._tpl_open
        # 好友列表里的「有福袋」图标模板：用来筛出该去哪几家（见 scan_friend_list）
        try:
            self._tpl_friend_bag = self._load_tpls(
                'friend_icon_bag.png', 'friend_icon_bag_pumpkin.png')
            self._tpl_friend_other = self._load_tpls('friend_icon_other.png')
        except Exception as e:  # noqa: BLE001 - 缺图标只影响"按需挑好友"，退回逐家遍历
            self._tpl_friend_bag = self._tpl_friend_other = None
            log(f'福袋: 好友列表图标模板缺失（{e}），退回逐家遍历')
        log(f'福袋: {"启用" if gb.enabled else "未启用"}，时间段 {gb.time_range}，'
            f'扫描间隔 {gb.interval_seconds} 秒')

    # ---- 检测 ----

    def _match(self, img, tpl, region_box=None) -> tuple[float, tuple[int, int], float]:
        """模板在搜索区的多尺度最大相关：返回（分数, 全图坐标 top-left, 尺度）。

        region_box 默认 BAG_REGION（好友家）；自己家的钱袋用 HOME_BAG_REGION
        （更靠左，见常量说明）。
        """
        x1, y1, x2, y2 = region_box or BAG_REGION
        h, w = img.shape[:2]
        rx1, ry1 = int(x1 * w / 1080), int(y1 * h / 2412)
        rx2, ry2 = int(x2 * w / 1080), int(y2 * h / 2412)
        region = img[ry1:ry2, rx1:rx2]
        best, best_loc, best_s = -1.0, (0, 0), 1.0
        for s in MATCH_SCALES:
            t = cv2.resize(tpl, None, fx=s, fy=s)
            if t.shape[0] >= region.shape[0] or t.shape[1] >= region.shape[1]:
                continue
            res = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            if mx > best:
                best = float(mx)
                best_loc = (rx1 + loc[0], ry1 + loc[1])  # 换算回全图坐标
                best_s = s
        return best, best_loc, best_s

    def _match_any(self, img, tpls, region_box=None):
        """一组模板逐个匹配，返回分最高的一组（分数, 全图坐标, 尺度, 命中的模板）。"""
        best = (-1.0, (0, 0), 1.0, None)
        for tpl in tpls:
            score, loc, s = self._match(img, tpl, region_box)
            if score > best[0]:
                best = (score, loc, s, tpl)
        return best

    def is_friend_page(self, screen=None) -> bool:
        """当前好友页的主人是不是我们的好友：右上角有"加好友"= 非好友，袋子领不了。

        好友页该位置显示"点亮中1/3"（实测两者稳定互斥，位置同为约 (800,210)）。"""
        img = screen if screen is not None else self.screen()
        x1, y1, x2, y2 = FRIEND_MARK_REGION
        h, w = img.shape[:2]
        rx1, ry1 = int(x1 * w / 1080), int(y1 * h / 2412)
        rx2, ry2 = int(x2 * w / 1080), int(y2 * h / 2412)
        return not any('加好友' in t for t, *_ in ocr_texts(img[ry1:ry2, rx1:rx2]))

    def bag_state(self, screen=None) -> str:
        """'tied'=系绳可领 / 'open'=已领（敞口或摊平）/ 'none'=没有袋子。"""
        img = screen if screen is not None else self.screen()
        st, _, _, _ = self._match_any(img, self._tpl_tied)
        so, _, _, _ = self._match_any(img, self._tpl_open)
        if st >= TIED_MIN_SCORE and st > so:
            return 'tied'
        if so >= TIED_MIN_SCORE and so > st:
            return 'open'
        return 'none'

    def bag_click_point(self, screen=None) -> tuple[int, int] | None:
        """系绳可领时返回袋子的实际点击点（模板命中位中心）。

        袋子位置随房间布局上下左右浮动（实测最左 x150 摆位），不能点固定坐标。"""
        img = screen if screen is not None else self.screen()
        st, loc, s, tpl = self._match_any(img, self._tpl_tied)
        if st < TIED_MIN_SCORE:
            return None
        th, tw = tpl.shape[:2]
        cx = loc[0] + int(tw * s / 2)
        cy = loc[1] + int(th * s / 2)
        return cx, cy

    # ---- 自己家的「钱袋」 ----

    def home_bag_state(self, screen=None) -> str:
        """自己家「钱袋」状态：'tied'=未领取（可点）/ 'open'=已领取 / 'none'=没看出来。

        用**自己家专属**的两态模板（米黄袋子 + 橙绳结），不能借好友家那套浅蓝袋子：
        跨素材 + 通道错位会把敞口误判成"可领"（实测系绳分 0.679 > 阈值）。
        两态判据来自用户观察：未领=袋口收拢系绳、已领=袋口外翻。
        """
        if not self._tpl_home_tied:
            return 'none'
        img = screen if screen is not None else self.screen()
        st, _, _, _ = self._match_any(img, self._tpl_home_tied, HOME_BAG_REGION)
        so, _, _, _ = self._match_any(img, self._tpl_home_open, HOME_BAG_REGION)
        if st >= HOME_BAG_MIN_SCORE and st > so + HOME_BAG_MARGIN:
            return 'tied'
        if so >= HOME_BAG_MIN_SCORE and so > st + HOME_BAG_MARGIN:
            return 'open'
        return 'none'

    def home_bag_click_point(self, screen=None) -> tuple[int, int] | None:
        """自己家钱袋「未领取」时的点击点（系绳模板命中位中心，随摆位浮动）。"""
        if not self._tpl_home_tied:
            return None
        img = screen if screen is not None else self.screen()
        st, loc, s, tpl = self._match_any(img, self._tpl_home_tied, HOME_BAG_REGION)
        if st < HOME_BAG_MIN_SCORE:
            return None
        th, tw = tpl.shape[:2]
        return loc[0] + int(tw * s / 2), loc[1] + int(th * s / 2)

    def claim_home_bag(self) -> str:
        """先领**自己家的**福袋（主页面「钱袋」）。返回同 claim：'ok:文本'/'none'。

        用户实报"为什么不会领取自己的福袋"——自己家的福袋**不在好友列表里、也不用绕
        好友列表**（好友列表里当然看不到自己），它就挂在主页面的 `content-desc="钱袋"`
        上。是否领过**看袋子自己的两态图标就够**（用户指出）：未领=袋口收拢+橙绳结
        （周围还有星星闪光），已领=袋口外翻。比"点开面板读领取人列表"省一步、快一秒，
        也顺带兼容游戏里「下次直接领取，不自动展开福袋详情」那个勾选项（勾了就没有面板可读）。

        一个人一个周期只能领一次，再点只弹面板不给钱，所以每次都调无副作用。
        关面板务必走 `_dismiss_popup()` 的 <300px 尺寸过滤：面板上有**两个**
        `content-desc="关闭"`——一个是全屏遮罩（bounds 1080x2412，重心落在领取人列表
        的好友行上），点它会误触跳到别人家；另一个才是真 X（90x90）。
        """
        screen = self.screen()
        state = self.home_bag_state(screen)
        if state == 'none':
            log('福袋: 主页面没看出钱袋状态（模板没命中），跳过自己家这一步')
            return 'none'
        if state == 'open':
            log('福袋: 自己家的钱袋已经领过了（袋口敞开），跳过')
            return 'none'
        point = self.home_bag_click_point(screen)
        if point is None:
            hit = self.see('home_bag', screen)     # 模板没定位到时退回控件中心
            if not hit:
                return 'none'
            point = (hit[0], hit[1])
        log(f'福袋: 自己家的钱袋还没领（系绳），点击领取 ({point[0]}, {point[1]})')
        return self._claim_at(point[0], point[1])

    # ---- 好友列表：按图标筛出"有福袋"的那几家 ----

    @staticmethod
    def _icon_score(crop, tpl) -> float:
        """裁剪区与图标模板的相似度（两者同尺寸，matchTemplate 直接给单点分数）。"""
        if crop.shape[:2] != tpl.shape[:2]:
            crop = cv2.resize(crop, (tpl.shape[1], tpl.shape[0]))
        return float(cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED).max())

    def scan_friend_list(self) -> list[tuple[int, int]] | None:
        """开好友列表 → 返回**有福袋图标**的好友行的「访问」按钮坐标。

        用户指出的判据：好友列表里"这家有可领福袋"会带一个图标（常规款=黄色袋子+爪印，
        2026-10 限时款=戴巫婆帽的南瓜；两套模板都保留、取高分）；别的东西（比如粉爱心
        魔法棒）不是福袋。于是不必挨家进好友家检测——

        实测这天：9 家好友里只有 2 家真有福袋，其余 7 家每轮白跑一趟导航；
        而原实现还因为模板问题**点错了对象**（把没福袋的判成可领、把有福袋的判成没有）。

        图标位置固定（bounds 43x43、x=789..832）但**没有 content-desc**，只能模板匹配；
        同行右侧的「访问」按钮有 `desc='访问'`，按 y 配对即可（好友名是自绘的读不到，
        但也不需要知道名字——点访问按钮就进那一行）。

        返回：坐标列表（可能为空 = 列表打开了但没有福袋）；None = 这一步用不了
        （模板缺失/好友按钮没找到/列表打不开），调用方退回逐家遍历。
        """
        if not self._tpl_friend_bag:
            return None
        btn = self.see('main_friend_btn')
        if not btn:
            log('福袋: 主页面没找到「好友」按钮，改回逐家遍历')
            return None
        self.click(btn[0], btn[1])
        time.sleep(1.6)
        xml = self.dev.d.dump_hierarchy()
        screen = self.screen()
        icons, visits = [], []
        for tag in re.finditer(r'<node[^>]*>', xml):
            node = tag.group(0)
            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
            if not b:
                continue
            x1, y1, x2, y2 = (int(b.group(i)) for i in (1, 2, 3, 4))
            d = re.search(r'content-desc="([^"]*)"', node)
            desc = d.group(1) if d else ''
            if desc == '访问':
                visits.append((x1, y1, x2, y2))
            # 常规款图标节点是 43x43（x=789..832）；2026-10 万圣节南瓜徽章是
            # **57x57**（实测 (782,2074)-(839,2131)），宽度上限必须放宽到 70，
            # 否则新版图标一粒都进不来（真机实测：收紧到 50 时永远"0 个图标"）。
            elif ('ImageView' in node and 40 <= x2 - x1 <= 70 and 760 <= x1 <= 840
                  and y1 > 900):        # y>900：只收好友行区域，别把顶栏的 57px 图标算进来
                icons.append((x1, y1, x2, y2))
        log(f'福袋: 好友列表 {len(icons)} 个图标 / {len(visits)} 个访问按钮')
        tgt_h, tgt_w = self._tpl_friend_bag[0].shape[:2]
        h, w = screen.shape[:2]
        targets: list[tuple[int, int]] = []
        for (ix1, iy1, _ix2, _iy2) in icons:
            # 裁图必须和模板用同样的外扩量（77x77），否则 matchTemplate 拿不到高分
            cx = max(0, int((ix1 - FRIEND_ICON_PAD) * w / 1080))
            cy = max(0, int((iy1 - FRIEND_ICON_PAD) * h / 2412))
            crop = screen[cy:cy + tgt_h, cx:cx + tgt_w]
            if crop.shape[:2] != (tgt_h, tgt_w):
                continue
            sb = max(self._icon_score(crop, t) for t in self._tpl_friend_bag)
            so = max(self._icon_score(crop, t) for t in self._tpl_friend_other)
            if sb < FRIEND_ICON_MIN_SCORE or sb <= so:
                continue
            for (vx1, vy1, vx2, vy2) in visits:
                if vy1 - 20 <= iy1 <= vy2 + 20:          # 同行：图标 y 落在按钮纵向范围内
                    targets.append(((vx1 + vx2) // 2, (vy1 + vy2) // 2))
                    break
        log(f'福袋: 好友列表里有福袋的 {len(targets)} 家（其余跳过）')
        # **故意不在这里关列表**：返回的是「访问」按钮坐标，调用方得在列表**还开着**
        # 的时候点下去。早先在这里 go_back()，结果坐标全是失效的——真机上表现为
        # "点了没反应/点到自己家别处"（踩过：截图里人还站在自家浴室）。关列表由调用方负责。
        return targets

    def _claim_and_report(self, desc: str) -> bool:
        """在当前好友家里判定并领取袋子。返回是否真的领到。"""
        state = self.bag_state()
        if state == 'open':
            log(f'福袋: {desc} 袋子已是敞口（领过），跳过')
            return False
        if state != 'tied':
            log(f'福袋: {desc} 没判出袋子，跳过')
            return False
        point = self.bag_click_point()
        if point is None:
            log(f'福袋: {desc} 系绳状态但定位不到点击点，跳过')
            return False
        result = self.claim(point)
        if result.startswith('ok:'):
            log(f'福袋: {desc} 领取成功（{result[3:]}）')
            return True
        if result.startswith('friend:'):
            log(f'福袋: {desc} 点击弹"加好友"提示（{result[7:]}），跳过不领')
            return False
        log(f'福袋: {desc} 点击后无反馈（可能不可领），跳过')
        return False

    def _claim_selected_friends(self) -> tuple[int, int] | None:
        """按好友列表的福袋图标挑好友：反复"开列表取一家 → 进去领 → 回主页面"。

        领过之后那家的图标会消失，所以每轮重开列表都能拿到还没处理的那几家；
        另外用 `done`（按「访问」按钮的 y）兜底，防止图标不消失时原地打转。
        返回 (检查家数, 领取个数)；None = 图标这一环用不了（调用方退回逐家遍历）。
        """
        if not self._tpl_friend_bag:
            return None
        checked = claimed = 0
        done: set[int] = set()
        for _ in range(MAX_SWEEP):
            targets = self.scan_friend_list()
            if targets is None:
                return None if checked == 0 else (checked, claimed)
            fresh = [(x, y) for (x, y) in targets if y not in done]
            if not fresh:
                # 列表还开着（scan_friend_list 故意不关），收尾时替它关上
                self.go_back()
                time.sleep(CLICK_INTERVAL)
                break
            vx, vy = fresh[0]
            done.add(vy)
            checked += 1
            log(f'福袋: 进有福袋图标的那家（本轮第 {len(done)} 家 / 列表共 {len(targets)} 家）')
            self.click(vx, vy)
            time.sleep(1.8)
            if self._claim_and_report('好友'):
                claimed += 1
            self.ensure_main_page()
        return checked, claimed

    # ---- 领取 ----

    def _dismiss_popup(self, xml: str) -> bool:
        """关闭福袋公告/提示弹窗：只点小号（<300px）的关闭类按钮。

        全屏遮罩节点的 bounds 是整屏、重心落在公告列表的好友行上——点它的
        中心会误触跳转到别人家（实测教训），必须用尺寸过滤掉。"""
        for m in re.finditer(
                r'<node[^>]*content-desc="(关闭|取消|知道了|确定)"[^>]*'
                r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
            x1, y1, x2, y2 = (int(m.group(2)), int(m.group(3)),
                              int(m.group(4)), int(m.group(5)))
            if x2 - x1 < 300 and y2 - y1 < 300:
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                log(f'福袋: 关闭弹窗「{m.group(1)}」({cx}, {cy})')
                self.click(cx, cy)
                time.sleep(CLICK_INTERVAL)
                return True
        return False

    def claim(self, point: tuple[int, int]) -> str:
        """点击袋子实际位置（1080x2412 参考坐标）-> 读反馈弹窗 -> 关闭。

        返回 'ok:文本'/'friend:文本'/'none'。"""
        w, h = self.dev.window_size()
        return self._claim_at(int(point[0] * w / 1080), int(point[1] * h / 2412))

    def _claim_at(self, cx: int, cy: int) -> str:
        """按**设备实际坐标**点击 -> 读反馈弹窗 -> 关闭（claim / claim_home_bag 共用）。"""
        self.click(cx, cy)
        time.sleep(2.2)
        xml = self.dev.d.dump_hierarchy()
        note = ''
        friend_prompt = False
        for m in re.finditer(r'<node[^>]*>', xml):
            d = re.search(r'content-desc="([^"]*)"', m.group(0))
            if not d:
                continue
            desc = d.group(1)
            if '加好友' in desc:
                friend_prompt = True
                note = note or desc[:80]
            elif re.search(r'福袋', desc) and len(desc) < 120:
                note = desc
        dismissed = self._dismiss_popup(xml)
        if not dismissed and (friend_prompt or note):
            # 兜底：弹窗没有可点的关闭/取消按钮时按系统返回（弹窗一般会被关掉）
            log('福袋: 未找到关闭按钮，按返回兜底')
            self.go_back()
            time.sleep(CLICK_INTERVAL)
        if friend_prompt:
            return f'friend:{note}'
        if note:
            return f'ok:{note}'
        return 'none'

    # ---- 入口 ----

    def run(self, max_times: int | None = None, max_rounds: int = 0) -> bool:
        """一轮 = 先领自己家的福袋（主页面「钱袋」）+ 遍历好友列表：每停一家检测袋口，
        系绳的点开领取，结束后回主页面。"""
        log('福袋扫描开始')
        self.ensure_main_page()
        # 自己家的福袋挂在主页面「钱袋」上（好友列表里看不到自己，也不该靠好友列表），
        # 先就地判一次两态图标：未领（袋口系绳）才点，已领（袋口敞开）直接跳过。
        # 结果单独记日志，不混进下面"检查 N 家"的统计里
        home = self.claim_home_bag()
        if home.startswith('ok:'):
            log(f'福袋: 自己家领取成功（{home[3:]}）')
        # 按好友列表的福袋图标挑出该去哪几家（用户指出的判据）：省掉没福袋那几家的
        # 白跑导航，也不会像老路子那样因模板跨素材误判而点错对象
        picked = self._claim_selected_friends()
        if picked is not None:
            checked, claimed = picked
            log(f'福袋扫描完成: 检查 {checked} 家，领取 {claimed} 个')
            self.last_sweep_at = datetime.now()
            self.ensure_main_page()
            return True
        # 兜底：图标这一环用不了（模板缺失/列表打不开）→ 走原来的逐家遍历
        self._friends = []        # 累积好友名单（只增不减），见 visit.py
        self._friend_index = 0
        self.goto_first_friend()
        self._accumulate_friends()
        checked = claimed = 0
        while True:
            time.sleep(0.8)
            desc = (self._friends[self._friend_index]
                    if self._friend_index < len(self._friends) else '')
            screen = self.screen()
            checked += 1
            if not self.is_friend_page(screen):
                # 好友都排在轮播最前面：一旦遇到非好友（右上角"加好友"）就收尾——
                # 后面的全都不是好友、袋子也领不了（用户确认的机制，不再往后点）
                log(f'福袋: {desc} 是非好友，说明好友已轮完，结束本轮')
                break
            state = self.bag_state(screen)
            if state == 'tied':
                point = self.bag_click_point(screen)
                if point is None:
                    log(f'福袋: {desc} 系绳状态但定位不到点击点，跳过')
                else:
                    result = self.claim(point)
                    if result.startswith('ok:'):
                        claimed += 1
                        log(f'福袋: {desc} 领取成功（{result[3:]}）')
                    elif result.startswith('friend:'):
                        log(f'福袋: {desc} 点击弹"加好友"提示（{result[7:]}），跳过不领')
                    else:
                        log(f'福袋: {desc} 点击后无反馈（可能不可领），跳过')
            if checked >= MAX_SWEEP:
                log(f'福袋: 已达单轮检查上限 {MAX_SWEEP} 家，结束本轮')
                break
            if not self.next_friend():
                break
        log(f'福袋扫描完成: 检查 {checked} 家，领取 {claimed} 个')
        self.last_sweep_at = datetime.now()
        self.ensure_main_page()
        return True
