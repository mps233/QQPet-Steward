"""职业解锁哨兵：读职业树，监控隐藏职业线（武术家/梦境旅人/大明星）是否解锁。

导航链路（2026-09-14 真机实测 1080×2412，u2 content-desc 定位，分辨率无关）：
    主页面 → 点头像/名字区（desc「宠物资料」）→ 宠物资料页点「职业」卡片 → 职业树页
（旧路径「出门 → 职业小镇 → 右上角树形图标」更冗长，仅作捷径失败时的回退）
职业树页：8 条线 × 5 级（见习→大师），一屏 4 列、横向可滑（慢滑一次 ≈ 移动 3 列）；
顶部同样显示三维读数（一次检查同时完成「属性同步 + 解锁判定」）。

判定：目标列出现在屏幕上时，列头文字（如「武术家」）横向 ±窗口 内、
头部向下固定区间里出现 2 个以上汉字的文本 → 该线已解锁；否则未解锁；
列没露过脸 → unknown。候选解锁会再滑回来复读一遍，两遍一致才算数（防 OCR 抖动）。
已知见习名（习武小童/浅梦行者/偶像练习生）出现在页面上 → 直接判该线解锁（增强信号）。

写盘（全部失败只记日志、不影响调度）：
- runs/career_plan.json  三维合并写回（仪表盘「职业」页数据保鲜，保留工分/毕业键）
- runs/career_tree_last.png  最近一次检查的树页截图
- runs/career_unlock.json  解锁事件（仪表盘横幅 + Hermes 定时通知脚本消费）
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT
from .jsonio import write_json
from .ocr import ocr_fullscreen
from .progress import log as _log
from .u2dev import save_png

RUNS = PROJECT_ROOT / 'runs'
PLAN_FILE = RUNS / 'career_plan.json'
UNLOCK_FILE = RUNS / 'career_unlock.json'
LAST_TREE_SHOT = RUNS / 'career_tree_last.png'

TARGETS_DEFAULT = ('武术家', '梦境旅人', '大明星')
# 树页 8 列顺序（左→右）：状态一律以职业树实测为准——数字阈值只是粗略近似，
# 实测存在「数值达标但未解锁」（浅梦行者@智88）与「数值不达标却已解锁」（大厨）并存。
ALL_LINES = ('流浪散人', '画家', '侦探', '法师', '大厨', '武术家', '梦境旅人', '大明星')
# 树页左侧的层级标签（位置可能落进最左列判定窗口，需排除，防误判"已解锁"）
TIER_LABELS = ('见习', '初级', '中级', '高级', '大师')
# 三条隐藏线「见习」级的已知名字（调研口径；解锁时列格出现该名字）
FIRST_TIER_NAMES = {'武术家': '习武小童', '梦境旅人': '浅梦行者', '大明星': '偶像练习生'}
# 「已展示（佩挂中）」的判据：名字牌变**亮黄**（官方实测 rgb(255,240,78)，其余线是深棕
# rgb(85,63,59)）。这是页面上唯一可靠的佩挂信号——主页面那个「职业胶囊」的
# content-desc 只有四个字「职业胶囊」，拿不到当前职业名（实测 2026-10-19）。
EQUIPPED_RGB = (255, 240, 78)
EQUIPPED_TOL = 16
# 名字牌所在的纵向窗口（相对屏高）：见习行图标中心 y≈0.275h、名字牌 y≈0.30h，
# 五级等距，每级名字牌在 (tier*0.1495 + 0.30)h 附近。这里只用来排除属性条那块黄。
PLATE_Y_MIN = 0.30
# 截图文件名用 ASCII slug（仪表盘 /files/ 只放行 ASCII 文件名）
SHOT_SLUG = {'武术家': 'wushujia', '梦境旅人': 'mengjinglvren', '大明星': 'damingxing'}

CJK_RE = re.compile(r'[\u4e00-\u9fff]')
ATTR_RE = re.compile(r'(力量|智力|魅力)\s*([0-9]{1,6})')


# ---------------------------------------------------------------- 纯函数（可离线回归）

def has_cjk(text: str, n: int = 2) -> bool:
    """文本是否含至少 n 个汉字（列格出现真名字的信号；「???」不会命中）。"""
    return len(CJK_RE.findall(text or '')) >= n


def read_attrs(items, ymax: int) -> dict:
    """从一屏 OCR 结果读三维读数（树页/小镇页顶部固定显示）。"""
    got = {}
    for t, x, y, s in items:
        if ymax and y > ymax:
            continue
        m = ATTR_RE.search(t)
        if m:
            got[m.group(1)] = int(m.group(2))
    return got


def _find_header(items, target: str):
    """找目标线的列头（如「武术家」），返回 (x, y) 或 None。"""
    for t, x, y, s in items:
        if target in t.replace(' ', ''):
            return (x, y)
    return None


def eval_tree_items(items, targets, win: int, h: int) -> dict:
    """从一屏 OCR 结果判定各目标线状态。

    返回 {target: {'state': 'unlocked'|'locked'|'unknown', 'name': str|None}}；
    列头没找到（该列不在这一屏）→ unknown。
    """
    states = {}
    y_lo_off, y_hi_off = int(0.05 * h), int(0.62 * h)
    for tgt in targets:
        hdr = _find_header(items, tgt)
        if hdr is None:
            states[tgt] = {'state': 'unknown', 'name': None}
            continue
        hx, hy = hdr
        found = None
        for t, x, y, s in items:
            if (abs(x - hx) <= win and (hy + y_lo_off) <= y <= (hy + y_hi_off)
                    and has_cjk(t) and t.replace(' ', '').strip() not in TIER_LABELS):
                found = t.strip()
                break
        states[tgt] = ({'state': 'unlocked', 'name': found} if found
                       else {'state': 'locked', 'name': None})
    return states


def merge_views(views, targets, win: int, h: int) -> dict:
    """合并多屏 OCR 结果：任一屏判定出 unlocked/locked 即采用（unlocked 优先），
    并叠加「已知见习名出现」增强信号。"""
    merged = {t: {'state': 'unknown', 'name': None} for t in targets}
    for items in views:
        st = eval_tree_items(items, targets, win, h)
        for tgt in targets:
            cur, new = merged[tgt], st[tgt]
            if new['state'] == 'unlocked':
                merged[tgt] = new
            elif new['state'] == 'locked' and cur['state'] == 'unknown':
                merged[tgt] = new
    # 增强信号：任一屏直接出现该线见习名 → 已解锁
    for items in views:
        for t, x, y, s in items:
            tt = t.replace(' ', '')
            for tgt, known in FIRST_TIER_NAMES.items():
                if tgt in targets and known in tt:
                    merged[tgt] = {'state': 'unlocked', 'name': known}
    return merged


def detect_equipped(img, headers=None) -> dict | None:
    """从一张职业树整屏截图里找「已展示」的名字牌（亮黄色胶囊）。

    返回 `{'line': 线名, 'name': 职业名}` 或 None。

    判据（官方实测 rgb(255,240,78)）：
      - 整页只有两处是这颜色：**已展示的名字牌**（实心 52dp 宽的块）
        和**那条线的金色连线**（2dp 细线）。
      - 所以用「同一横向连续段的宽度」区分：连线段只有几像素、名字牌上百像素。
      - 线名靠列头反查：`headers` 传 OCR 出来的 `{线名: (x, y)}`（调用方本来就有），
        取 x 最近的；没有 headers 时退回按左端固定步进推算（只在第一屏准）。

    `name` 靠对黄牌那一小块单独 OCR（黄牌上是深棕字，对比度足够）。
    """
    try:
        import numpy as np
    except Exception:
        return None
    a = np.asarray(img)
    if a.ndim != 3 or a.shape[2] < 3:
        return None
    h, w, _ = a.shape
    r, g, b = (a[:, :, i].astype(int) for i in range(3))
    mask = ((abs(r - EQUIPPED_RGB[0]) < EQUIPPED_TOL)
            & (abs(g - EQUIPPED_RGB[1]) < EQUIPPED_TOL)
            & (abs(b - EQUIPPED_RGB[2]) < EQUIPPED_TOL))
    # 只看名字牌区（排除顶部属性条里的黄色图标）
    mask[: int(h * PLATE_Y_MIN), :] = False
    if not mask.any():
        return None
    # 逐行找**足够宽的**黄色连续段（连线只有 1~2dp，名字牌 52dp）
    min_w = int(w * 0.05)          # 1080 → 54px（52dp 牌子有 117px，留足余量）
    best = None                    # (宽, y0, y1, cx)
    for y in range(int(h * PLATE_Y_MIN), h):
        row = mask[y]
        if not row.any():
            continue
        xs = np.where(row)[0]
        # 找最长连续段
        run_start = xs[0]
        run_prev = xs[0]
        run_best = (xs[0], xs[0])
        for x in xs[1:]:
            if x - run_prev > 3:
                if run_prev - run_start > run_best[1] - run_best[0]:
                    run_best = (run_start, run_prev)
                run_start = x
            run_prev = x
        if run_prev - run_start > run_best[1] - run_best[0]:
            run_best = (run_start, run_prev)
        width = run_best[1] - run_best[0]
        if width < min_w:
            continue
        cx = (run_best[0] + run_best[1]) / 2
        if best is None or width > best[0]:
            best = (width, y, y, cx)
        elif best is not None and abs(cx - best[3]) < w * 0.05:
            best = (best[0], best[1], y, best[3])   # 同一块牌的下沿继续吞
    if best is None:
        return None
    _, y0, y1, cx = best
    cols = mask[y0:y1 + 1].sum(axis=0)
    xs = np.where(cols > 0)[0]
    if not xs.size:
        return None
    cx = (xs.min() + xs.max()) / 2
    # 线名：优先用调用方给的列头 x
    line = None
    if headers:
        line = min(headers, key=lambda k: abs(headers[k][0] - cx))
        if abs(headers[line][0] - cx) > w * 0.16:   # 差超过 57dp 认为没对准
            line = None
    if line is None:
        col_dp = cx / (w / 360.0)
        idx = round((col_dp - 86.0) / 83.0)
        if 0 <= idx < len(ALL_LINES) and abs(col_dp - (86.0 + idx * 83.0)) <= 45:
            line = ALL_LINES[idx]
    if line is None:
        return None
    name = None
    try:
        pad = int(w * 0.13)
        crop = a[max(0, y0 - 2):y1 + 2, max(0, int(cx) - pad):int(cx) + pad]
        for t, *_ in ocr_fullscreen(crop):
            tt = t.replace(' ', '').strip()
            if has_cjk(tt, 2) and tt not in TIER_LABELS:
                name = tt
                break
    except Exception:
        pass
    return {'line': line, 'name': name}


# ---------------------------------------------------------------- 逐格实测

# 树页 8 列 × 5 级的格子中心（**比例坐标**，随分辨率缩放）。
# 实测基准：1080×2412 / dpr3 的原机截图 —— 列中心 258/506/755/1000（第一屏 4 列），
# 行中心 664/1025/1387/1749/2111。换算成比例后换设备也不偏。
TREE_COL_CENTERS = (0.2389, 0.4685, 0.6991, 0.9259)   # 一屏 4 列
TREE_ROW_CENTERS = (0.2753, 0.4249, 0.5750, 0.7251, 0.8752)


def classify_cell(img, cx: float, cy: float, radius: float = 0.038) -> str:
    """判定一格的状态：`unlocked` | `lockedKnown` | `locked` | `blank`。

    **三态是 2026-10-19 扫非好友时发现的**，官方那三种长相：
      · 已解锁        → 纯彩（橙/蓝/紫底 + 职业物）
      · 已现世未解锁   → **彩色 + 半透明暗圆 + 白锁**
      · 未现世        → 纯灰（浅灰底 + 灰职业物）

    实测特征（1080×2412 原机截图，取格子中心 ±0.038 屏高）：

    | 类型       | 饱和度 | 亮度    | **高饱和像素占比** |
    | 彩色       | 85~146 | 162~181 | **0.60~0.97** |
    | 纯灰       | **0**  | 150~184 | **0.00** |
    | 空/占位格  | 32~36  | 89~117  | **0.00** |

    ⚠️ **别用「亮度阈值」区分占位和彩色**：大厨见习是彩色但亮度只有 125，
    占位格亮度 117 —— 两者重叠（踩过，会把它判成 empty）。
    真正干净的分界是「高饱和像素占比」：彩色格有 60%+ 的像素饱和度 > 60，
    灰格和占位格一个都没有。

    判据顺序：
      1. 高饱和占比 < 0.15 → 没有彩色职业物：再看饱和度
         · 饱和度 ≥ 22 → `blank`（空/占位格，饱和度 32 来自底色渐变）
         · 否则         → `locked`（纯灰格）
      2. 中心亮度 / 外围亮度 < 0.92 → 有锁遮罩 → `lockedKnown`
      3. 否则 → `unlocked`
    """
    try:
        import numpy as np
    except Exception:
        return 'locked'
    a = np.asarray(img)
    if a.ndim != 3 or a.shape[2] < 3:
        return 'locked'
    h, w, _ = a.shape
    r = max(6, int(min(h, w) * radius))
    x0, x1 = int(max(0, cx - r)), int(min(w, cx + r))
    y0, y1 = int(max(0, cy - r)), int(min(h, cy + r))
    patch = a[y0:y1, x0:x1].astype(int)
    if patch.size == 0:
        return 'locked'
    sat = patch.max(axis=2) - patch.min(axis=2)
    sat_mean = float(sat.mean())
    frac = float((sat > 60).mean())
    if frac < 0.15:
        # 没有彩色职业物：要么纯灰格，要么空/占位格
        return 'blank' if sat_mean >= 22 else 'locked'
    ph, pw, _ = patch.shape
    ch, cw = int(ph * 0.28), int(pw * 0.28)
    lum = float(patch.mean(axis=2).mean())
    lum_c = float(patch[ph // 2 - ch:ph // 2 + ch, pw // 2 - cw:pw // 2 + cw].mean(axis=2).mean())
    if lum_c < lum * 0.92:
        return 'lockedKnown'    # 中间压了暗色锁遮罩
    return 'unlocked'


def read_tree_grid(img, col_centers=TREE_COL_CENTERS,
                   row_centers=TREE_ROW_CENTERS) -> list[list[str]]:
    """读一屏的格子状态矩阵：`[[line0_tier0..tier4], ...]`（每列一条线，4 列）。"""
    h, w = (img.shape[0], img.shape[1]) if hasattr(img, 'shape') else (img.size[1], img.size[0])
    out = []
    for cf in col_centers:
        col = []
        for rf in row_centers:
            col.append(classify_cell(img, cf * w, rf * h))
        out.append(col)
    return out


# ---------------------------------------------------------------- 写盘

def save_attrs(attrs: dict) -> None:
    """三维合并写回 career_plan.json（保留工分/毕业等手工键）。"""
    try:
        data = json.loads(PLAN_FILE.read_text('utf-8'))
    except Exception:
        data = {}
    data.update({'力': attrs['力量'], '智': attrs['智力'], '魅': attrs['魅力']})
    write_json(PLAN_FILE, data, indent=1)


def load_unlock_data() -> dict:
    """读解锁事件文件（不存在/损坏回空档）。"""
    try:
        data = json.loads(UNLOCK_FILE.read_text('utf-8'))
        if isinstance(data, dict):
            data.setdefault('events', [])
            return data
    except Exception:
        pass
    return {'events': []}


def save_unlock_data(data: dict) -> None:
    try:
        write_json(UNLOCK_FILE, data, indent=1)
    except OSError:
        pass


def touch_last_check() -> None:
    """记录最近一次检查时间（仪表盘展示用）。"""
    data = load_unlock_data()
    data['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_unlock_data(data)


# ---------------------------------------------------------------- 真机检查

def _save_img(img, path: Path) -> bool:
    # 走 u2dev.save_png（内部用 PIL，按 RGB）：img 来自 dev.screenshot() 是 **RGB**
    # 数组，而 cv2.imwrite 按 BGR 解释 —— 直接用它存会把红蓝通道写反（设备上金黄
    # 的福袋存出来变蓝袋子）。
    return save_png(path, img)


def _on_career_tree(scen) -> bool:
    """当前是否已到职业树页（OCR 到"职业树"）。"""
    try:
        return any('职业树' in t for t, *_ in ocr_fullscreen(scen.screen()))
    except Exception:
        return False


def _open_career_tree_shortcut(scen, w: int, h: int, log=None) -> bool:
    """捷径进职业树：主页面点「宠物资料」→ 宠物资料页点「职业」卡片。

    2026-09-14 实测（1080x2412）：两处都是 u2 content-desc 精确命中，
    比旧的"出门→职业小镇→右上角树形图标"少两步、也不依赖坐标比例。
    """
    log = log or _log
    prof = scen.see('pet_profile')
    if not prof:
        log('职业哨兵: 主页面未找到「宠物资料」入口')
        return False
    scen.click(prof[0], prof[1])
    time.sleep(2.2)
    card = scen.see('pet_profile_career')
    if not card:
        # 资料页可能还在加载/动画，重试一次
        time.sleep(1.0)
        card = scen.see('pet_profile_career')
    if not card:
        log('职业哨兵: 宠物资料页未找到「职业」卡片')
        return False
    scen.click(card[0], card[1])
    time.sleep(2.5)
    if _on_career_tree(scen):
        log('职业哨兵: 已进职业树（捷径：宠物资料 → 职业）')
        return True
    log('职业哨兵: 点了「职业」卡片但未到职业树页')
    return False


def _open_career_tree_legacy(scen, w: int, h: int, log=None) -> bool:
    """旧路径（捷径失败时回退）：出门 → 职业小镇（工作页）→ 右上角树形图标。"""
    log = log or _log
    log('职业哨兵: 捷径失败，回退旧路径（出门 → 职业小镇 → 树形图标）')
    hit = scen.see('leave_home') or (int(0.498 * w), int(0.862 * h))
    scen.click(hit[0], hit[1])
    time.sleep(1.6)
    town = scen.see('town')
    if not town:
        time.sleep(1.0)
        town = scen.see('town')
    town = town or (int(0.787 * w), int(0.513 * h))
    scen.click(town[0], town[1])
    time.sleep(1.8)
    for cx, cy in ((int(0.787 * w), int(0.081 * h)), (int(0.787 * w), int(0.058 * h))):
        scen.click(cx, cy)
        time.sleep(2.0)
        if _on_career_tree(scen):
            log('职业哨兵: 已进职业树（旧路径）')
            return True
    return False


def _swipe_and_ocr(scen, x1: int, x2: int, y: int, log=None, settle: float = 0.25,
                   max_wait: float = 1.6):
    """横滑一屏并 OCR：**等画面稳定**再识别，替代固定 sleep（更快也更稳）。

    实测 swipe 本身约 1.6s（含惯性动画），而 screen()+OCR 只要 0.5s 左右；
    旧实现每屏固定 `sleep(1.0)` 会在动画结束后白等。这里改为轮询相邻两帧
    的 OCR 文本一致就算稳定（最多 max_wait 秒），滑动惯性结束时立即返回。
    返回 (img, items)。
    """
    scen.dev.d.swipe(x1, y, x2, y, 0.5)
    prev = None
    img, items = None, []
    deadline = time.time() + max_wait
    while True:
        img = scen.screen()
        items = ocr_fullscreen(img)
        sig = tuple(sorted((t, x, y2) for t, x, y2, _ in items))
        if sig == prev:
            return img, items
        prev = sig
        if time.time() >= deadline:
            return img, items
        time.sleep(settle)


def check_career_tree(scen, targets=None, log=None) -> dict:
    """导航到职业树检查一遍（调用方保证设备空闲；finally 回主页面）。

    scen: 任一场景实例（借用其设备/导航，建议传调度器的 school 实例）
    返回: {ok, reason, attrs: {力量,智力,魅力}, states: {线: {...}},
           unlocks: {线: 名字}（两遍确认过的）, shot, w, h}
    """
    log = log or _log
    event_targets = list(targets or TARGETS_DEFAULT)  # 报事件/通知的线（默认隐藏三线）
    watch = list(ALL_LINES)  # 实际判定与写回的线：全 8 线（仪表盘板子用）
    result = {'ok': False, 'reason': '', 'attrs': {}, 'states': {},
              'unlocks': {}, 'shot': None}
    started = time.time()
    try:
        scen.ensure_main_page()
        img = scen.screen()
        if hasattr(img, 'shape'):
            h, w = int(img.shape[0]), int(img.shape[1])
        else:  # PIL 兜底
            w, h = img.size
        win = max(60, int(0.088 * w))

        # 主页面捷径（2026-09-14 实测，2 步）：
        #   点顶部头像/名字区（content-desc="宠物资料"）→ 宠物资料页的「职业」卡片 → 职业树
        # 旧路径（出门 → 职业小镇 → 右上角树形图标，4 步）已弃用；若捷径失败再回退旧路径。
        items = []
        opened = _open_career_tree_shortcut(scen, w, h)
        if opened:
            items = ocr_fullscreen(scen.screen())
        if not opened:
            opened = _open_career_tree_legacy(scen, w, h)
            if opened:
                items = ocr_fullscreen(scen.screen())
        if not opened:
            result['reason'] = '未进入职业树页（捷径与旧路径均失败）'
            return result

        # 顶部三维（数值异步加载，重试）：短轮询代替固定 sleep(1.0)——数值通常
        # 几百毫秒就出来，白等 1 秒是浪费（OCR 本身仅 ~0.36s）
        attrs = {}
        for attempt in range(8):
            attrs = read_attrs(items, ymax=int(0.29 * h))
            if len(attrs) == 3:
                break
            time.sleep(0.3 if attempt < 4 else 1.0)
            items = ocr_fullscreen(scen.screen())

        # 横滑拍两屏（一屏 4 列）：初始左屏（流浪散人~法师）+ 右滑后的右屏（大厨~大明星）
        # 先读一次库里的已知状态，用于判断"本次是否有变化"——复读确认只为防 OCR 抖动
        # 导致的**误报新解锁**，状态没变时整轮复读（4 次 swipe + 3 次 OCR ≈ 8s）纯属浪费。
        try:
            prev_lines = (load_unlock_data().get('lines') or {})
        except Exception:
            prev_lines = {}

        y = int(0.62 * h)
        # **同一张图既做 OCR 又做逐格实测**：view_imgs[i] 与 views[i] 必须同源，
        # 否则列头（OCR）和格子颜色（像素）来自不同时刻，配出来的列会错位。
        img0 = scen.screen()
        items0 = items if img0 is None else ocr_fullscreen(img0)
        view_imgs, views = [img0] if img0 is not None else [], [items0]
        if img0 is None:
            views = [items]          # 兜底：拿不到图就沿用进树页那次 OCR
        for _ in range(2):
            vimg, vitems = _swipe_and_ocr(scen, int(0.83 * w), int(0.24 * w), y, log)
            view_imgs.append(vimg)
            views.append(vitems)

        states = merge_views(views, watch, win, h)

        # ---- 逐格实测（8 线 × 5 级）----
        # 之前只记「见习一级 + 每线是否解锁」，其余级别靠**手填的属性**推算，
        # 结果跟游戏里对不上（用户实报：明明解锁了显示灰色）。
        # 这里直接对每屏截图做颜色分类，把 40 格的真实状态全记下来。
        tier_states: dict[str, dict[str, str]] = {}
        try:
            # **逐屏各自配对**：view_imgs[i] 对应 views[i] 那次 OCR。
            # 之前把所有屏的列头混在一个 dict 里再排序，三屏的列头叠在一起，
            # 前 4 个可能全来自第一屏 → 列与线严重错配（实测把大厨记成 4/5 全彩）。
            for si, vimg in enumerate(view_imgs):
                if si >= len(views) or vimg is None:
                    break
                grid = read_tree_grid(vimg)
                hdrs_now = {}
                for t_, x_, yy_, _sc in views[si]:
                    tt = t_.replace(' ', '')
                    for ln in watch:
                        if ln in tt and 450 <= yy_ <= 620 and ln not in hdrs_now:
                            hdrs_now[ln] = x_
                ordered = sorted(hdrs_now, key=lambda k: hdrs_now[k])[:4]
                if len(ordered) < 3:
                    log(f'职业哨兵: 第{si}屏列头只识别到 {len(ordered)} 个，跳过逐格实测')
                    continue
                for ci, ln in enumerate(ordered):
                    if ci >= len(grid):
                        break
                    cells = grid[ci]
                    rec = tier_states.setdefault(ln, {})
                    for ti, cell in enumerate(cells):
                        if cell == 'blank':
                            continue      # 空/占位不写
                        st = 'unlocked' if cell == 'unlocked' else 'locked'
                        # unlocked 永久：一旦是 unlocked 不再降级
                        if rec.get(str(ti)) == 'unlocked':
                            continue
                        rec[str(ti)] = st
        except Exception as e:
            log(f'职业哨兵: 逐格实测失败: {e}')

        # 是否有"新解锁"（本次实测 unlocked，而库里记的是未解锁）——只有这种才需要复读
        newly = [t for t in watch
                 if states[t]['state'] == 'unlocked'
                 and not (prev_lines.get(t) or {}).get('unlocked')]
        if newly:
            # 复读确认（防 OCR 抖动）：滑回左端再滑过来，两遍一致才算数
            log(f'职业哨兵: 出现新解锁候选 {newly}，复读确认一遍')
            for _ in range(2):
                scen.dev.d.swipe(int(0.24 * w), y, int(0.83 * w), y, 0.5)
                time.sleep(0.8)
            views2 = [ocr_fullscreen(scen.screen())]  # 左屏复读
            for _ in range(2):
                _, vitems = _swipe_and_ocr(scen, int(0.83 * w), int(0.24 * w), y, log)
                views2.append(vitems)
            states2 = merge_views(views2, watch, win, h)
            for tgt in watch:
                if states[tgt]['state'] == 'unlocked' and states2[tgt]['state'] != 'unlocked':
                    states[tgt] = {'state': 'unknown', 'name': None}  # 两遍不一致，下轮复查
            log('职业哨兵: 复读确认完成: '
                + '、'.join(f"{t}={'已解锁' if states[t]['state'] == 'unlocked' else states[t]['state']}"
                           for t in watch))
        elif any(v['state'] == 'unlocked' for v in states.values()):
            log('职业哨兵: 无新解锁（与库中记录一致），跳过复读确认')

        # 8 线状态写回（树实测为准；解锁永久——不因单次读不到而回退）
        try:
            udata = load_unlock_data()
            lines_db = udata.get('lines') or {}
            now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for tgt in watch:
                st = states.get(tgt) or {}
                if st.get('state') not in ('unlocked', 'locked'):
                    continue
                old = lines_db.get(tgt) or {}
                unlocked = bool(old.get('unlocked')) or st['state'] == 'unlocked'
                name = st.get('name') or (old.get('name') if old.get('unlocked') else None)
                lines_db[tgt] = {'unlocked': unlocked, 'name': name, 'at': now_s}
            udata['lines'] = lines_db
            save_unlock_data(udata)
        except Exception as e:
            log(f'职业哨兵: 8线状态写回失败: {e}')

        # 逐级状态写回（前端职业树的彩色/带锁/灰色三态都靠它，**不再依赖手填属性**）
        if tier_states:
            try:
                udata = load_unlock_data()
                old_tiers = udata.get('tiers') or {}
                now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for ln, rec in tier_states.items():
                    old = old_tiers.get(ln) or {}
                    for ti, st in rec.items():
                        prev_rec = old.get(ti)
                        # 兼容两种历史形态：dict{'unlocked':bool} 或裸字符串
                        if isinstance(prev_rec, dict):
                            prev = bool(prev_rec.get('unlocked'))
                        else:
                            prev = prev_rec == 'unlocked'
                        old[ti] = {
                            # 解锁永久：历史 true 不会被一次误读覆盖
                            'unlocked': prev or st == 'unlocked',
                            'at': now_s,
                        }
                    old_tiers[ln] = old
                udata['tiers'] = old_tiers
                save_unlock_data(udata)
                log('职业哨兵: 逐格状态已记录 '
                    + '、'.join(f'{k}:{sum(1 for v in v.values() if v == "unlocked")}/5'
                               for k, v in tier_states.items()))
            except Exception as e:
                log(f'职业哨兵: 逐级状态写回失败: {e}')

        # 已展示（佩挂）的线：黄色名字牌。**这是页面上唯一可靠的佩挂信号** ——
        # 主页面那个「职业胶囊」的 content-desc 只有四个字，拿不到当前职业名。
        # 只在拍到的整屏里找；找不到就不动库里的旧值（避免一次没对准就把记录清空）。
        try:
            hdrs = {}
            for items in views:
                for t, x, yy, _sc in items:
                    tt = t.replace(' ', '')
                    for ln in watch:
                        if ln in tt and 450 <= yy <= 620 and ln not in hdrs:
                            hdrs[ln] = (x, yy)
            found = None
            for vimg in ([view_imgs[0]] if view_imgs else []) + view_imgs:
                found = detect_equipped(vimg, hdrs)
                if found:
                    break
            if found:
                udata = load_unlock_data()
                if udata.get('equipped') != found:
                    log(f"职业哨兵: 已展示 = {found['line']}（{found['name']}）")
                udata['equipped'] = found
                save_unlock_data(udata)
                result['equipped'] = found
        except Exception as e:
            log(f'职业哨兵: 已展示检测失败: {e}')

        # 保存最近树页截图
        if view_imgs and _save_img(view_imgs[-1], LAST_TREE_SHOT):
            result['shot'] = LAST_TREE_SHOT.name

        # 汇总结论 + 解锁截图存档（事件只报隐藏三线；普通线状态在仪表盘 8 线格子里看）
        unlocks = {}
        for tgt in event_targets:
            if tgt in states and states[tgt]['state'] == 'unlocked':
                name = states[tgt]['name'] or FIRST_TIER_NAMES.get(tgt)
                unlocks[tgt] = name
                # 只在**本次新解锁**时存图（newly 是本函数上面就算好的集合：本次实测
                # unlocked、而库里记的是未解锁）。这里原先直接按"当前是否 unlocked"
                # 存图，三条线全解锁之后每次检查（每小时兜底 + 每节课结算）都照存一份
                # ——17 天攒了 1245 张、占了 runs/ 2.1G 的大头。
                if tgt not in newly:
                    continue
                slug = SHOT_SLUG.get(tgt, 'unlock')
                shot_name = f"career_unlock_{slug}_{datetime.now():%m%d_%H%M}.png"
                if view_imgs and _save_img(view_imgs[-1], RUNS / shot_name):
                    unlocks.setdefault('_shots', {})
                    unlocks['_shots'][tgt] = shot_name
        result['attrs'] = attrs
        result['states'] = {t: {'state': states[t]['state'], 'name': states[t]['name']}
                            for t in watch}
        result['unlocks'] = {k: v for k, v in unlocks.items() if k != '_shots'}
        if '_shots' in unlocks:
            result['unlock_shots'] = unlocks['_shots']
        result['ok'] = True
        result['reason'] = 'ok'
        if len(attrs) == 3:
            try:
                save_attrs(attrs)
            except Exception as e:
                log(f'职业哨兵: 三维写回 career_plan.json 失败: {e}')
        log(f"职业哨兵: 检查完成（{time.time() - started:.0f}s，力{attrs.get('力量','?')}"
            f"/智{attrs.get('智力','?')}/魅{attrs.get('魅力','?')}）")
        return result
    except Exception as e:
        result['reason'] = f'{type(e).__name__}: {e}'
        return result
    finally:
        try:
            scen.ensure_main_page()
        except Exception as e:
            log(f'职业哨兵: 回主页面失败: {e}')
