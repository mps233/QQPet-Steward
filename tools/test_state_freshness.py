"""离线测试：调度器重启后的"状态新鲜度"（用户实报 bug 的守卫）。

背景（2026-09-28 实报）：宠物被项目送去上课 → 用户手动召回 → 停掉调度器 →
再启动调度器 → **界面还显示"在上课"**。原因有两处残留：
  ① `runs/queue_status.json` 还是上一轮进程写的（`pending: 上课`），新进程要跑完
     第一个任务才覆盖它 → GUI 任务队列卡显示"上课（进行中）"；
  ② 仪表盘的 `work_eta()` 是**按日志**算的，重启后上一轮那条"上课: 进行中，预计…
     （01:39:13 收尾）"还在 10 分钟窗口内 → 显示"上课中 剩余 xx 分钟"。

本脚本覆盖三块（都不连设备、不碰 runs/ 里的真实进度文件）：
  A. `src/queue_status.py` 的 starting/stopped/pid 新鲜度判定；
  B. `dashboard.work_eta()` 只认"本次调度器启动之后"的登记 + 启动标记不漂移；
  C. `src/scenario.py` 的出门状态分类/启动实测 `probe_activity_state()`，
     以及 `wait_busy_end()` 重构后行为不变（结算页/进行中/被雇佣三条路径）；
  D. `TaskQueueRunner._startup_activity_probe()` 的四条分支（空/进行中/结算/被雇佣）；
  E. GUI 任务队列卡的"启动检查中"判定 + `_main_pending_key()` 任务键；
  F. 队列状态的 `next_reason`（"为什么在等"）：
     `TaskDeferred.reason` → `task.defer_reason` → `queue_status.next_reason`。

用法：
    .venv/bin/python tools/test_state_freshness.py          # 全部（含 GUI 卡，需要 Qt）
    .venv/bin/python tools/test_state_freshness.py --no-gui # 跳过 GUI 部分
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import dashboard  # noqa: E402  （模块级常量，import 不会起服务）
from src import queue_status as qs  # noqa: E402
from src import scenario as scenario_mod  # noqa: E402
import scenarios.runner as runner_mod  # noqa: E402
from scenarios.runner import TaskQueueRunner  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = '') -> None:
    if cond:
        print(f'  ✓ {name}')
    else:
        print(f'  ✗ {name}  {detail}')
        FAILED.append(name)


def _clock(offset_sec: int) -> str:
    return (datetime.now() + timedelta(seconds=offset_sec)).strftime('%H:%M:%S')


def _eta_line(kind: str, offset_sec: int, log_clock: str = '01:00:00') -> str:
    """一条"进行中"登记日志（log_clock = 这行日志自己的时间戳，需早于重启标记）。"""
    return (f'[{log_clock}] {kind}: 进行中，预计 1200 秒后结束'
            f'（{_clock(offset_sec)} 收尾），先调度其他任务')


START = '[01:28:19] 调度引擎: task_queue'


# ---------------- A. queue_status 新鲜度 ----------------

def test_queue_status() -> None:
    print('A. src/queue_status.py 生命周期/新鲜度')
    with tempfile.TemporaryDirectory() as tmp:
        orig = qs.QUEUE_STATUS_FILE
        qs.QUEUE_STATUS_FILE = Path(tmp) / 'queue_status.json'
        try:
            qs.mark_starting(4242)
            st = qs.load_queue_status()
            check('mark_starting 写入 starting+pid', bool(st and st.get('starting')
                                                          and st.get('pid') == 4242))
            check('starting 状态被判为"不属于当前进程"', not qs.status_is_current(st, 4242))
            check('starting 状态清空了上一轮活动', st.get('current') == ''
                  and st.get('pending') == '')

            st = {'current': '踩踩', 'pending': '上课', 'pid': 999}
            check('旧进程写的状态（pid 不同）不算新鲜', not qs.status_is_current(st, 4242))
            check('pid 相同 = 新鲜', qs.status_is_current({**st, 'pid': 4242}, 4242))
            check('不知道 pid 时不看 starting/stopped 之外的东西',
                  qs.status_is_current(st, None))

            # 占位状态的宽限期：legacy 引擎不写队列状态，不能让"启动中"永远挂着
            check('占位 + 刚启动 → 显示启动检查中',
                  qs.startup_placeholder_active({'starting': True}, 1, 5.0))
            check('占位 + 已过宽限期 → 退回按配置展示',
                  not qs.startup_placeholder_active({'starting': True}, 1, 601.0))
            check('当前进程写的状态 → 不用占位',
                  not qs.startup_placeholder_active({'pid': 1}, 1, 5.0))

            qs.mark_stopped()
            st = qs.load_queue_status()
            check('mark_stopped 写入 stopped', bool(st and st.get('stopped')))
            check('stopped 状态不算新鲜', not qs.status_is_current(st, 4242))
            check('load_queue_status 读得回 JSON', isinstance(st, dict))
        finally:
            qs.QUEUE_STATUS_FILE = orig


# ---------------- B. dashboard work_eta 截断 ----------------

def test_work_eta() -> None:
    print('B. dashboard.work_eta() 只认本轮启动后的登记')
    stale = _eta_line('上课', 600)                       # 上一轮（01:00 登记，收尾还在未来）
    fresh = _eta_line('打工', 900, '01:29:31')            # 本轮（01:29:30 重启之后）

    # 真实日志顺序：上一轮登记 → （重启）启动标记 → 本轮启动日志
    eta = dashboard.work_eta([stale, START, '[01:28:25] 任务队列调度已启用，执行顺序: 护理 > 学习'])
    check('重启后丢掉上一轮的"上课"', eta is None, f'got {eta}')

    eta = dashboard.work_eta([stale, START, '[01:29:00] 手动停止',
                              '[01:29:10] 调度引擎: legacy', '[01:29:11] 已在主页面'])
    check('第二次重启后依然不显示旧登记', eta is None, f'got {eta}')

    eta = dashboard.work_eta([stale, START, '[01:29:30] 调度引擎: task_queue', fresh])
    check('本轮新登记的打工照常显示', bool(eta and eta['kind'] == '打工' and eta['remaining'] > 0),
          f'got {eta}')

    eta = dashboard.work_eta([stale])   # 老日志（没有启动标记）：保持原行为
    check('没有启动标记时退回旧行为', bool(eta and eta['kind'] == '上课'), f'got {eta}')

    # 跨天：本次运行是昨天启动的（标记在昨天的尾部）
    yesterday = ['[23:50:00] 调度引擎: task_queue',
                 _eta_line('冒险', 300, '23:55:00')]
    eta = dashboard.work_eta(yesterday + ['[00:05:00] 已在主页面'])
    check('跨零点：昨天的启动标记仍算本轮', bool(eta and eta['kind'] == '冒险'), f'got {eta}')

    # 启动标记不漂移：runner 里真的打了这行日志
    src = (BASE / 'scenarios' / 'runner.py').read_text(encoding='utf-8')
    check('runner.py 里有 "调度引擎: {engine}" 日志',
          bool(re.search(r"log\(f'调度引擎: \{engine\}'\)", src)))
    check('dashboard 的标记正则能匹配 runner 的这行',
          bool(dashboard.RUN_START_LOG_RE.search('[01:02:03] 调度引擎: task_queue'))
          and bool(dashboard.RUN_START_LOG_RE.search('[01:02:03] 调度引擎: legacy')))


# ---------------- C. 场景：出门分类 / 启动实测 / wait_busy_end 行为不变 ----------------

class StubScen:
    """DeviceScenario 的最小替身：只提供被测方法用到的东西。"""

    def __init__(self, classify=(), settle_text=None):
        self.classify = classify          # [(kind, settled), ...] 依次返回，用尽返回 None
        self.settle_text = settle_text
        self.defer_wait = True
        self.check_interval = 1.0
        self.pending = None
        self.screens = 0
        self.left_home = 0
        self.mains = 0
        self.quits = 0
        self.clicks = 0
        self.encouraged = 0
        self.deferred: list[str] = []
        self.waited: list[tuple] = []
        self.employed_waits = 0
        self.main_page_calls = 0

    # 基类接口
    def leave_home(self):
        self.left_home += 1

    def ensure_main_page(self):
        self.main_page_calls += 1

    def screen(self):
        self.screens += 1
        return object()

    def see(self, name, screen=None, source=None):
        if name == 'quit':
            return (10, 20)
        return None

    def click(self, x, y):
        self.clicks += 1

    def dismiss_career_popup(self, screen=None):
        return False

    def _encourage_burst(self):
        self.encouraged += 1

    def _defer_busy(self, kind, screen):
        self.deferred.append(kind)
        self.pending = {'in_name': kind, 'end_name': kind + '_end', 'desc': kind,
                        'until': datetime.now() + timedelta(seconds=60)}
        return True

    def wait_end(self, in_name, end_name, check_interval=None, encourage=False):
        self.waited.append((in_name, end_name, encourage))

    def wait_employed_back(self, check_interval=None):
        self.employed_waits += 1

    # 被测
    def _classify_busy_screen(self, screen):
        return self.classify.pop(0) if self.classify else None


def test_probe_activity_state() -> None:
    print('C. src/scenario.probe_activity_state() 启动实测')
    probe = scenario_mod.DeviceScenario.probe_activity_state

    scen = StubScen(classify=[None], settle_text=None)
    check('什么都没有 → None（并回主页面由调用方处理）', probe(scen, attempts=1) is None,
          f'left_home={scen.left_home}')

    scen = StubScen(classify=[('school', False)])
    hit = probe(scen, attempts=1)
    check('上课中 → (school, False) 且登记 pending', hit == ('school', False)
          and scen.pending is not None and scen.deferred == ['school'], f'got {hit}')

    scen = StubScen(classify=[('work', True)])
    hit = probe(scen, attempts=1)
    check('结算页 → (work, True) 且点了 quit', hit == ('work', True) and scen.clicks == 1
          and scen.encouraged == 1, f'got {hit} clicks={scen.clicks}')

    scen = StubScen(classify=[('employed', False)])
    hit = probe(scen, attempts=1)
    check('被雇佣 → (employed, False) 不阻塞等待召回',
          hit == ('employed', False) and scen.employed_waits == 0, f'got {hit}')

    scen = StubScen(classify=[None, None, ('adventure', False)])
    hit = probe(scen, attempts=3)
    check('前两轮没识别到会重试（出门加载延迟）', hit == ('adventure', False)
          and scen.screens == 3, f'got {hit} screens={scen.screens}')


def test_wait_busy_end_unchanged() -> None:
    print('C2. wait_busy_end() 重构后行为不变（结算/进行中/被雇佣）')
    wait = scenario_mod.DeviceScenario.wait_busy_end

    scen = StubScen(classify=[('school', False)])
    check('进行中 + defer_wait：登记 pending 后立即返回，不阻塞等下课',
          wait(scen, attempts=1) == 'school' and scen.deferred == ['school']
          and scen.waited == [])

    scen = StubScen(classify=[('school', False)])
    scen.defer_wait = False
    check('legacy（defer_wait=False）：仍走阻塞 wait_end',
          wait(scen, attempts=1) == 'school'
          and scen.waited == [('school_in', 'school_end', True)], f'{scen.waited}')

    scen = StubScen(classify=[('work', True)])
    check('结算页：点 quit 后返回 work（计数交给调用方）', wait(scen, attempts=1) == 'work')

    scen = StubScen(classify=[('employed', False)])
    check('被雇佣：阻塞等召回（wait_employed_back）',
          wait(scen, attempts=1) == 'employed' and scen.employed_waits == 1)

    scen = StubScen(classify=[None])
    check('四种状态都没有 → None', wait(scen, attempts=1) is None)


def test_classify_precedence() -> None:
    print('C3. _classify_busy_screen() 判定顺序')
    classify = scenario_mod.DeviceScenario._classify_busy_screen

    class S(StubScen):
        def __init__(self, settle=None, signs=()):
            super().__init__()
            self.settle = settle
            self.signs = set(signs)

        def _detect_settlement(self, screen, source):
            return self.settle

        def see(self, name, screen=None, source=None):
            return (1, 2) if name in self.signs else None

    def cls(scen):
        return classify(scen, object())

    check('结算页优先于进行中状态', cls(S(settle='work', signs=['school_in'])) == ('work', True))
    check('进行中按 school > work > adventure > employed 顺序',
          cls(S(signs=['work_in', 'school_in'])) == ('school', False)
          and cls(S(signs=['employed_in', 'adventure_in'])) == ('adventure', False))
    check('都没有 → None', cls(S()) is None)


# ---------------- D. 调度器启动实测分支 ----------------

class StubSchool:
    def __init__(self, hit, pending=None):
        self.hit = hit
        self.pending = pending
        self.ensured = 0
        self.probed = 0

    def ensure_main_page(self):
        self.ensured += 1

    def probe_activity_state(self, attempts=None):
        self.probed += 1
        if isinstance(self.hit, Exception):
            raise self.hit
        return self.hit


def _probe_runner(hit, pending=None, boom=False):
    """构造一个只够跑 _startup_activity_probe 的 TaskQueueRunner。"""
    obj = TaskQueueRunner.__new__(TaskQueueRunner)
    obj._startup_probe_done = False
    obj._current_task = None
    obj._career_check_due = False
    obj.school = StubSchool(RuntimeError('boom') if boom else hit, pending)
    obj._written: list[str | None] = []
    obj._write_queue_status = lambda tasks, order: obj._written.append(obj._current_task)
    obj._back_to_main = lambda stage: obj._written.append(f'back:{stage}')
    obj._tasks, obj._order = {}, []
    return obj


def test_startup_probe() -> None:
    print('D. TaskQueueRunner._startup_activity_probe() 四条分支')
    orig_log, orig_count = runner_mod.log, runner_mod.count_cross
    logs: list[str] = []
    counted: list[str] = []
    runner_mod.log = lambda msg: logs.append(str(msg))
    runner_mod.count_cross = lambda kind: counted.append(kind)
    try:
        # ① 当前空闲
        r = _probe_runner(None)
        ret = r._startup_activity_probe({}, [])
        check('空闲：返回 False 且明确记日志',
              ret is False and any('当前没有进行中的活动' in m for m in logs), f'{logs}')
        check('空闲：写了两次状态（启动检查前/后）',
              r._written[0] == '启动检查' and r._written[-1] is None, f'{r._written}')
        check('空闲：没有误计数', counted == [])

        # ② 活动进行中 + 已登记 pending
        logs.clear()
        r = _probe_runner(('school', False), pending={'until': datetime.now()})
        ret = r._startup_activity_probe({}, [])
        check('进行中：返回 True、不重复计数（计数在收尾时）',
              ret is True and counted == []
              and any('已登记延时收尾' in m for m in logs), f'{logs}')
        check('进行中：实测后回了主页面（不限在出门页）',
              any(str(w).startswith('back:') for w in r._written))
        check('进行中：职业哨兵不在没结算时触发', r._career_check_due is False)

        # ③ 出门撞上结算页 → 立刻按"等完活动"计数
        logs.clear()
        r = _probe_runner(('school', True))
        ret = r._startup_activity_probe({}, [])
        check('结算页：按等完活动计数（count_cross）', ret is True and counted == ['school'],
              f'{counted}')
        check('结算页：一节课结算后触发职业哨兵', r._career_check_due is True)

        # ④ 被雇佣中
        logs.clear()
        counted.clear()
        r = _probe_runner(('employed', False))
        ret = r._startup_activity_probe({}, [])
        check('被雇佣：返回 True 交给召回流程、不计数',
              ret is True and counted == [] and any('被雇佣' in m for m in logs), f'{logs}')

        # ⑤ 实测本身抛异常：只记日志，继续按配置调度
        logs.clear()
        r = _probe_runner(None, boom=True)
        ret = r._startup_activity_probe({}, [])
        check('实测异常：不抛出去、按空闲继续',
              ret is False and any('实测失败' in m for m in logs), f'{logs}')

        # ⑥ 只做一次
        logs.clear()
        r = _probe_runner(None)
        r._startup_activity_probe({}, [])
        n = len(logs)
        r._startup_activity_probe({}, [])
        check('启动实测只做一次', len(logs) == n and r.school.probed == 1)
    finally:
        runner_mod.log, runner_mod.count_cross = orig_log, orig_count


# ---------------- E. GUI 任务队列卡（可选） ----------------

def test_gui_queue_card() -> None:
    print('E. GUI 任务队列卡：状态不属于当前进程时显示"启动检查中"')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    try:
        from PyQt6.QtWidgets import QApplication
        import main as gui_mod
    except Exception as e:  # noqa: BLE001
        print(f'  - 跳过（Qt 不可用: {e}）')
        return

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        orig_file = qs.QUEUE_STATUS_FILE
        qs.QUEUE_STATUS_FILE = Path(tmp) / 'queue_status.json'
        # main.py 里是 `from src.queue_status import load_queue_status`，
        # 函数内部读的是 src.queue_status 的模块常量，所以直接改这里 x 即可
        gui_mod.MainWindow._start_all = lambda self: None
        try:
            qs.save_queue_status({'current': '', 'pending': '上课', 'pid': 111,
                                  'next': '护理', 'ready': 2, 'waiting': 3,
                                  'updated': '01:29:47'})
            win = gui_mod.MainWindow()
            win._runner_proc = type('P', (), {'poll': lambda self: None, 'pid': 222})()
            win._refresh_queue_card()
            cur = win._queue_values['current'].text()
            check('pid 对不上 → 显示启动检查中', '启动检查' in cur, f'got {cur!r}')

            qs.save_queue_status({'current': '踩踩', 'pending': '', 'pid': 222,
                                  'next': '护理', 'ready': 2, 'waiting': 3,
                                  'updated': '01:29:47'})
            win._refresh_queue_card()
            cur = win._queue_values['current'].text()
            check('pid 对上 → 显示真实当前任务', cur == '踩踩', f'got {cur!r}')

            qs.mark_starting(222)
            win._refresh_queue_card()
            cur = win._queue_values['current'].text()
            check('starting 占位状态 → 显示启动检查中', '启动检查' in cur, f'got {cur!r}')
            win.close()
        finally:
            qs.QUEUE_STATUS_FILE = orig_file


def test_main_pending_key() -> None:
    """E. `_main_pending_key()`：延时收尾写队列状态时的任务键。

    回归用例（2026-09-28 实发）：这个方法里 `desc = (pend.pending …)` 引用了**未定义的 `pend`**，
    而上面 D 段把 `_write_queue_status` 用 lambda 桩掉了，所以测试一路绿灯；真实场景里任何一次
    重启都会走「启动实测 → _write_queue_status → 本函数」，直接 NameError 把调度器崩掉。
    这里**直接调本函数**（不经桩），把这个坑焊死。
    """
    print('E. _main_pending_key()（队列状态的任务键）')
    r = _probe_runner(None)
    for name in ('adventure', 'school', 'hire_friend', 'work'):
        setattr(r, name, StubSchool(None, None))
    check('四个场景都没 pending → 空键', r._main_pending_key() == '', f'{r._main_pending_key()!r}')

    # 描述优先：启动实测借道 school 场景做检测，但登记的是"打工"这类活动
    r.school.pending = {'desc': '打工', 'until': datetime.now()}
    check('按描述认键：借道 school 登记的「打工」→ work',
          r._main_pending_key() == 'work', f'{r._main_pending_key()!r}')

    # 描述认不出 → 回落到场景归属
    r.school.pending = {'desc': '某个新活动', 'until': datetime.now()}
    check('描述认不出 → 按场景归属兜底（school）',
          r._main_pending_key() == 'school', f'{r._main_pending_key()!r}')

    # pending 没有 desc 键也不能炸（启动实测登记的 dict 结构可能不同）
    r.school.pending = {'until': datetime.now()}
    check('pending 里没有 desc → 不抛异常', r._main_pending_key() == 'school')


# ---------------- F. 队列状态的"为什么在等"（next_reason） ----------------

def _reason_runner(tasks, order, eligible=False, throttle=None):
    """构造一个只够跑 `_write_queue_status` 的 TaskQueueRunner。

    注意：调用方必须先把 `runner_mod.save_queue_status` 换成内存捕获，
    否则这里会写真实的 runs/queue_status.json。
    """
    import types as _types

    r = TaskQueueRunner.__new__(TaskQueueRunner)
    r._current_task = ''
    r._run_started_ts = 0.0
    r.visit_times = r.pk_times = r.adventure_times = 0
    r.study_quota_hours = r.work_quota_hours = 0
    r._load_durations = lambda: (0, 0)
    r._fatigue_skip = r._quota_over = r._duration_over = r._work_over = lambda *a: False
    r._main_pending_scen = lambda: None
    r._main_pending_key = lambda: ''
    r._next_daily_time = lambda times, now: None

    def _eligible_fn(t, n):
        # eligible 可以是 True/False（全体），也可以是一组任务键（只有它们可执行）
        if isinstance(eligible, bool):
            return eligible
        return t.key in eligible

    r._eligible = _eligible_fn
    r._scen_throttle_next = lambda k: (throttle or {}).get(k)
    hf = _types.SimpleNamespace(enabled=True, times_per_day=99, friend_name='墨瞳')
    r.hire_friend = _types.SimpleNamespace(cfg=_types.SimpleNamespace(hire_friend=hf))
    r.care = _types.SimpleNamespace(cfg=_types.SimpleNamespace(
        care=_types.SimpleNamespace(interval_seconds=60)))
    r.gift_bag = _types.SimpleNamespace(cfg=_types.SimpleNamespace(
        gift_bag=_types.SimpleNamespace(enabled=True, interval_seconds=1800)))
    r.friend_care = _types.SimpleNamespace(cfg=_types.SimpleNamespace(
        friend_care=_types.SimpleNamespace(enabled=True, friend_name='墨瞳')))
    r._tasks, r._order = tasks, order
    return r


def test_wait_reason() -> None:
    """F. 队列状态的 next_reason：仪表盘"等待中"下面显示的**具体**原因。

    回归背景（2026-09-30 实报）：Deck 原先只认 work_eta（只有学习/打工/冒险这类
    走延时收尾的主任务才写那种日志），所以 PK / 护理 / 雇佣好友在跑时界面说"空闲"；
    补上 `queue.current` 之后，"等待中"又只剩一句笼统的"等待下一项任务"。
    这里把"为什么在等"的整条链焊死：
    `TaskDeferred.reason` → `task.defer_reason` → `queue_status.next_reason`。
    """
    print('F. 队列状态 next_reason（"为什么在等"）')
    from src.scenario import TaskDeferred

    captured: dict = {}
    orig_save = runner_mod.save_queue_status
    runner_mod.save_queue_status = lambda st: captured.update(st)
    now = datetime.now()
    try:
        # ④ 场景层：TaskDeferred 必须把 reason 存成**属性**（只传 super() 的话
        #    调度层拿不到，等于白写）
        d = TaskDeferred(now + timedelta(seconds=60), '测试原因')
        check('TaskDeferred.reason 可读', d.reason == '测试原因', f'{d.reason!r}')

        # ① 场景主动延后（雇佣好友：想雇的人正忙）—— 原因必须原样带出来
        t = runner_mod._QueueTask('hire_friend')
        t.next_at = now + timedelta(seconds=60)
        t.defer_reason = '想雇的「墨瞳」正打工中（不换人，等到空闲再雇）'
        _reason_runner({'hire_friend': t}, ['hire_friend'])._write_queue_status(
            {'hire_friend': t}, ['hire_friend'])
        check('延后原因写进 next_reason',
              captured.get('next_reason') == '雇佣好友：想雇的「墨瞳」正打工中（不换人，等到空闲再雇）',
              f'{captured.get("next_reason")!r}')
        check('next 仍是任务名（前端拼"下一项"用）',
              captured.get('next') == '雇佣好友', f'{captured.get("next")!r}')

        # ② 场景级节流（护理间隔未到）
        t2 = runner_mod._QueueTask('care')
        _reason_runner({'care': t2}, ['care'], eligible=True,
                       throttle={'care': now + timedelta(seconds=30)})._write_queue_status(
            {'care': t2}, ['care'])
        check('节流原因带间隔说明',
              captured.get('next_reason') == '护理：护理间隔未到（每 60 秒巡检一次）',
              f'{captured.get("next_reason")!r}')

        # ③ 失败退避（没有场景原因时，给笼统但可行动的退避文案）
        t3 = runner_mod._QueueTask('pk')
        t3.next_at = now + timedelta(seconds=120)
        _reason_runner({'pk': t3}, ['pk'])._write_queue_status({'pk': t3}, ['pk'])
        check('退避原因', captured.get('next_reason') == 'PK：上次未成功，退避重试中',
              f'{captured.get("next_reason")!r}')

        # ③b **阻塞原因优先于"最近的等待点"**：护理 30 秒后就到期、雇佣好友要等
        #     30 分钟 —— 显示的必须是后者（用户实报：一直被"等护理到点"刷屏，
        #     而主任务其实在等雇佣结束）
        tc = runner_mod._QueueTask('care')
        th = runner_mod._QueueTask('hire_friend')
        th.next_at = now + timedelta(minutes=30)
        th.defer_reason = '宠物正在被雇佣（剩余约 45 分钟），雇佣好友延后到 01:33:29'
        pair = {'care': tc, 'hire_friend': th}
        _reason_runner(pair, ['care', 'hire_friend'], eligible={'care'},
                       throttle={'care': now + timedelta(seconds=30)})._write_queue_status(
            pair, ['care', 'hire_friend'])
        check('阻塞原因优先于最近等待点（不是"等护理"）',
              '宠物正在被雇佣' in (captured.get('next_reason') or ''),
              f'{captured.get("next_reason")!r}')
        check('next 仍是最近等待点（护理）', captured.get('next') == '护理',
              f'{captured.get("next")!r}')

        # ④ 执行成功后延后原因必须清空（否则界面会一直挂着上次的雇佣 CD 文案）
        t.defer_reason = ''
        t.next_at = now
        _reason_runner({'hire_friend': t}, ['hire_friend'], eligible=True)._write_queue_status(
            {'hire_friend': t}, ['hire_friend'])
        check('成功后不再复用旧的延后原因',
              '打工中' not in (captured.get('next_reason') or ''),
              f'{captured.get("next_reason")!r}')
    finally:
        runner_mod.save_queue_status = orig_save


def main() -> int:
    ap = argparse.ArgumentParser(description='状态新鲜度离线测试（不连设备）')
    ap.add_argument('--no-gui', action='store_true', help='跳过 GUI 任务队列卡测试')
    args = ap.parse_args()

    # 场景基类里的 sleep 让测试变慢：本进程内直接去掉（只影响本测试进程）
    import time as _time
    _time.sleep = lambda *a, **k: None  # noqa: E731
    scenario_mod.time.sleep = _time.sleep
    # **别污染调度器日志**：scenario 的 log() 会写 runs/logs/<date>.log（仪表盘"实时日志"
    # 读的就是它），测试里那些"检测到正在上课/出门后检测到 xx 结算页"会被当成真事。
    scenario_mod.log = lambda *a, **k: None

    test_queue_status()
    test_work_eta()
    test_probe_activity_state()
    test_wait_busy_end_unchanged()
    test_classify_precedence()
    test_startup_probe()
    test_main_pending_key()
    test_wait_reason()
    if not args.no_gui:
        test_gui_queue_card()

    print()
    if FAILED:
        print(f'❌ {len(FAILED)} 项失败: {FAILED}')
        return 1
    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
