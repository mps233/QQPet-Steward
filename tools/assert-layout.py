#!/usr/bin/env python3
"""布局体检断言：消费 tools/layout-check.sh 产出的探针 JSON Lines。

为什么需要它：web/src/styles/app.css 占全部 web 提交的 61%、占全部 fix 提交的
80%，约 45% 的提交在返工布局，且同一个"高度链断裂"错误犯过 3 次（断点分别在
.app / main / .page-slot）。而这类错误**没有任何报错**——改完只是整页塌成背景。

方法本身早就写在 web/FRONTEND_PITFALLS.md:40-52（同源 iframe + headless Chrome），
但一直靠人工执行。本脚本把它变成可断言的检查。

用法：
    python3 tools/assert-layout.py report.jsonl

退出码：0 全通过 / 1 有断言失败 / 2 输入为空

断言：
    P1  .home 高度 ≈ 视口高（±1.5px）      —— 高度链是否还连着
    P2  总览页 .home 不溢出                 —— 整页多出一条可滚区
    P3  main 直系子全为 div.page-slot       —— 有人往骨架插了新 wrapper
    P4  main 相关选择器命中数 > 0           —— 直系选择器被"多包一层"打断（静默失效）
    P5  --u <= 1.35px（提示级）             —— 单位基准上限未生效
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HOME_TOLERANCE_PX = 1.5
U_MAX_HINT = 1.35
# 亚像素溢出容差：.deck 这类元素的 top 由 calc() 算出，会产生 448.0001 这类值，
# 而 scrollHeight 是向上取整的整数 → 总差 1px。这是浮点噪音而非布局错误，
# 但会在输出里标注（"能多滑 1px"正是 git 历史里用户实报过的那一类现象）。
OVERFLOW_TOLERANCE_PX = 1


def main(path: str) -> int:
    rows = []
    failed = 0

    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            tag, notes = 'PASS', []
            # 只有总览页有 .home；内页是 section.card / section.shotpage
            # （见 web/src/App.tsx:351,363 vs 394-434）。这里提前判定，
            # 保证 probeMissing 分支也能安全引用。
            is_home_page = r.get('page') == 'main'

            if r.get('probeMissing'):
                tag = 'FAIL'
                notes.append('探针无输出（页面没渲染完 / 导航失败？）')
            else:
                vp = r.get('vp') or [0, 0]
                vh = vp[1]

                if is_home_page:
                    # P1 舞台高度：.home 高 ≈ 视口高
                    home_h = r.get('homeH')
                    if home_h is None:
                        tag = 'FAIL'
                        notes.append('总览页找不到 .home')
                    elif abs(home_h - vh) > HOME_TOLERANCE_PX:
                        tag = 'FAIL'
                        notes.append(
                            '.home 高 %.2f ≠ 视口高 %d（差 %+.2f）'
                            % (home_h, vh, home_h - vh))
                else:
                    # 内页：section 比一屏高是**正常**的（内容更长），
                    # 关键是滚动必须发生在 .page-slot 上，而不是让整页/body 滚
                    # （body 滚动会触发 iOS 整页橡皮筋，见 FRONTEND_PITFALLS 的滚动架构约束）。
                    sec_h = r.get('secH')
                    if sec_h is None:
                        tag = 'FAIL'
                        notes.append('内页找不到 section[data-page]')
                    elif r.get('slotOverflow') is None:
                        tag = 'FAIL'
                        notes.append('内页找不到 .page-slot（滚动容器缺失）')
                    else:
                        # 有内容溢出时，溢出量应体现在 slot 上（slotOverflow > 0）
                        if sec_h > vh + HOME_TOLERANCE_PX and (r.get('slotOverflow') or 0) <= 0:
                            tag = 'FAIL'
                            notes.append(
                                '内页内容 %.0f > 视口 %d，但 .page-slot 无溢出 —— '
                                '滚动可能落在了 body/html 上' % (sec_h, vh))

                # P2 总览页 .home 不该滚（内页滚动发生在 .page-slot，是正常的）
                #
                # 判据是"用户能不能滚"，不是"scrollHeight 与 clientHeight 差多少"：
                # overflow:hidden 时元素内容仍可能比盒子高 1px（子元素 top 由 calc 算出
                # 的亚像素值 + scrollHeight 取整），但用户滚不动，不是问题。
                # 只有 computed overflow-y 为 auto/scroll 时，那点溢出才会变成可滚动。
                overflow = r.get('homeOverflow')
                overflow_y = (r.get('homeOverflowY') or '').lower()
                if is_home_page and overflow not in (None, 0):
                    scrollable = overflow_y in ('auto', 'scroll', 'overlay')
                    if not scrollable:
                        notes.append(
                            '总览页 .home 内容高 %+dpx，但 overflow-y=%s（不可滚，已压掉）'
                            % (overflow, overflow_y or '?'))
                    elif abs(overflow) <= OVERFLOW_TOLERANCE_PX:
                        notes.append('总览页 .home 溢出 %+dpx（亚像素，可忽略）' % overflow)
                    else:
                        tag = 'FAIL'
                        notes.append(
                            '总览页 .home 溢出 %+dpx 且 overflow-y=%s —— '
                            '用户能滑出来（修法：给 .home 加 overflow:hidden）'
                            % (overflow, overflow_y))

                # P3 结构契约：main 的直系子应是 .page-slot（转场容器）
                kids = r.get('mainKids') or []
                if not kids or not all(k.startswith('div.page-slot') for k in kids):
                    tag = 'FAIL'
                    notes.append('main 直系子异常：%s' % kids)

                # P4 选择器失效体检：命中数为 0 的 main 相关规则 = 静默回归
                dead = [s for s, n in (r.get('rules') or []) if n == 0]
                if dead:
                    tag = 'FAIL'
                    notes.append('失效规则：' + ' | '.join(dead[:3]))

                # P5 基准上限（提示级，不改判定）
                u = (r.get('uVar') or '').strip()
                if u.endswith('px'):
                    try:
                        if float(u[:-2]) > U_MAX_HINT:
                            notes.append('--u=%s 超过 1.2 上限（04-E 未修？）' % u)
                    except ValueError:
                        pass

                if r.get('ready') is False:
                    notes.append('数据未加载完（断言仍有效：骨架高度与数据无关）')

            rows.append((
                r.get('size', '?'), r.get('page', '?'), tag,
                r.get('homeH') if is_home_page else r.get('secH'),
                (r.get('vp') or [0, 0])[1],
                (r.get('uVar') or '').strip(), r.get('btnW'), r.get('fsTask'),
                '；'.join(notes),
            ))
            if tag == 'FAIL':
                failed = 1

    if not rows:
        print('❌ 没有任何探针结果（report 为空？）')
        return 2

    w = max(len(str(x[0])) for x in rows)
    print('%s  页   结果  .home高  视口高  --u          圆钮宽  任务字号  说明'
          % '尺寸'.ljust(w))
    print('-' * (w + 72))
    for size, page, tag, home_h, vh, u, bw, fs, note in rows:
        print('%s  %-4s %-4s %7s %7s  %-10s %7s %8s  %s'
              % (str(size).ljust(w), page, tag, home_h, vh, u, bw, fs, note))
    print()
    if failed:
        print('❌ 存在失败项')
        return 1
    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
