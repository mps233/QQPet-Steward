"""真机页面探测小工具：dump 控件树 / 整屏 OCR / 点击 / 滑动，用于校准 locators。

新增场景前先在真机上逐屏探测，拿到真实的 content-desc / xpath / 文字坐标，
不要凭截图猜（QQ 宠物大量界面是 Kuikly / canvas 渲染，控件树与截图对不上）。

运行（用项目虚拟环境）：

    PY=.venv/bin/python
    $PY tools/probe_page.py dump 出门页          # 控件树 + 截图 -> runs/probe/出门页.{xml,png}
    $PY tools/probe_page.py text                 # 列出控件树里所有 content-desc / text（带坐标）
    $PY tools/probe_page.py ocr                  # 整屏 OCR：文字 + 屏幕坐标（定位 canvas 自绘元素）
    $PY tools/probe_page.py tap 950 240          # 点击设备坐标
    $PY tools/probe_page.py swipe 540 1800 540 900 600
    $PY tools/probe_page.py res                  # 屏幕分辨率

设备取 config.yaml 的 adb.device_serial（多设备时必须显式指定）。
"""

from __future__ import annotations

import os
import re
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

OUT_DIR = os.path.join(PROJECT_ROOT, 'runs', 'probe')


def _serial() -> str:
    try:
        from src.config import load_config
        return load_config().adb.device_serial
    except Exception:
        return ''


def _connect():
    import uiautomator2 as u2
    serial = _serial()
    return u2.connect(serial) if serial else u2.connect()


def _safe(name: str) -> str:
    return re.sub(r'[^\w\u4e00-\u9fff.-]', '_', name) or 'page'


def cmd_dump(label: str) -> None:
    d = _connect()
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.join(OUT_DIR, _safe(label))
    xml = d.dump_hierarchy()
    with open(base + '.xml', 'w', encoding='utf-8') as f:
        f.write(xml)
    d.screenshot(base + '.png')
    print(f'{base}.xml / {base}.png  ({len(xml)} 字节)')


def cmd_text() -> None:
    """列出控件树里所有带 content-desc / text 的节点（Kuikly 页面通常只有部分可见）。"""
    d = _connect()
    xml = d.dump_hierarchy()
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    n = 0
    for node in root.iter('node'):
        desc = node.get('content-desc') or ''
        text = node.get('text') or ''
        if not desc and not text:
            continue
        b = node.get('bounds') or ''
        m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', b)
        cx, cy = (int((int(m.group(1)) + int(m.group(3))) / 2),
                  int((int(m.group(2)) + int(m.group(4))) / 2)) if m else (0, 0)
        cls = (node.get('class') or '').split('.')[-1]
        rid = node.get('resource-id') or ''
        print(f'({cx:>4},{cy:>4}) {cls:<22} desc={desc!r} text={text!r} id={rid!r}')
        n += 1
    print(f'--- 共 {n} 个带文字的节点 ---')


def cmd_ocr() -> None:
    from src.locators import ocr_screen
    from src.u2dev import U2Device
    dev = U2Device()
    try:
        texts = ocr_screen(dev.screen())
        for t, x, y, s in sorted(texts, key=lambda r: (r[2], r[1])):
            print(f'({x:>4},{y:>4}) score={s:.2f} {t!r}')
        print(f'--- 共 {len(texts)} 条 ---')
    finally:
        dev.close() if hasattr(dev, 'close') else None


def cmd_tap(x: int, y: int) -> None:
    d = _connect()
    d.click(x, y)
    print(f'点击 ({x}, {y})')


def cmd_swipe(x1: int, y1: int, x2: int, y2: int, ms: int) -> None:
    d = _connect()
    d.swipe(x1, y1, x2, y2, duration=ms / 1000)
    print(f'滑动 ({x1},{y1}) -> ({x2},{y2}) {ms}ms')


def cmd_res() -> None:
    d = _connect()
    w, h = d.window_size()
    print(f'{w}x{h}')


def cmd_press(key: str) -> None:
    d = _connect()
    d.press(key)
    print(f'按键 {key}')


def cmd_wait(seconds: float) -> None:
    time.sleep(seconds)
    print(f'等待 {seconds}s')


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == 'dump':
        cmd_dump(args[0] if args else f'page-{int(time.time())}')
    elif cmd == 'text':
        cmd_text()
    elif cmd == 'ocr':
        cmd_ocr()
    elif cmd == 'tap':
        cmd_tap(int(args[0]), int(args[1]))
    elif cmd == 'swipe':
        cmd_swipe(int(args[0]), int(args[1]), int(args[2]), int(args[3]),
                  int(args[4]) if len(args) > 4 else 400)
    elif cmd == 'res':
        cmd_res()
    elif cmd == 'press':
        cmd_press(args[0] if args else 'back')
    elif cmd == 'wait':
        cmd_wait(float(args[0]) if args else 1)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
