#!/usr/bin/env python3
"""从「出门」地图截图里抠出右上角那颗「职业小镇」橙色树图标。

**为什么要抠图**：这颗树是游戏 HUD 上的职业小镇入口（地图右上角两颗圆钮里
左边那颗），素材库里没有独立文件 —— 查过 `qqpet_assets` 全仓 16623 张图、
以及职业小镇的 38 张 CDN 原图（`m8n.cdn-go.cn/web/cdn/-/latest/ai/*.png`，
那些都是场景建筑），都没有这个 HUD 图标。唯一来源就是整屏截图。

**为什么只保留橙色**：圆底是米白（#F1F2E0），和背景草地绿的差异太小，
按"偏离衬色"抠会把草地也带进来（踩过：出来一圈绿色描边）。树本身是高饱和
橙色（R-G ≥ 45、R-B ≥ 95），用这个判据能干净地把它单独摘出来。

用法：
    python3 tools/extract_career_tree_icon.py            # 用默认源图重新生成
    python3 tools/extract_career_tree_icon.py --src X.png

源图归档在 `static/qp-icons/official/_src/career_tree_map.png`（720×730），
输出覆盖 `static/qp-icons/official/off_r2_career.png`（80×80 透明 PNG）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / 'static/qp-icons/official/_src/career_tree_map.png'
OUT = ROOT / 'static/qp-icons/official/off_r2_career.png'

# 源图里那颗圆钮的圆心与半径（720×730 截图实测：圆盘 x 546~606、y 46~108）
CX, CY, R = 576.0, 77.0, 30.0
# 树本体的填色 = **左列四颗圆钮图标的同一个棕色** `#BE6321`（= CSS token `--btn-l-fg`）。
# 用户定的（2026-10-19 贴了铃铛图标说"弄成跟这个颜色一样"）。
#
# 演变史，别再绕回去：
#   ① 先是游戏原色橙 #E48E40 —— 用户说"在背景下不易读"；
#   ② 改成米白 #FFF8EA —— 用户说"还是用原来的（颜色）"，改回橙；
#   ③ 现在统一成 #BE6321 —— 与左列 4 颗（铃铛/齿轮/衣/日记）**同色**。
# 颜色由用户在 `--btn-l-fg` 这一类 token 上统一把控；要调就两边一起调。
FILL = (190, 99, 33)  # #BE6321
TARGET = 80
FILL_RATIO = 0.86


def extract(src: Path) -> Image.Image:
    im = Image.open(src).convert('RGB')
    px = im.load()
    size = int(R * 2)
    raw = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    rp = raw.load()
    for oy in range(size):
        for ox in range(size):
            x, y = int(CX - R + ox), int(CY - R + oy)
            if not (0 <= x < im.width and 0 <= y < im.height):
                continue
            r, g, b = px[x, y]
            # 严格橙色：绿色压得够低、蓝色更低、红色明显领先
            if not (r - g >= 45 and g - b >= 10 and r - b >= 95 and r >= 150 and b <= 130):
                continue
            a = int(255 * min(1.0, (r - b - 95) / 60))
            if a < 24:
                continue
            rp[ox, oy] = (*FILL, max(a, 60))
    return raw


def square_icon(raw: Image.Image) -> Image.Image:
    """按内容 bbox 居中放进 TARGET×TARGET，内容占 FILL_RATIO。"""
    box = raw.getbbox()
    if box is None:
        raise SystemExit('抠图为空：源图不符合预期（圆钮位置变了？）')
    crop = raw.crop(box)
    scale = 8
    big = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    # 轻度锐化 alpha：源图只有 60px 见方，不锐化会糊成一团
    alpha = big.split()[3].filter(ImageFilter.UnsharpMask(radius=4, percent=55, threshold=6))
    big.putalpha(alpha)

    side = max(big.size)
    inner = int(TARGET * FILL_RATIO)
    k = inner / side
    scaled = big.resize((max(1, int(big.width * k)), max(1, int(big.height * k))), Image.LANCZOS)
    out = Image.new('RGBA', (TARGET, TARGET), (0, 0, 0, 0))
    out.alpha_composite(scaled, ((TARGET - scaled.width) // 2, (TARGET - scaled.height) // 2))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description='抠出职业小镇的橙色树图标')
    ap.add_argument('--src', type=Path, default=DEFAULT_SRC)
    args = ap.parse_args()
    icon = square_icon(extract(args.src))
    icon.save(OUT)
    print(f'已生成 {OUT.relative_to(ROOT)}  {icon.size[0]}×{icon.size[1]}')


if __name__ == '__main__':
    main()
