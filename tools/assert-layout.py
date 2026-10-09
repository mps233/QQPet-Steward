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
    P1  V1 高度锁 / V2 至少填满可用高度     —— 高度链是否还连着
    P2  V2 短屏在 page-slot 滚动，不遮挡内容
    P3  main 直系子全为 div.page-slot       —— 有人往骨架插了新 wrapper
    P4  骨架选择器命中数 > 0
    P5  单位固定 / 小屏宽度 / 底栏重叠 / 列表可达 / 测试视频缩放
"""
from __future__ import annotations

import json
import sys

HOME_TOLERANCE_PX = 1.5
# 亚像素溢出容差：.deck 这类元素的 top 由 calc() 算出，会产生 448.0001 这类值，
# 而 scrollHeight 是向上取整的整数 → 总差 1px。这是浮点噪音而非布局错误，
# 但会在输出里标注（"能多滑 1px"正是 git 历史里用户实报过的那一类现象）。
OVERFLOW_TOLERANCE_PX = 1


def overlaps(a, b) -> bool:
    if not a or not b:
        return False
    return (min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]) > HOME_TOLERANCE_PX
            and min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]) > HOME_TOLERANCE_PX)


def responsive_failures(r: dict) -> list[str]:
    notes = []
    if r.get('bodyOverflowX', 0) > OVERFLOW_TOLERANCE_PX:
        notes.append('页面横向溢出')
    u = (r.get('uVar') or '').strip()
    if u.endswith('px') and abs(float(u[:-2]) - 1) > 0.000001:
        notes.append('默认舞台单位不是 1px：' + u)

    if r.get('homeVariant') == 'v2' and r.get('page', '').startswith('main'):
        center = r.get('centerRect')
        for key in ('idRect', 'capsRect'):
            box = r.get(key)
            if center and box and (box[0] < center[0] - HOME_TOLERANCE_PX
                                  or box[0] + box[2] > center[0] + center[2] + HOME_TOLERANCE_PX):
                notes.append(key + ' 超出中间列')
        if any(n > OVERFLOW_TOLERANCE_PX for n in r.get('capOverflow', [])):
            notes.append('胶囊行横向溢出')
        for key in ('panelRect', 'funcRect'):
            if overlaps(r.get(key), r.get('deckRect')):
                notes.append(key + ' 与底栏重叠')
        panel, tasks = r.get('panelRect'), r.get('listRect')
        if not tasks or tasks[2] < 60 or tasks[3] < 44:
            notes.append('任务列表被挤没或无法操作')
        elif panel and tasks[1] + tasks[3] > panel[1] + panel[3] + HOME_TOLERANCE_PX:
            notes.append('任务列表被面板裁切')
        if r.get('listLastVisible') is False:
            notes.append('滚到列表底仍看不全最后一项')
        if r.get('ringHit') is False:
            notes.append('宠物状态环被其他元素挡住')
        if r.get('page') == 'main-open' and not r.get('expanded'):
            notes.append('宠物状态未展开')

    if r.get('page') == 'shot':
        card, video, native = r.get('liveRect'), r.get('videoRect'), r.get('videoSize')
        if not card or not video or not native or min(native) <= 0:
            notes.append('测试视频未就绪')
        elif max(abs(a - b) for a, b in zip(card, video)) > HOME_TOLERANCE_PX:
            notes.append('视频没有填满直播容器（尺寸样式未匹配）')
        elif abs(card[2] - card[3] * native[0] / native[1]) > HOME_TOLERANCE_PX:
            notes.append('直播容器未跟随视频实际比例')
    return notes


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
            is_home_page = r.get('page', '').startswith('main')

            if r.get('probeMissing'):
                tag = 'FAIL'
                notes.append('探针无输出（页面没渲染完 / 导航失败？）')
            else:
                vp = r.get('vp') or [0, 0]
                vh = r.get('availableH') or vp[1]
                is_v2 = r.get('homeVariant') == 'v2'

                if is_home_page:
                    home_h = r.get('homeH')
                    if home_h is None:
                        tag = 'FAIL'
                        notes.append('总览页找不到舞台')
                    elif (home_h < vh - HOME_TOLERANCE_PX if is_v2
                          else abs(home_h - vh) > HOME_TOLERANCE_PX):
                        tag = 'FAIL'
                        notes.append(
                            '舞台高 %.2f 与可用高 %d 不符（差 %+.2f）'
                            % (home_h, vh, home_h - vh))
                    elif is_v2 and home_h > vh + HOME_TOLERANCE_PX:
                        if (r.get('slotOverflow') or 0) <= 0 or r.get('slotOverflowY') not in ('auto', 'scroll'):
                            tag = 'FAIL'
                            notes.append('短屏内容增加，但 page-slot 无法滚动')
                        else:
                            notes.append('短屏由 page-slot 滚动，内容保留')
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
                if is_home_page and not is_v2 and overflow not in (None, 0):
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

                # 只检查所有视图都应存在的骨架，不把别的页面的条件规则判为失效。
                dead = [s for s, n in (r.get('rules') or []) if n == 0]
                if dead:
                    tag = 'FAIL'
                    notes.append('失效规则：' + ' | '.join(dead[:3]))

                issues = responsive_failures(r)
                if issues:
                    tag = 'FAIL'
                    notes.extend(issues)

                if r.get('ready') is False:
                    tag = 'FAIL'
                    notes.append('页面数据未加载完')

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
