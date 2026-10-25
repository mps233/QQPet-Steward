#!/usr/bin/env python3
"""QQ 宠物托管 · 手机仪表盘（纯标准库，零依赖）。

读取 runs/ 下的日志、进度、状态、队列文件，提供手机端页面：

    GET /             手机页面（自动刷新）
    GET /api/data     汇总数据 JSON
    GET /api/adventure 冒险记录 JSON（统一数据源，实时曲线）
    GET /api/logs     实时日志尾部 JSON（?tail=250）
    GET /files/<png>  异常截图
    POST /api/runner/start  启动调度器（独立后台进程）
    POST /api/runner/stop   停止调度器（SIGINT 优雅收尾退出）

启动:  python3 dashboard.py [--port 8787]
访问:  http://<本机内网IP>:8787
"""

import errno
import gzip
import hmac
import ipaddress
import io
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from src.config import APP_ROOT, RESOURCE_ROOT
from src.client_protocol import (PROTOCOL_VERSION, ClientProtocolStore,
                                 InvalidPairingTicket, PairingAttemptLimiter,
                                 ProtocolStoreError, candidate_base_urls,
                                 client_meta, pairing_payloads)
from src.dashboard_auth import (COOKIE_NAME, LOGIN_HTML, MIN_KEY_LENGTH, authorize,
                                bearer_credential, cors_granted,
                                is_custom_key, load_access_key, same_origin,
                                save_access_key, session_value, validate_new_key)

BASE = RESOURCE_ROOT

RUNS = APP_ROOT / 'runs'
RUNNER_PID = RUNS / 'runner.pid'      # 调度器自己写的单实例登记（见 src/instance.py）
LOCAL_CONTROL_HEADER = 'X-QPet-Control'
LOCAL_CONTROL_FILE = RUNS / 'desktop_control.token'
LOCAL_CONTROL_HOST = '127.0.0.1'

sys.path.insert(0, str(BASE))          # 让 `import src.instance` 在源码运行/打包都成立
from src import instance  # noqa: E402  （判活工具：pidfile + os.kill(pid,0)，不依赖 ps）
from src.jsonio import write_json, write_text_atomic  # noqa: E402  （统一原子写）
from src import runtime_process  # noqa: E402  （跨平台调度器启动/停止）

try:                                   # 队列状态的"已停止"标记（字段格式见 src/queue_status.py）
    from src.queue_status import mark_stopped as mark_queue_stopped  # noqa: E402
except Exception as _qs_err:           # 缺 ruamel/yaml 等极端环境：退化为本地写同一份格式
    print(f'[dashboard] src.queue_status 不可用（停止时改本地写）：{_qs_err}', flush=True)

    def mark_queue_stopped() -> None:
        # 降级副本：正常路径用的是 src.queue_status.mark_stopped（见上方 import）。
        # 字段必须与 src/queue_status.py 的 mark_stopped 保持一致 —— 这里原先少了
        # next_reason / note，导致前端在这条路径下读到 undefined（见
        # docs/review/07-进程间契约.md S4 的 schema 漂移）。
        # 也改为原子写（tmp + os.replace）：消费端可能恰好读到这里写的文件。
        try:
            from src.jsonio import write_json
            write_json(RUNS / 'queue_status.json', {
                'current': '', 'pending': '', 'next': '', 'next_reason': '', 'note': '',
                'next_at': '', 'next_ts': 0,
                'ready': 0, 'waiting': 0, 'tasks': {},
                'stopped': True, 'pid': None,
                'updated': datetime.now().strftime('%H:%M:%S'),
            })
        except OSError:
            pass

LOGS = RUNS / 'logs'
ADV_LIVE_FILE = RUNS / 'adventure_live.jsonl'   # 冒险统一记录（实验 300 把 + 日常实时）
DEFAULT_PORT = 8787
_SETTINGS_LOCK = threading.RLock()
_ONBOARDING_LOCK = threading.RLock()
_CLIENT_STORE_LOCK = threading.RLock()
_CLIENT_PROTOCOL_STORE: ClientProtocolStore | None = None


def _default_client_store() -> ClientProtocolStore:
    """Process-wide default store; tests/servers may inject their own instance."""
    global _CLIENT_PROTOCOL_STORE
    with _CLIENT_STORE_LOCK:
        if _CLIENT_PROTOCOL_STORE is None:
            _CLIENT_PROTOCOL_STORE = ClientProtocolStore(RUNS / 'client_protocol.json')
        return _CLIENT_PROTOCOL_STORE


def set_client_protocol_store(store) -> None:
    """Install a protocol store (used by the server bootstrap and tests)."""
    global _CLIENT_PROTOCOL_STORE
    with _CLIENT_STORE_LOCK:
        _CLIENT_PROTOCOL_STORE = store

# ---- 静态资源的传输优化（见 docs/review/03-p0-交付链与安全.md B/C 节）----
# 小于此不压：省 CPU，收益也小。
_GZIP_MIN = 1024
# 只压文本类。png/jpg/woff2/mp4 本身已压缩，再压纯属浪费 CPU。
_GZIP_TYPES = re.compile(
    r'^(text/|application/(javascript|json|manifest\+json)|image/svg\+xml)')
# 带内容 hash 的 Vite 产物名（如 index-BYrpw4Vd.js）→ 内容变 URL 就变，可长缓存。
# 不带 hash 的（/next/index.html 之类兼容别名）仍走 no-store，否则用户永远停在旧版本。
_HASHED_ASSET_RE = re.compile(r'-[A-Za-z0-9_-]{8,}\.')
CACHE_IMMUTABLE = 'public, max-age=31536000, immutable'
CACHE_NONE = 'no-store'

# ---- PWA：应用清单 + Service Worker（离线兜底；SW 需 https/localhost 才注册）----
MANIFEST_JSON = json.dumps({
    'name': 'QQ宠物托管', 'short_name': 'QQ宠物',
    'start_url': '/', 'scope': '/', 'display': 'standalone',
    # 暖色房间底（与 CSS --bg / 背景图顶部一致）。历史值是冷灰 #f6f7f9，
    # 是暖色改版前留下的，会让启动闪屏/状态栏与页面割裂。
    'background_color': '#FCF7ED', 'theme_color': '#D5A758',
    'icons': [
        {'src': '/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
        {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png',
         'purpose': 'any maskable'},
    ],
}, ensure_ascii=False)

SW_JS = """// 迁到 React 版后不再需要离线缓存（界面本来就要求联网：数据来自 /api、画面来自 HLS 流）。
// 这个文件留着只为让老客户端上已注册的 Service Worker 能自我清理（清缓存 + 注销）。
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(ks => Promise.all(ks.map(k => caches.delete(k))))
    .then(() => self.registration.unregister())
    .catch(() => {})
));
self.addEventListener('fetch', e => {
  if (e.request.mode === 'navigate') e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});"""


# ---------------------------------------------------------------- 数据读取

def list_friends() -> list[str]:
    """读调度器写的好友名单缓存（runs/friends_cache.json）里的名字列表。

    缓存由 scenarios/visit.py 扫好友时写入，数据源是好友列表控件的
    content-desc（"好友 墨瞳"）—— 即**主人昵称**，非宠物名。
    """
    try:
        data = json.loads((RUNS / 'friends_cache.json').read_text('utf-8'))
        names = data.get('names') or []
        return [str(n) for n in names if str(n).strip()]
    except (OSError, ValueError):
        return []


def friends_meta() -> dict:
    """好友缓存的元信息（来源/更新时间/说明），供前端展示。"""
    try:
        data = json.loads((RUNS / 'friends_cache.json').read_text('utf-8'))
        return {'source': data.get('source') or '', 'note': data.get('note') or '',
                'updated': data.get('updated') or ''}
    except (OSError, ValueError):
        return {'source': '', 'note': '', 'updated': ''}


def read_json(name: str) -> dict:
    try:
        return json.loads((RUNS / name).read_text('utf-8'))
    except Exception:
        return {}


def _has_onboarding_history() -> bool:
    """Recognize actual use; first-launch config, logs and empty state do not count."""
    cache = read_json('status_cache.json')
    accounts = cache.get('accounts') if isinstance(cache, dict) else None
    if isinstance(accounts, dict):
        for entry in accounts.values():
            if isinstance(entry, dict) and any(
                    entry.get(key) is not None and entry.get(key) != ''
                    for key in ('pet_name', 'energy', 'clean', 'mood',
                                'coins', 'soap', 'biscuit')):
                return True

    for path in RUNS.glob('*_progress.json'):
        progress = read_json(path.name)
        if not isinstance(progress, dict):
            continue
        if (any(_int_or_0(progress.get(key)) > 0
                for key in ('learned', 'study_secs', 'work_secs'))
                or progress.get('done') is True):
            return True
        history = progress.get('history')
        if isinstance(history, dict) and any(_int_or_0(value) > 0
                                             for value in history.values()):
            return True

    queue = read_json('queue_status.json')
    if isinstance(queue, dict) and (queue.get('current') or queue.get('pending')):
        return True
    for name in ('session_rewards.jsonl', 'adventure_live.jsonl'):
        try:
            with (RUNS / name).open(encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict) and any(
                            key in record for key in ('kind', 'coins', 'settle')):
                        return True
        except OSError:
            pass
    return False


def onboarding_status() -> dict:
    state = read_json('onboarding.json')
    completed = (isinstance(state, dict) and state.get('version') == 1
                 and state.get('completed') is True)
    return {'version': 1, 'required': not completed and not _has_onboarding_history(),
            'completed': completed}


def complete_onboarding() -> dict:
    with _ONBOARDING_LOCK:
        state = read_json('onboarding.json')
        if not (isinstance(state, dict) and state.get('version') == 1
                and state.get('completed') is True):
            write_json(RUNS / 'onboarding.json', {
                'version': 1, 'completed': True,
                'completed_at': datetime.now().isoformat(timespec='seconds'),
            })
    return {'ok': True, 'onboarding': onboarding_status()}


_SECRET_KEY_RE = re.compile(
    r'(token|secret|password|passwd|pwd|api_?key|webhook|chat_id|send_?key|bark_?key)',
    re.IGNORECASE,
)
_SECRET_VAL_RE = re.compile(r'(\d{6,}:[A-Za-z0-9_-]{20,})')   # Telegram bot token 形态


def _redact(msg: str) -> str:
    """抹掉审计日志里的凭据（token/webhook/chat_id 等）。

    设置保存会把提交内容整条记进 runs/logs/dashboard.log，凭据因此长期明文落盘
    （曾把 Telegram bot token 完整写进日志）。token 等同渠道控制权，必须脱敏。
    """
    # 形态匹配：Telegram token 这类"数字:长串"无论键名如何都抹掉
    msg = _SECRET_VAL_RE.sub(lambda m: m.group(1)[:4] + '***已隐藏***', msg)
    # 键值匹配：'key': 'value' / key=value / "key": "value"
    def _sub_kv(m):
        return f'{m.group(1)}{m.group(2)}{m.group(3)}***已隐藏***{m.group(4)}'
    return re.sub(
        r"(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(\s*[:=]\s*)(['\"])([^'\"]*)\4",
        lambda m: _sub_kv(m) if _SECRET_KEY_RE.search(m.group(2)) else m.group(0),
        msg,
    )


def audit(msg: str) -> None:
    """关键操作审计日志（谁什么时候改了什么），写 runs/logs/dashboard.log（凭据自动脱敏）。"""
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with open(LOGS / 'dashboard.log', 'a', encoding='utf-8') as f:
            f.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {_redact(str(msg))}\n')
    except Exception:
        pass


#: 访问码校验失败限流：自定义访问码可以短到 8 位，必须挡住局域网内的穷举。
AUTH_FAIL_LIMIT = 8
AUTH_FAIL_WINDOW = 300
#: RLock：改访问码时要在同一临界区里写文件、换 server 上的访问码、
#: 再清掉本机失败计数，而 auth_clear_failures() 自己也要拿这把锁（否则自锁死）。
AUTH_LOCK = threading.RLock()
AUTH_FAILURES: dict[str, list[float]] = {}

#: 客户端配对失败限流（默认每来源 5 分钟 8 次）。按来源 IP 记账，
#: 与访问码登录限流分开，避免一个泄露的配对票据拖垮浏览器登录。
PAIRING_FAILURES = PairingAttemptLimiter(limit=8, window=300)


def auth_retry_after(ip: str) -> int:
    """还剩多少秒处于锁定中；0 = 现在可以尝试。"""
    now = time.time()
    with AUTH_LOCK:
        stamps = [t for t in AUTH_FAILURES.get(ip, []) if now - t < AUTH_FAIL_WINDOW]
        if stamps:
            AUTH_FAILURES[ip] = stamps
        else:
            AUTH_FAILURES.pop(ip, None)
        if len(stamps) < AUTH_FAIL_LIMIT:
            return 0
        return max(1, int(AUTH_FAIL_WINDOW - (now - stamps[-1])))


def auth_record_failure(ip: str) -> None:
    now = time.time()
    with AUTH_LOCK:
        stamps = [t for t in AUTH_FAILURES.get(ip, []) if now - t < AUTH_FAIL_WINDOW]
        stamps.append(now)
        AUTH_FAILURES[ip] = stamps[-AUTH_FAIL_LIMIT * 2:]


def auth_clear_failures(ip: str) -> None:
    with AUTH_LOCK:
        AUTH_FAILURES.pop(ip, None)


def scheduler_info() -> dict:
    pids = _runner_pids()
    uptime = ''
    if pids:
        info = instance.read_pidfile(RUNNER_PID)
        if info and info['pid'] == pids[0] and info.get('started'):
            secs = max(0, int(time.time() - float(info['started'])))
            uptime = f'{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}'
        else:
            try:      # 没有 pidfile 的老进程：退回 ps（ps 不可用就算了，不显示运行时长）
                uptime = subprocess.run(['ps', '-o', 'etime=', '-p', str(pids[0])],
                                        capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:
                pass
    return {'alive': bool(pids), 'pid': pids[0] if pids else None, 'uptime': uptime}


def _runner_pids() -> list[int]:
    """调度器进程 PID 列表 —— **判活只信内核，不调 ps 读命令行**。

    踩过的坑（2026-09-28）：原来用 `pgrep -f` 拿 pid、再 `ps -p <pid> -o command=` 复核，
    可一旦 `ps` 不可用（受限沙箱/权限被拒），复核拿到的命令行是空串 → 所有 pid 被过滤掉 →
    `alive` 恒为 False：① 「已在运行不重复启动」的守卫失效，点一次「启动」就多起一个调度器
    （实测一口气 3 个，同时操作同一部手机）；② 「停止」也不认账（返回"本来就没有在运行"）。
    现在统一走 src/instance.find_running：pidfile（调度器自己写的）+ 内核判活，
    再补一遍 pgrep 兜底（覆盖旧版本起的进程，命令行做严格校验，防 shell 误配）。
    """
    return [r['pid'] for r in instance.find_running(RUNNER_PID)]


def _runtime_data_paths() -> runtime_process.UserDataPaths:
    """Use the active data root, not always the source checkout."""
    data_root = APP_ROOT
    frozen = bool(getattr(sys, 'frozen', False))
    portable = bool(os.environ.get(runtime_process.PORTABLE_ENV))
    return runtime_process.user_data_paths(
        data_root, frozen=frozen, portable=portable)


def _local_control_key() -> str:
    """Load or create the private token used by the macOS menu-bar host.

    This is deliberately separate from the browser access key: the desktop
    host may read live state without copying an HttpOnly cookie, and a leaked
    browser cookie cannot be replayed as a local control credential.
    """
    LOCAL_CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(LOCAL_CONTROL_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return LOCAL_CONTROL_FILE.read_text('utf-8').strip()
    key = secrets.token_urlsafe(32)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(key + '\n')
    LOCAL_CONTROL_FILE.chmod(0o600)
    return key


def _is_loopback_client(address: str) -> bool:
    try:
        return ipaddress.ip_address(str(address)).is_loopback
    except ValueError:
        return False


def _local_control_is_loopback(handler) -> bool:
    return _is_loopback_client(handler.client_address[0])


def _local_control_authorized(handler) -> bool:
    credential = str(handler.headers.get(LOCAL_CONTROL_HEADER) or '')
    if not _local_control_is_loopback(handler) or not credential:
        return False
    key = _local_control_key()
    return len(key) >= 32 and hmac.compare_digest(credential, key)


def scheduler_start() -> dict:
    """从仪表盘启动调度器：独立会话进程，输出追加到 runs/runner_console.log。

    Python 解释器/冻结版 scheduler 的选择统一由 runtime_process 负责；
    已在运行时不重复启动（防多实例——曾踩过双开尾巴的坑）。
    """
    info = scheduler_info()
    if info['alive']:
        return {'ok': False, 'msg': f"调度器已在运行（PID {info['pid']}），不重复启动"}
    try:
        RUNS.mkdir(parents=True, exist_ok=True)
        # Source mode must use absolute paths: the dashboard and scheduler may
        # be started with different working directories, and Windows cannot
        # resolve the POSIX `.venv/bin/python` shim.
        command = runtime_process.build_scheduler_argv(BASE.resolve())
    except Exception as e:
        return {'ok': False, 'msg': f'无法准备调度器命令: {e}'}
    try:
        runtime_process.spawn_detached_scheduler(
            command,
            cwd=APP_ROOT,
            log_path=RUNS / 'runner_console.log',
            env=runtime_process.scheduler_environment(
                _runtime_data_paths(),
                portable=bool(os.environ.get(runtime_process.PORTABLE_ENV)),
            ),
        )
    except Exception as e:
        return {'ok': False, 'msg': f'启动失败: {e}'}
    deadline = time.time() + 8
    while time.time() < deadline:
        time.sleep(0.5)
        info = scheduler_info()
        if info['alive']:
            audit('调度器启动（来自仪表盘）')
            return {'ok': True, 'msg': f"已启动（PID {info['pid']}）", 'scheduler': info}
    return {'ok': False, 'msg': '启动后 8 秒内未检测到进程，请看 runs/runner_console.log'}


PAUSE_SIGNAL = RUNS / 'pause.signal'


def scheduler_pause() -> dict:
    """写暂停信号，让调度器在安全点让路（不退出进程、pending 与进度全保留）。

    入口是 Telegram 机器人的 `/pause`（原网页「接管操作」已移除，但让路机制保留）。
    """
    try:
        # 原子写：调度器靠 mtime 判"信号是否新鲜"，写到一半被读会拿到半截时间戳。
        # os.replace 用的是 tmp 的 mtime（即刚写入的时刻），语义不变。
        write_text_atomic(PAUSE_SIGNAL, str(time.time()))
    except OSError as e:
        return {'ok': False, 'msg': f'写暂停信号失败：{e}'}
    audit('调度器让路（来自仪表盘）')
    return {'ok': True, 'msg': '已请求让路（调度器在下一个安全点暂停）',
            'scheduler': scheduler_info()}


def scheduler_resume() -> dict:
    try:
        PAUSE_SIGNAL.unlink(missing_ok=True)
    except OSError as e:
        return {'ok': False, 'msg': f'清除暂停信号失败：{e}'}
    audit('调度器恢复（交还控制，来自仪表盘）')
    return {'ok': True, 'msg': '已交还控制权，调度器继续'}


def scheduler_stop() -> dict:
    """停止调度器：先请求优雅退出，超时再逐级强制；平台差异见
    ``src/runtime_process.stop_process``（POSIX SIGINT→TERM→KILL，
    Windows CTRL_BREAK→taskkill /T→/F）。"""
    try:
        PAUSE_SIGNAL.unlink(missing_ok=True)   # 停调度器时清掉暂停信号，避免残留挂住下次启动
    except OSError:
        pass
    pids = _runner_pids()
    if not pids:
        mark_queue_stopped()   # 顺手把可能残留的"进行中"状态标成已停止
        return {'ok': True, 'msg': '调度器本来就没有在运行', 'scheduler': scheduler_info()}
    stopped = [runtime_process.stop_process(pid) for pid in pids]
    forced = any(item.forced for item in stopped)
    left = _runner_pids()
    audit(f"调度器停止（来自仪表盘）: {pids} → 剩余 {left or '无'}"
          f"{'（含强制）' if forced else ''}")
    if left:
        return {'ok': False, 'msg': f'仍有进程未退出：{left}', 'scheduler': scheduler_info()}
    # 已停：队列状态标"已停止"，否则界面（本页/手机端）会继续引用上一轮的
    # "上课（进行中）"、日志里那条旧收尾时间也还在
    mark_queue_stopped()
    return {'ok': True,
            'msg': f"已停止（PID {'、'.join(map(str, pids))}）" + ('，有进程被强制结束' if forced else ''),
            'scheduler': scheduler_info()}


def today_log_path():
    p = LOGS / (datetime.now().strftime('%Y-%m-%d') + '.log')
    if p.is_file():
        return p
    files = sorted(LOGS.glob('*.log'))
    return files[-1] if files else None


def cross_day_log_lines(lines: list[str], path) -> list[str]:
    """跨天活动的日志兜底：今天的日志里没有"进行中"登记时，接上前一天的尾部。

    日志按天分文件（`runs/logs/YYYY-MM-DD.log`），而打工/上课常跨零点
    （如 23:39 登记"检测到正在打工，预计…（00:24:37 收尾）"）——跨天后那条记录
    就不在今天文件里了，`work_eta()` 找不到 → 界面误显示"等待中"
    （用户实报"明明还在打工呢"）。只给 work_eta 用，不影响 last_line / 今日时长。
    """
    if path is None or any(re.search(r'进行中，预计|检测到正在', ln) for ln in lines):
        return lines
    prev = path.parent / (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d.log')
    return (tail_lines(prev, 400) + lines) if prev.is_file() else lines


def tail_lines(path, n: int = 250) -> list[str]:
    """高效读取文件尾部（最多回读 400KB）。"""
    size = path.stat().st_size
    chunk = min(size, 400_000)
    with open(path, 'rb') as f:
        f.seek(size - chunk)
        data = f.read().decode('utf-8', 'replace')
    lines = data.splitlines()
    if chunk < size and lines:
        lines = lines[1:]
    return lines[-n:]


#: 调度器"本次启动"的日志标记：`scenarios/runner.py` 的 run_scheduler() 每次启动都会
#: 先打一条 `调度引擎: task_queue|legacy`（在设备连接/启动检查之前），全项目只此一处。
#: **改动 runner 的这行日志时必须同步这里**（tools/test_state_freshness.py 有守卫）。
RUN_START_LOG_RE = re.compile(r'调度引擎:\s*(?:task_queue|legacy)')


def lines_since_runner_start(lines: list[str]) -> list[str]:
    """只保留"最近一次调度器启动之后"的日志行（找不到启动标记就原样返回）。

    重启会丢掉调度器内存里的 pending（"正在上课/打工"的登记），上一轮日志里那条
    "进行中，预计…收尾"就作废了——不截断的话界面会继续显示"上课中 / 剩余 xx 分钟"
    （用户实报："宠物去上课了，我手动召回，停掉调度器再启动，显示还是在上课"）。
    """
    for idx in range(len(lines) - 1, -1, -1):
        if RUN_START_LOG_RE.search(lines[idx]):
            return lines[idx:]
    return lines


def work_eta(lines: list[str]):
    """从日志里找最后一次"进行中"登记，算出剩余秒数 + 场景名（kind）。

    **两种日志格式都要认**（都代表"已登记 pending、先调度其他任务"，只是来源不同）：
      ① `冒险: 进行中，预计 44 秒后结束（…收尾）` —— scenario.defer_busy_end（场景主动延时收尾）
      ② `检测到正在打工，预计 2681 秒后结束（…收尾）` —— scenario.detect_busy_remaining
        （出门预检/被雇佣召回等路径）
    早期只匹配 ①，导致手动切到打工后界面仍停在更早那条"冒险: 进行中"上——
    而那条的结束时间早过了，于是显示"冒险中 / 收尾中"（用户实报的 bug）。

    **只看本次调度器启动之后的登记**（见 lines_since_runner_start）：重启后上一轮的
    登记已经作废，继续展示会让用户以为宠物还在上课/打工。
    """
    m = None
    for ln in lines_since_runner_start(lines)[-400:]:
        mm = re.search(r'(?:([^\[\]:：]{1,8})[:：]\s*进行中|检测到正在([^，,]{1,10}))'
                       r'，预计 (\d+) 秒后结束'
                       r'（(\d{2}):(\d{2}):(\d{2}) 收尾）', ln)
        if mm:
            m = mm
    if not m:
        return None
    kind = (m.group(1) or m.group(2)).strip()
    hh, mi, ss = m.group(4), m.group(5), m.group(6)
    now = datetime.now()
    target = now.replace(hour=int(hh), minute=int(mi), second=int(ss), microsecond=0)
    # 跨天兜底：日志里的收尾时间可能是**次日凌晨**（如 23:39 记录、00:24 收尾），
    # 而 replace 只能拼出"今天 00:24"（已过去近 24 小时），会被下面的 rem<-600 误判成
    # 过期而返回 None。超过 12 小时就当成明天同一时刻。
    if target < now - timedelta(hours=12):
        target += timedelta(days=1)
    rem = int((target - now).total_seconds())
    if rem < -600:
        return None
    return {'eta_clock': f'{hh}:{mi}:{ss}',
            'remaining': max(0, rem),
            'kind': kind}


# 游戏机制：学习+打工合计时长的收益效率档（与 scenarios/runner.py 的
# efficiency_tiers 保持一致；门槛可用 schedule.efficiency_tier*_hours 覆盖）。
# dashboard 是独立进程、不导入 runner，故此处自带一份默认值。
DEFAULT_EFFICIENCY_TIERS = ((12 * 3600, 10), (8 * 3600, 25))


def efficiency_tiers_of(sched: dict):
    """按 config 的 schedule 段生成 (秒门槛, 效率%) 序列；缺失时用默认值。"""
    t1 = sched.get('efficiency_tier1_hours')
    t2 = sched.get('efficiency_tier2_hours')
    if t1 is None and t2 is None:
        return DEFAULT_EFFICIENCY_TIERS
    try:
        t1 = 8 if t1 is None else int(t1)
        t2 = 12 if t2 is None else int(t2)
    except (TypeError, ValueError):
        return DEFAULT_EFFICIENCY_TIERS
    tiers = []
    if t2 > 0:
        tiers.append((t2 * 3600, 10))
    if t1 > 0:
        tiers.append((t1 * 3600, 25))
    return tuple(tiers) or DEFAULT_EFFICIENCY_TIERS


def today_duration(lines: list[str], tiers=None):
    """最后一条“今日时长: 已学习 X 分钟 + 已打工 Y 分钟”的 X/Y + 效率档。

    效率档（游戏机制，与 scenarios/runner.py 的 efficiency_tiers 一致）：
    学习+打工合计 >= tier2 小时 10%、>= tier1 小时 25%、否则 100%。
    门槛可配（schedule.efficiency_tier*_hours），tiers 传入 (秒门槛, 效率%) 序列。
    额外返回距下一档还有多少分钟，供界面提示"还能跑多久降档"。"""
    m = None
    for ln in lines[-400:]:
        mm = re.search(r'今日时长: 已学习 (\d+) 分钟 \+ 已打工 (\d+) 分钟', ln)
        if mm:
            m = mm
    if not m:
        return None
    learn_min, work_min = int(m.group(1)), int(m.group(2))
    total_min = learn_min + work_min
    total_s = total_min * 60
    if tiers is None:
        tiers = DEFAULT_EFFICIENCY_TIERS
    eff = 100
    for threshold_s, pct in tiers:
        if total_s >= threshold_s:
            eff = pct
            break
    nxt = None
    for ts, p in tiers:
        if total_s < ts and (nxt is None or ts < nxt[0]):
            nxt = (ts, p)
    return {
        'learn_min': learn_min, 'work_min': work_min, 'eff_pct': eff,
        'total_min': total_min,
        'next_pct': nxt[1] if nxt else None,
        'next_in_min': (nxt[0] - total_s) // 60 if nxt else None,
    }


def _task_enabled_map(tasks: dict, friend_care: dict, gift_bag: dict,
                      hire_friend: dict) -> dict:
    """各任务的"配置启用状态"，等价于 runner 写队列快照时的 disabled 判定：
    任务级 tasks.<key>.enabled + 场景级开关（好友护理/福袋/雇佣好友还有额外条件：
    好友护理需 friend_name、雇佣好友需 times_per_day+friend_name、福袋看场景 enabled）。
    供调度器停止时队列卡显示——避免用旧快照里的 disabled 状态误导。"""
    out = {}
    for k, v in (tasks or {}).items():
        if not isinstance(v, dict) or 'enabled' not in v:
            continue
        ok = bool(v.get('enabled', True))
        if k == 'friend_care':
            ok = ok and bool(friend_care.get('enabled', False)) \
                 and bool(str(friend_care.get('friend_name') or '').strip())
        elif k == 'gift_bag':
            ok = ok and bool(gift_bag.get('enabled', True))
        elif k == 'hire_friend':
            ok = (ok and bool(hire_friend.get('enabled', False))
                  and bool(int(hire_friend.get('times_per_day') or 0))
                  and bool(str(hire_friend.get('friend_name') or '').strip()))
        out[k] = ok
    return out


def config_summary() -> dict:
    try:
        import yaml  # venv 里有；缺失时返回空
        cfg = yaml.safe_load((APP_ROOT / 'config.yaml').read_text('utf-8')) or {}
    except Exception:
        return {}
    tasks = cfg.get('tasks') or {}
    sched = cfg.get('schedule') or {}
    work = cfg.get('work') or {}
    adv = cfg.get('adventure') or {}
    visit = cfg.get('visit') or {}
    pk = cfg.get('pk') or {}
    care = cfg.get('care') or {}
    friend_care = cfg.get('friend_care') or {}
    gift_bag = cfg.get('gift_bag') or {}
    hire_friend = cfg.get('hire_friend') or {}
    recover = cfg.get('recover') or {}
    adb = cfg.get('adb') or {}
    control = cfg.get('control') or {}
    notify = cfg.get('notify') or {}
    runner = cfg.get('runner') or {}

    # 通知渠道摘要（设置页"通知"行）：列出真正已启用的渠道 + 事件开关状态
    def _notify_channels_label(nt: dict) -> str:
        chans = []
        if nt.get('feishu_enabled') and str(nt.get('feishu_webhook') or '').strip():
            chans.append('飞书')
        if nt.get('telegram_enabled') and str(nt.get('telegram_token') or '').strip():
            chans.append('Telegram')
        if nt.get('win_toast', True):
            chans.append('桌面通知')
        if str(nt.get('onepush_config') or '').strip():
            chans.append('OnePush')
        head = ' + '.join(chans) if chans else '未启用任何渠道'
        kinds = []
        if nt.get('quota_done', True) and nt.get('event_notify', True):
            kinds.append('收工总账/冒险日报')
        if nt.get('career_notify', True):
            kinds.append('职业解锁')
        return head + ('（推送：' + '、'.join(kinds) + '）' if kinds else '（不推送事件）')
    school_enabled = bool((tasks.get('school') or {}).get('enabled', True))

    def n_per_day(n):
        return '不限次' if not n else f'{n} 次/天'

    # 今日策略：按**配额**判断，而不是只看 school_enabled——配额才是当天实际安排
    # （work_quota=0 就是"今天不打工"，此时不该再写"学习 + 打工"）
    sq = sched.get('study_quota_hours', 0) or 0
    wq = sched.get('work_quota_hours', 0) or 0
    work_on = bool((tasks.get('work') or {}).get('enabled', True))
    if wq <= 0 or not work_on:
        strategy = '只学习' if (sq > 0 and school_enabled) else '不学习不打工'
    elif sq <= 0 or not school_enabled:
        strategy = '只打工'
    else:
        strategy = f'学习 {sq}h + 打工 {wq}h'
    stop_h = stop_total_hours(sched)   # 合计停止点（学习+打工合计满则两项一起停）

    rows = [
        ['调度策略', strategy],
        ['调度引擎', str(runner.get('engine', 'task_queue'))],
        ['打工', f"{work.get('location', '')} · {work.get('duration', '')} · {n_per_day(work.get('times_per_day'))}"],
        ['金币阈值', f"{sched.get('coin_threshold', '-')}（低于优先打工）"],
        # 合计停止点：学习+打工合计满即两项一起停（不再分"停学习/停打工"两个数，
        # 见设置页「合计停止点与收益档」卡片与 stop_total_hours()）
        ['合计停止点', (f"{stop_h} 小时/天（学习+打工合计满则全停，转冒险）"
                    if stop_h else '不限')],
        ['福袋', f"{'启用' if (cfg.get('gift_bag') or {}).get('enabled', True) else '未启用'}"
                 f" · 每 {(cfg.get('gift_bag') or {}).get('interval_seconds', '-')} 秒扫描"],
        ['结算补贴', '每次打工/雇佣好友结束自动领「看视频 +N」金币补贴'
                     if (sched.get('claim_ad_bonus', True)) else '已关闭'],
        ['踩踩', f"{visit.get('times_per_day', '-')} 次/天 @ {visit.get('start_time', '')}"],
        ['PK', f"{pk.get('times_per_day', '-')} 次/天 @ {pk.get('start_time', '')}"],
        ['冒险', f"{adv.get('times_per_day', '-')} 次/天 @ {adv.get('start_time', '')}"],
        ['护理', f"{care.get('method', '')} · 阈值 {care.get('energy_threshold', '-')}/{care.get('clean_threshold', '-')}"],
        ['异常恢复', str(recover.get('method', ''))],
        ['设备', f"{adb.get('device_serial') or '自动'} · {control.get('method', '')}"],
        ['通知', _notify_channels_label(notify)],
    ]
    return {
        'strategy': strategy,
        'school_enabled': school_enabled,
        'work_enabled': work_on,
        'work_location': work.get('location'),
        'work_duration': work.get('duration'),
        'coin_threshold': sched.get('coin_threshold'),
        # 今日配额（小时）：首页「今日学习/打工」瓦片按配额显示进度（如 8.2/12h）
        'study_quota_hours': sched.get('study_quota_hours', 0),
        'work_quota_hours': sched.get('work_quota_hours', 0),
        'visit_per_day': visit.get('times_per_day'),
        'pk_per_day': pk.get('times_per_day'),
        'adventure_times': adv.get('times_per_day'),
        'adventure_start': adv.get('start_time'),
        'task_order': [x.strip() for x in str(tasks.get('order') or '').split('>') if x.strip()],
        'tasks_enabled': _task_enabled_map(tasks, friend_care, gift_bag, hire_friend),
        'rows': rows,
    }


def load_progress() -> dict:
    """今日进度：按进度文件里的 date 过滤——不是今天的（调度器停跑时残留的昨天数据）
    按 0 / 未完成显示，避免把昨天的次数当成今天（用户曾问"今日PK为什么显示 15/15 明明一次没打"）。"""
    today = datetime.now().strftime('%Y-%m-%d')

    def today_of(name: str, zero: dict) -> dict:
        d = read_json(name)
        return d if d.get('date') == today else zero

    return {
        'visit': today_of('visit_progress.json', {'learned': 0}),
        'pk': today_of('pk_progress.json', {'learned': 0}),
        'work': today_of('work_progress.json', {'learned': 0}),
        'school': today_of('school_progress.json', {'learned': 0, 'study_secs': 0}),
        'adventure': today_of('adventure_progress.json', {'learned': 0}),
        'exp_daily': today_of('exp_daily_progress.json', {'done': False}),
    }


def adventure_data(sel_date: str | None = None) -> dict:
    """冒险记录（统一数据源：runs/adventure_live.jsonl——实验期 300 把 + 日常实时，
    由 src/scenario.record_adventure_live 在每把结算时记录）。

    sel_date: 统计范围——'YYYY-MM-DD' 看指定某天，'all' 看全部历史，
    空/'today' = 当天（当天还没有记录时回落到最近有记录的一天）。
    每条记录归属哪天优先取结算页内容里的日期（跨零点补录归前一天，
    如 00:00 才检测到的"昨天 23:59"结算），识别不到再用记录时间 ts。
    """
    rows = []
    try:
        for _line in ADV_LIVE_FILE.read_text('utf-8').splitlines()[-1500:]:
            try:
                rows.append(json.loads(_line))
            except Exception:
                pass
    except Exception:
        pass
    if not rows:
        return {'ok': False}
    rows.sort(key=lambda d: str(d.get('ts') or ''))
    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    for r in rows:
        day = ''
        for t in (r.get('settle') or []):
            m = re.search(r'(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})', str(t))
            if m:
                day = f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
                break
        r['_day'] = day or str(r.get('ts') or '')[:10] or today
    date_n: dict = {}
    for r in rows:
        date_n[r['_day']] = date_n.get(r['_day'], 0) + 1
    dates = sorted(date_n, reverse=True)
    if not sel_date or sel_date == 'today':
        sel = today if today in date_n else (dates[0] if dates else today)
    elif sel_date == 'all':
        sel = 'all'
    elif sel_date in date_n:
        sel = sel_date
    else:
        sel = today
    view = rows if sel == 'all' else [r for r in rows if r['_day'] == sel]
    coins = [int(r.get('coins') or 0) for r in view]
    n = len(coins)
    net = sum(coins)
    win = sum(1 for c in coins if c > 0)
    zero = sum(1 for c in coins if c == 0)
    dist = {}
    for c in coins:
        dist[str(c)] = dist.get(str(c), 0) + 1
    cum, s = [], 0
    for i, c in enumerate(coins, 1):
        s += c
        cum.append([i, s])
    pts = [[i, c] for i, c in enumerate(coins, 1)]
    tn = tnet = 0
    for r in rows:
        if r['_day'] == today:
            tn += 1
            tnet += int(r.get('coins') or 0)
    gains = {}
    recent = []
    for i, r in enumerate(view, 1):
        toks = [str(t).strip() for t in (r.get('settle') or [])]
        gs = []
        for t in toks:
            m = re.fullmatch(r'(心情|体力|清洁)值\+(\d+)', t)
            if m:
                g = gains.setdefault(m.group(1), [0, 0])
                g[0] += 1
                g[1] += int(m.group(2))
                gs.append(f'{m.group(1)}+{m.group(2)}')
        ts = str(r.get('ts') or '')
        c = int(r.get('coins') or 0)
        recent.append([i, ts[5:16] or ts[:5], c, ' '.join(gs), 0])
    # 状态曲线上的阈值参考线用配置里的真实值（以前把 60 写死在说明文案里，
    # 用户改了 care.energy_threshold 之后说明就跟实际不符）
    _care_thr = editable_snapshot()
    return {
        'ok': True, 'n': n,
        'net': net, 'avg': round(net / n, 2) if n else 0,
        'dist': dist, 'win': win, 'zero': zero, 'loss': 0,
        'gains': [[k, v[0], v[1]] for k, v in sorted(gains.items())],
        'cum': cum, 'pts': pts, 'stats': [], 'recent': recent[-800:],
        'today_n': tn, 'today_net': tnet,
        'date': sel, 'dates': dates, 'date_n': date_n,
        'today': today, 'yesterday': yesterday, 'all_n': len(rows),
        'updated': str(view[-1].get('ts') or '')[11:16] if view else '',
        'care_energy': _care_thr.get('care_energy', 60),
        'care_clean': _care_thr.get('care_clean', 60),
    }


SESSION_REWARD_FILE = RUNS / 'session_rewards.jsonl'   # 学习/打工按次结算收益
_ATTR_KEYS = ('力量', '智力', '魅力')


def _reward_sum(items) -> dict:
    """把一批结算记录汇总成 次数/学分/属性/金币/疲惫场次。

    credits_n / coins_n = **解析出数值**的条数：打工结算页字段还没实测，
    金币可能一条都解析不出来，前端要靠它区分"没收益"和"没解析出来"。
    """
    out = {'sessions': 0, 'credits': 0, 'coins': 0, 'tired': 0,
           'credits_n': 0, 'coins_n': 0, 'workpoints': 0, 'workpoints_n': 0,
           'ad_coins': 0, 'ad_coins_n': 0,
           'attrs': {k: 0 for k in _ATTR_KEYS}}
    for r in items:
        out['sessions'] += 1
        if r.get('credits') is not None:
            out['credits'] += int(r['credits'])
            out['credits_n'] += 1
        if r.get('coins') is not None:
            out['coins'] += int(r['coins'])
            out['coins_n'] += 1
        if r.get('workpoints') is not None:
            out['workpoints'] += int(r['workpoints'])
            out['workpoints_n'] += 1
        # 广告加成金币（结算页「看视频获得 N 金币」）：独立一笔，不计进 coins
        if r.get('ad_coins') is not None:
            out['ad_coins'] += int(r['ad_coins'])
            out['ad_coins_n'] += 1
        if r.get('tired'):
            out['tired'] += 1
        for k, v in (r.get('attrs') or {}).items():
            if k in out['attrs']:
                out['attrs'][k] += int(v or 0)
    return out


def rewards_data(sel_date: str | None = None) -> dict:
    """学习/打工每次结算的收益（数据源 runs/session_rewards.jsonl，由
    src/scenario.record_session_reward 在结算页记录）。

    sel_date: 'YYYY-MM-DD' 看指定某天 / 'all' 全部历史 / 空或 'today' = 当天
    （当天还没有记录时回落到最近有记录的一天）。归属哪天优先取结算页自带的
    日期时间（= 这次活动的开始时间，跨零点结算归前一天），识别不到再用 ts。
    """
    rows = []
    try:
        for _line in SESSION_REWARD_FILE.read_text('utf-8').splitlines()[-3000:]:
            try:
                rows.append(json.loads(_line))
            except Exception:
                pass
    except Exception:
        pass
    if not rows:
        return {'ok': False}
    rows.sort(key=lambda d: str(d.get('ts') or ''))
    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    for r in rows:
        day = ''
        m = re.search(r'(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})', str(r.get('sig') or ''))
        if m:
            day = f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
        r['_day'] = day or str(r.get('ts') or '')[:10] or today
    date_n: dict = {}
    for r in rows:
        date_n[r['_day']] = date_n.get(r['_day'], 0) + 1
    dates = sorted(date_n, reverse=True)
    if not sel_date or sel_date == 'today':
        sel = today if today in date_n else (dates[0] if dates else today)
    elif sel_date == 'all':
        sel = 'all'
    elif sel_date in date_n:
        sel = sel_date
    else:
        sel = today
    view = rows if sel == 'all' else [r for r in rows if r['_day'] == sel]
    recent = []
    for r in view[-300:]:
        attrs = ' '.join(f'{k}+{v}' for k, v in (r.get('attrs') or {}).items() if v)
        # 标题：学习=课程 / 打工=岗位名（结算页"工资明细"里那行"武馆教学助理(10分钟)"），
        # 都没有才回落配置的打工地点
        recent.append([str(r.get('ts') or '')[5:16],
                       r.get('kind') or '',
                       r.get('course') or r.get('job') or r.get('location') or '',
                       r.get('credits'), attrs, r.get('coins'),
                       1 if r.get('tired') else 0,
                       r.get('workpoints'), r.get('pay_detail') or ''])
    school = _reward_sum([r for r in view if r.get('kind') == 'school'])
    work = _reward_sum([r for r in view if r.get('kind') == 'work'])
    today_school = _reward_sum([r for r in rows
                                if r.get('kind') == 'school' and r['_day'] == today])
    today_work = _reward_sum([r for r in rows
                              if r.get('kind') == 'work' and r['_day'] == today])
    # 宠物名 / 主人名（结算页头部，src/scenario.parse_session_reward 解析）：
    # 取当前统计范围内最近一条有值的，用来标明这份数据属于哪只宠物 / 哪个号。
    pet = next((str(r.get('pet')) for r in reversed(view) if r.get('pet')), '')
    owner = next((str(r.get('owner')) for r in reversed(view) if r.get('owner')), '')
    return {
        'ok': True, 'date': sel, 'dates': dates, 'date_n': date_n,
        'today': today, 'yesterday': yesterday, 'all_n': len(rows),
        'n': len(view), 'school': school, 'work': work,
        'today_school': today_school, 'today_work': today_work,
        'recent': recent, 'pet': pet, 'owner': owner,
        'updated': str(view[-1].get('ts') or '')[11:16] if view else '',
    }


PLAN_FILE = RUNS / 'career_plan.json'
_PLAN_DEFAULTS = {'力': 0, '智': 0, '魅': 0, '工分': 0, '金币': 0,
                  '初级毕业': False, '中级毕业': False}

# (线名, 见习条件: 三元组 或 0/1/2=比例专修轴, 初级三元组)
_PLAN_LINES = [
    ('流浪散人', None, None),
    ('画家', (7, 3, 11), (225, 113, 412)),
    ('侦探', (11, 5, 5), (350, 200, 200)),
    ('法师', (3, 11, 7), (113, 412, 225)),
    ('大厨', (11, 7, 3), (412, 225, 113)),
    ('武术家', 0, (750, 0, 0)),
    ('梦境旅人', 1, (0, 750, 0)),
    ('大明星', 2, (0, 0, 750)),
]


def _plan_values() -> dict:
    try:
        data = json.loads(PLAN_FILE.read_text('utf-8'))
    except Exception:
        data = {}
    v = dict(_PLAN_DEFAULTS)
    for k in v:
        if k in data:
            v[k] = data[k]
    return v


def _triple_ok(V, tri):
    return all(V[i] >= tri[i] for i in range(3))


def plan_data() -> dict:
    """职业解锁计划进度（runs/career_plan.json + 规则换算）。"""
    v = _plan_values()
    V = [int(v['力']), int(v['智']), int(v['魅'])]

    def ratio_ok(axis):
        return V[axis] >= 24 and V[axis] >= 2.5 * (sum(V) - V[axis])

    # 职业树实测状态（career_unlock.json 的 lines 段，哨兵每节课后写回）
    ud0 = read_json('career_unlock.json') or {}
    tree_lines = (ud0.get('lines') or {}) if isinstance(ud0, dict) else {}

    def jr_state(name, fallback_numeric: bool) -> bool:
        """见习状态：职业树实测优先；没测到过的隐藏线按未解锁（宁可不勾），普通线按数值兜底。"""
        st = tree_lines.get(name) or {}
        if 'unlocked' in st:
            return bool(st.get('unlocked'))
        return fallback_numeric

    gates = {'工分': int(v['工分']) >= 360, '金币': int(v['金币']) >= 1000,
             '初级毕业': bool(v['初级毕业']), '中级毕业': bool(v['中级毕业'])}
    gates_ok = gates['工分'] and gates['金币'] and gates['初级毕业']
    lines = []
    for name, jr, ch in _PLAN_LINES:
        if jr is None and ch is None:
            lines.append({'name': name, 'jr': True, 'ch': False})
            continue
        jr_num = ratio_ok(jr) if isinstance(jr, int) else _triple_ok(V, jr)
        hidden = isinstance(jr, int)  # 隐藏线（单属性）：解锁有概率性，不能按数字勾
        jr_ok = jr_state(name, False if hidden else jr_num)
        ch_attr = _triple_ok(V, ch)
        lines.append({'name': name, 'jr': jr_ok, 'ch': ch_attr and gates_ok and jr_ok,
                      'ch_attr': ch_attr})
    # 阶梯路线（2026-09-13 调整：先解锁浅梦行者——智力专修最先，魅力/力量按 ×2.5 链递增）
    # 隐藏线三步的"完成"以职业树实测为准（解锁有概率性：数值达标≠已解锁）
    r_mind = max(24, int(2.5 * (V[0] + V[2])))
    r_char = max(24, int(2.5 * (V[0] + V[1])))
    r_force = max(24, int(2.5 * (V[1] + V[2])))

    def hidden_step(line: str, axis: int, need: int):
        num_ok = V[axis] >= 24 and V[axis] >= 2.5 * (sum(V) - V[axis])
        done = jr_state(line, False)
        pr = f'{V[axis]}（需≥{need}）' if (done or not num_ok) else f'{V[axis]} 达标·待触发'
        return done, pr

    s1_done, s1_pr = hidden_step('梦境旅人', 1, r_mind)
    s2_done, s2_pr = hidden_step('大明星', 2, r_char)
    s3_done, s3_pr = hidden_step('武术家', 0, r_force)
    steps = [
        ('S1', '智力专修，解锁 浅梦行者（≥其余两和的2.5倍）', s1_done, s1_pr),
        ('S2', '魅力专修，解锁 偶像练习生', s2_done, s2_pr),
        ('S3', '力量专修，解锁 习武小童', s3_done, s3_pr),
        ('S4', '补 力11·智11，解锁 侦探/法师/画家/大厨（三维覆盖自动达成）',
         V[0] >= 11 and V[1] >= 11 and V[2] >= 24, f'{V[0]}/11 · {V[1]}/11'),
        ('S5', '魅力补到 225（混合线初级前置）', V[2] >= 225, f'{V[2]}/225'),
        ('S6', '三维各 750 → 全 8 线初级', min(V) >= 750, f'最低 {min(V)}/750'),
    ]
    # 隐藏职业哨兵状态（runs/career_unlock.json + config.yaml 的 career 段；展示用）
    try:
        import yaml as _yaml
        _rawcfg = _yaml.safe_load((APP_ROOT / 'config.yaml').read_text('utf-8')) or {}
        ccfg = _rawcfg.get('career') or {}
    except Exception:
        ccfg = {}
    ud = read_json('career_unlock.json')
    try:
        alive = bool(scheduler_info().get('alive'))
    except Exception:
        alive = False
    watch = {
        'enabled': bool(ccfg.get('watch', True)),
        'stop_study': bool(ccfg.get('stop_study_on_unlock', True)),
        'interval': ccfg.get('check_interval_min', 60),
        'last_check': ud.get('last_check'),
        'alive': alive,
        'events': [{'career': e.get('career'), 'name': e.get('name'), 'ts': e.get('ts'),
                    'stopped': bool(e.get('stopped')), 'shot': e.get('shot')}
                   for e in (ud.get('events') or [])[-6:]],
    }
    total = sum(V)
    last_at = max((str((st or {}).get('at') or '') for st in tree_lines.values()), default='')
    lines_meta = f'职业树实测 · {last_at[5:16]}' if last_at else ''
    # 逐级解锁记录（career_unlock.json 的 tiers 段）：
    # 哨兵原先只记见习级（`lines`），而职业树页的初级/中级/高级需求游戏里没公开，
    # 只能靠实测。哨兵改为逐级记录后从 `tiers[线][层级序号]` 读，没有就回空让前端
    # 退回"需求未知=未解锁"。层级序号 0=见习 … 4=大师，与前端 TIERS 下标一致。
    tiers_db = ud.get('tiers') if isinstance(ud, dict) else {}
    if not isinstance(tiers_db, dict):
        tiers_db = {}
    # 已展示的路线（职业树上显示为黄牌 + 金连线）：哨兵没记就按主页面职业胶囊推——
    # 主页面右上角那个职业图标是当前佩挂，但 OCR 拿不准，所以只认显式记录。
    equipped = ud.get('equipped') if isinstance(ud, dict) else None
    if not isinstance(equipped, dict):
        equipped = None
    return {
        'ok': True, 'values': v, 'total': total, 'total_target': 2250,
        'jr_n': sum(1 for l in lines if l['jr']),
        'ch_n': sum(1 for l in lines if l['ch']),
        'lines': lines,
        'tree_lines': tree_lines,          # 职业树页：8 线的实测解锁记录（含见习名）
        'tree_tiers': tiers_db,            # 职业树页：逐级解锁记录（可能为空）
        'equipped': equipped,              # 职业树页：已展示的线 + 职业名
        'steps': [[s[0], s[1], s[2], s[3]] for s in steps],
        'lines_meta': lines_meta,
        'gates': gates,
        'updated': datetime.now().strftime('%H:%M:%S'),
        'watch': watch,
    }


def apply_plan(updates: dict) -> dict:
    v = _plan_values()
    applied, rejected = {}, []
    int_limits = {'力': 99999, '智': 99999, '魅': 99999, '工分': 9999999, '金币': 99999999}
    for k, val in (updates or {}).items():
        if k in int_limits:
            try:
                n = int(val)
            except Exception:
                rejected.append(f'{k}: 需要数字')
                continue
            if not (0 <= n <= int_limits[k]):
                rejected.append(f'{k}: 超范围')
                continue
            v[k] = n
            applied[k] = n
        elif k in ('初级毕业', '中级毕业'):
            if not isinstance(val, bool):
                rejected.append(f'{k}: 需要布尔')
                continue
            v[k] = val
            applied[k] = val
        else:
            rejected.append(f'{k}: 不支持')
    if applied:
        write_json(PLAN_FILE, v, indent=1)
    return {'ok': not rejected, 'applied': applied, 'rejected': rejected}


_SYNC = {'busy': False}


def career_sync() -> dict:
    """运行 tools/career_sync.py 自动识别游戏里的属性（占用设备约 20 秒）。"""
    if _SYNC['busy']:
        return {'ok': False, 'reason': '正在同步中，请稍候'}
    try:
        command = runtime_process.build_career_sync_argv(BASE)
    except FileNotFoundError as exc:
        return {'ok': False, 'reason': str(exc)}
    _SYNC['busy'] = True
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=150, cwd=str(APP_ROOT))
    except subprocess.TimeoutExpired:
        return {'ok': False, 'reason': '识别超时（150 秒）'}
    finally:
        _SYNC['busy'] = False
    for line in reversed((proc.stdout or '').splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {'ok': False, 'reason': '脚本无有效输出', 'stderr': (proc.stderr or '')[-300:]}


def list_shots() -> list[dict]:
    out = []
    for p in sorted(RUNS.glob('*.png'), key=lambda x: x.stat().st_mtime, reverse=True)[:12]:
        out.append({'name': p.name,
                    'mtime': datetime.fromtimestamp(p.stat().st_mtime).strftime('%m-%d %H:%M')})
    return out


_shot_cache = {'ts': 0.0, 'data': b'', 'at': '', 'source': None}
_SHOT_CACHE_LOCK = threading.Lock()


def capture_phone(width: int = 390, min_interval: float = 5.0):
    """adb 截取手机当前画面：缩放 + JPEG（带节流缓存）。

    返回 (jpeg_bytes, 拍摄时间字符串, 是否来自缓存)。
    """
    configured, serial = _adb_settings()
    source = (configured, serial)
    with _SHOT_CACHE_LOCK:
        if _shot_cache.get('source') != source:
            _shot_cache.update(ts=0.0, data=b'', at='', source=source)
        if _shot_cache['data'] and time.time() - _shot_cache['ts'] < min_interval:
            return _shot_cache['data'], _shot_cache['at'], True
    adb_path, error = _resolve_adb(configured)
    if error:
        raise RuntimeError(error)
    cmd = [adb_path] + (['-s', serial] if serial else []) + ['exec-out', 'screencap', '-p']
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout:
        detail = (proc.stderr or b'').decode('utf-8', 'replace').strip()[:200]
        raise RuntimeError(detail or 'adb screencap 失败')
    from PIL import Image
    img = Image.open(io.BytesIO(proc.stdout))
    w, h = img.size
    if w > width:
        img = img.resize((width, max(1, round(h * width / w))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'JPEG', quality=82)
    data = buf.getvalue()
    at = datetime.now().strftime('%H:%M:%S')
    with _SHOT_CACHE_LOCK:
        if _shot_cache.get('source') == source:
            _shot_cache.update(ts=time.time(), data=data, at=at)
    return data, at, False


def _int_or_0(v) -> int:
    """配置里的数字字段转 int（None / 空串 / 非数字 → 0）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def stop_total_hours(sched: dict) -> int:
    """设置页「合计满则停止」的当前值 = 学习与打工**一起**停下的那个合计小时数。

    底层仍是三个键（scenarios/runner.py 里各有一处判定，不区分引擎）：
    `daily_hour_limit`（合计满则停学习）/ `work_stop_hours`（满则停打工）/
    `efficiency_tier2_hours`（满则两项全停）。学习在 min(全停, 停学习) 停、
    打工在 min(全停, 停打工) 停，所以「都停」的时刻 = 两者里更晚的那个；
    任一项不限（对应键 0）就永远等不到"都停"，返回 0（不限）。

    设置页只暴露这一个数（保存时三键写同一个值，见 apply_settings），
    这个函数负责把老配置（三项不一致，例如"学满 8h 后继续打 25% 档到 12h"）
    折算成一个能看的数字；前端另有三项不一致的提示。
    """
    def _stop(*vals):
        pos = [v for v in (_int_or_0(x) for x in vals) if v > 0]
        return min(pos) if pos else None
    study = _stop(sched.get('efficiency_tier2_hours'), sched.get('daily_hour_limit'))
    work = _stop(sched.get('efficiency_tier2_hours'), sched.get('work_stop_hours'))
    if study is None or work is None:
        return 0
    return max(study, work)


def _course_kind(value) -> str | None:
    """school.duration → '短课'/'长课'（与 src/settings.validate_field 同一份归一化）。

    设置页下拉只有"短课/长课"两项，而 config.yaml 里可能是旧版的分钟数写法，
    回填前必须先归一——否则 select 找不到匹配项，浏览器退化成显示第一项。
    """
    try:
        from src.progress import course_kind
    except Exception:
        return None
    return course_kind(value)


def editable_snapshot() -> dict:
    """设置卡可编辑字段的当前值（供表单回填）。"""
    try:
        import yaml
        cfg = yaml.safe_load((APP_ROOT / 'config.yaml').read_text('utf-8')) or {}
    except Exception:
        return {}
    try:
        from src.settings import WORK_LOCATIONS
        locations = list(WORK_LOCATIONS)
    except Exception:
        locations = []
    tasks = cfg.get('tasks') or {}
    sched = cfg.get('schedule') or {}
    work = cfg.get('work') or {}
    visit = cfg.get('visit') or {}
    pk = cfg.get('pk') or {}
    adv = cfg.get('adventure') or {}
    care = cfg.get('care') or {}
    fc = cfg.get('friend_care') or {}
    emp = cfg.get('employed') or {}
    school = cfg.get('school') or {}
    career = cfg.get('career') or {}
    notify = cfg.get('notify') or {}
    hf = cfg.get('hire_friend') or {}
    tgbot = cfg.get('tgbot') or {}
    return {
        # 连接层：ADB 路径与设备序列号（设置页「连接手机」卡片；改完需重启调度器）
        'adb_path': str((cfg.get('adb') or {}).get('path') or ''),
        'adb_serial': str((cfg.get('adb') or {}).get('device_serial') or ''),
        # 各任务的「启用」开关（设置页表单回填）：**必须逐个列出**，不能只给 school。
        # 前端 schema 里 work/care/visit/pk/adventure 都有开关，拿不到值就被
        # `boolOf()` 当成 false —— 表现为"设置页显示关闭、任务列表里却是勾选的"
        # （用户实报：打工页显示关闭、首页打工行是开着的）。保存侧 mapping 一直都有
        # 这些键，漏的是回填这一半，所以两个界面长期对不上。
        'school_enabled': bool((tasks.get('school') or {}).get('enabled', True)),
        'work_enabled': bool((tasks.get('work') or {}).get('enabled', True)),
        'care_enabled': bool((tasks.get('care') or {}).get('enabled', True)),
        'visit_enabled': bool((tasks.get('visit') or {}).get('enabled', True)),
        'pk_enabled': bool((tasks.get('pk') or {}).get('enabled', True)),
        'adventure_enabled': bool((tasks.get('adventure') or {}).get('enabled', True)),
        'school_attribute': str(school.get('attribute') or '力量'),
        # 归一化成"短课/长课"再回填：config.yaml 里可能还是旧的分钟数写法
        # （10分钟=短课，其余分钟数=长课），前端下拉只有短课/长课两项，
        # 原样回填会让 select 落空、显示成第一项，与真正生效的档位对不上。
        'school_duration': (_course_kind(school.get('duration')) or '短课'),
        'school_times': school.get('times_per_day', 0),
        'work_location': work.get('location'),
        'work_locations': locations,
        'work_duration': work.get('duration'),
        'hire_name': str(work.get('hire_name') or ''),
        'hire_wait': bool(work.get('hire_wait', False)),
        # 「等他空闲」时干什么（设置页在 hire_wait 关闭时置灰）
        'hire_wait_action': str(work.get('hire_wait_action') or '去冒险'),
        # 顺路雇好友的每日配额（原「雇佣好友」任务并入打工；行为固定，没有策略选项）
        'hire_daily_hours': work.get('hire_daily_hours', 4),
        'coin_threshold': sched.get('coin_threshold', 2000),
        # 设置页「合计满则停止」= 唯一入口（stop_total_hours 由下面三个键折算，
        # 保存时三键一起写同一个值）。三个原键仍回填给前端：用于"三项不一致"
        # 提示与主页胶囊的分母，不再单独出现在表单里。
        'stop_total_hours': stop_total_hours(sched),
        'daily_hour_limit': sched.get('daily_hour_limit', 8),
        'work_stop_hours': sched.get('work_stop_hours', 12),
        'study_quota_hours': sched.get('study_quota_hours', 8),
        'work_quota_hours': sched.get('work_quota_hours', 8),
        'efficiency_tier1_hours': sched.get('efficiency_tier1_hours', 8),
        'efficiency_tier2_hours': sched.get('efficiency_tier2_hours', 12),
        'main_order': str(tasks.get('main_order') or ''),
        'visit_times': visit.get('times_per_day', 10),
        'pk_times': pk.get('times_per_day', 15),
        'pk_only': str(pk.get('only_names') or ''),
        'pk_skip': str(pk.get('skip_names') or ''),
        'pk_max_level': pk.get('max_level', 0) or 0,
        'pk_helper': str(pk.get('helper_names') or ''),
        'pk_helper_fallback': bool(pk.get('helper_fallback', False)),
        'adventure_times': adv.get('times_per_day', 1),
        # 冒险类型（2026-09 新增）：附近走走（约45秒，靠连跑刷次数）/ 诗和远方（约2小时）
        'adventure_type': adv.get('type', '附近走走'),
        'care_energy': care.get('energy_threshold', 60),
        'care_clean': care.get('clean_threshold', 60),
        'care_method': care.get('method', '一键护理'),
        'care_exchange': care.get('exchange_count', 20),
        'friend_care_enabled': bool(fc.get('enabled', False)),
        'friend_care_name': str(fc.get('friend_name') or ''),
        # 时间段（HH:MM-HH:MM；起止相同 = 跨零点 = 全天）
        'friend_care_range': str(fc.get('time_range') or ''),
        'friend_care_interval': fc.get('interval_seconds', 120),
        'friend_care_method': str(fc.get('method') or 'ocr检测'),
        'care_interval': care.get('interval_seconds', 60),
        'hire_friend_enabled': bool(hf.get('enabled', False)),
        # 雇佣好友的"备选目标"：实际优先用 work.hire_name（config 的 _hire_friend_raw 会把它
        # 排到 friend_name 列表最前），这里单独给一份供任务列表显示"雇的是谁"做兜底
        'hire_friend_name': str(hf.get('friend_name') or ''),
        'hire_friend_times': hf.get('times_per_day', 8),
        'employed_enabled': bool(emp.get('enabled', False)),
        'employed_action': str(emp.get('action') or '等到25/75（小于45min）'),
        'employed_interval': emp.get('interval_seconds', 60),
        'gift_bag_enabled': bool((cfg.get('gift_bag') or {}).get('enabled', True)),
        'gift_bag_range': str((cfg.get('gift_bag') or {}).get('time_range') or ''),
        'gift_bag_interval': (cfg.get('gift_bag') or {}).get('interval_seconds', 1800),
        # 每次打工（含雇佣好友打工）结束时在结算页上顺手领「看视频 +N」金币补贴
        'claim_ad_bonus': bool(sched.get('claim_ad_bonus', True)),
        'career_watch': bool(career.get('watch', True)),
        'career_stop_study': bool(career.get('stop_study_on_unlock', True)),
        'career_interval': career.get('check_interval_min', 60),
        # 通知渠道（飞书群机器人 / Telegram Bot）
        'notify_feishu_enabled': bool(notify.get('feishu_enabled', False)),
        'notify_feishu_webhook': '',
        'notify_feishu_webhook_set': bool(str(notify.get('feishu_webhook') or '').strip()),
        'notify_feishu_secret': '',
        'notify_feishu_secret_set': bool(str(notify.get('feishu_secret') or '').strip()),
        'notify_feishu_access_token': '',
        'notify_feishu_access_token_set': bool(str(notify.get('feishu_access_token') or '').strip()),
        'notify_telegram_enabled': bool(notify.get('telegram_enabled', False)),
        'notify_telegram_token': '',
        'notify_telegram_token_set': bool(str(notify.get('telegram_token') or '').strip()),
        'notify_telegram_chat_id': '',
        'notify_telegram_chat_id_set': bool(str(notify.get('telegram_chat_id') or '').strip()),
        'notify_career': bool(notify.get('career_notify', True)),
        'notify_quota_done': bool(notify.get('quota_done', True)),
        'notify_event_notify': bool(notify.get('event_notify', True)),
        'notify_error_notify': bool(notify.get('error_notify', True)),
        # ---- TG 控制机器人（反向通道）----
        # 凭据类（token / api_key）只回「是否已配置」，原值不回传（与通知页同一规则）。
        'tgbot_enabled': bool(tgbot.get('enabled', False)),
        'tgbot_token': '',
        'tgbot_token_set': bool(str(tgbot.get('token') or '').strip()),
        'tgbot_allowed_chats': str(tgbot.get('allowed_chats') or ''),
        'tgbot_llm_enabled': bool(tgbot.get('llm_enabled', False)),
        'tgbot_llm_base_url': str(tgbot.get('llm_base_url') or ''),
        'tgbot_llm_model': str(tgbot.get('llm_model') or ''),
        'tgbot_llm_api_key': '',
        'tgbot_llm_api_key_set': bool(str(tgbot.get('llm_api_key') or '').strip()),
        # 只读状态（设置页显示"线程在不在跑 / 最近一次错误 / 白名单几个人"）
        'tgbot_running': bool(_tg_bot_status().get('running')),
        'tgbot_last_error': str(_tg_bot_status().get('last_error') or ''),
        'tgbot_allowed_count': _tg_bot_status().get('allowed', 0),
        # 最近一次成功长轮询的时刻（"" = 从没成功过）。排查"线程在但收不到消息"。
        'tgbot_last_ok': str(_tg_bot_status().get('last_ok') or ''),
        'tgbot_handled': _tg_bot_status().get('handled', 0),
    }


def apply_settings(updates: dict) -> dict:
    with _SETTINGS_LOCK:
        return _apply_settings(updates)


def _apply_settings(updates: dict) -> dict:
    """把设置卡改动写入 config.yaml（ruamel 往返保留注释，复用 src/settings 校验）。

    调度器每轮重读配置（含 tasks.* 任务级设置），保存后下一轮调度自动生效。
    """
    import src.settings as S
    # 复合任务（好友护理/福袋/雇佣好友）同时有任务级 tasks.<k>.enabled 与场景级
    # <k>.enabled 两个开关，前端勾选态 = 两者 AND（见 _task_enabled_map）。仪表盘
    # 任务队列勾选框与设置页开关共用同一字段名，只写其中一个会让另一个残留 false，
    # 表现为"点了勾选/开关却启不起来"（单向失效）。两处一起写，保持一致。
    task_scene_pairs = {
        'friend_care_enabled': ('tasks.friend_care.enabled', 'friend_care.enabled'),
        'gift_bag_enabled': ('tasks.gift_bag.enabled', 'gift_bag.enabled'),
        'hire_friend_enabled': ('tasks.hire_friend.enabled', 'hire_friend.enabled'),
    }
    # 合计停止点（设置页唯一入口）：学习+打工合计满 N 小时 → 两项一起停（转冒险）。
    # runner 里三个键各有一处判定——daily_hour_limit 停学习、work_stop_hours 停打工、
    # efficiency_tier2_hours 两项全停；设置页只给一个数，所以**三键写同一个值**
    #（只写其中一个会出现"设了 10 小时却 12 小时才停"的错觉）。三项的分开玩法
    # （例如"学满 8h 后继续吃 25% 档打工到 12h"）仍可手改 config.yaml，引擎照旧认。
    stop_total_keys = ('schedule.daily_hour_limit', 'schedule.work_stop_hours',
                       'schedule.efficiency_tier2_hours')
    mapping = {
        # 连接层（改完需重启调度器才生效，设置页卡片里有提示）
        'adb_path': ('adb.path', 'str'),
        'adb_serial': ('adb.device_serial', 'str'),
        'school_enabled': ('tasks.school.enabled', 'bool'),
        'care_enabled': ('tasks.care.enabled', 'bool'),
        'adventure_enabled': ('tasks.adventure.enabled', 'bool'),
        'visit_enabled': ('tasks.visit.enabled', 'bool'),
        'pk_enabled': ('tasks.pk.enabled', 'bool'),
        'work_enabled': ('tasks.work.enabled', 'bool'),
        # 复合任务主键（实际写入见 task_scene_pairs 的双写）
        'friend_care_enabled': ('tasks.friend_care.enabled', 'bool'),
        'gift_bag_enabled': ('tasks.gift_bag.enabled', 'bool'),
        'hire_friend_enabled': ('tasks.hire_friend.enabled', 'bool'),
        'work_location': ('work.location', None),
        'work_duration': ('work.duration', None),
        'hire_name': ('work.hire_name', None),
        'hire_wait': ('work.hire_wait', 'bool'),
        'hire_wait_action': ('work.hire_wait_action', None),
        'hire_daily_hours': ('work.hire_daily_hours', 'int'),
        'coin_threshold': ('schedule.coin_threshold', 'int'),
        # 合计停止点：映射到 work_stop_hours 只为过 validate_field（0..24 校验），
        # 实际写入见 stop_total_keys 的三键同值分支
        'stop_total_hours': ('schedule.work_stop_hours', 'int'),
        'daily_hour_limit': ('schedule.daily_hour_limit', 'int'),
        'work_stop_hours': ('schedule.work_stop_hours', 'int'),
        'study_quota_hours': ('schedule.study_quota_hours', 'int'),
        'work_quota_hours': ('schedule.work_quota_hours', 'int'),
        # 收益档门槛：**设置页已不给输入框**（游戏机制说明，卡片里只读展示）。
        # 保留映射只为向后兼容旧客户端/手写 API 调用；手改 config.yaml 也能改。
        'efficiency_tier1_hours': ('schedule.efficiency_tier1_hours', 'int'),
        'efficiency_tier2_hours': ('schedule.efficiency_tier2_hours', 'int'),
        'main_order': ('tasks.main_order', None),
        # 任务队列扫描顺序——任务列表拖拽排序写回的就是它。
        # `> `分隔的任务键，src/settings.validate_field 会校验键名合法性
        # （含未知键直接拒绝并回默认），不会把调度器的扫描表写坏。
        'task_order': ('tasks.order', None),
        'school_attribute': ('school.attribute', None),
        'school_duration': ('school.duration', None),
        'school_times': ('school.times_per_day', 'int'),
        'visit_times': ('visit.times_per_day', 'int'),
        'pk_times': ('pk.times_per_day', 'int'),
        'pk_only': ('pk.only_names', None),
        'pk_skip': ('pk.skip_names', None),
        'pk_max_level': ('pk.max_level', 'int'),
        'pk_helper': ('pk.helper_names', None),
        'pk_helper_fallback': ('pk.helper_fallback', 'bool'),
        'adventure_times': ('adventure.times_per_day', 'int'),
        'adventure_type': ('adventure.type', None),
        'care_energy': ('care.energy_threshold', 'int'),
        'care_clean': ('care.clean_threshold', 'int'),
        'care_method': ('care.method', None),
        'care_exchange': ('care.exchange_count', 'int'),
        'friend_care_name': ('friend_care.friend_name', None),
        'friend_care_range': ('friend_care.time_range', 'str'),
        'friend_care_interval': ('friend_care.interval_seconds', 'int'),
        'friend_care_method': ('friend_care.method', None),
        'care_interval': ('care.interval_seconds', 'int'),
        'hire_friend_times': ('hire_friend.times_per_day', 'int'),
        'employed_enabled': ('employed.enabled', 'bool'),
        'employed_action': ('employed.action', None),
        'employed_interval': ('employed.interval_seconds', 'int'),
        'gift_bag_range': ('gift_bag.time_range', 'str'),
        'gift_bag_interval': ('gift_bag.interval_seconds', 'int'),
        'claim_ad_bonus': ('schedule.claim_ad_bonus', 'bool'),
        'career_watch': ('career.watch', 'bool'),
        'career_stop_study': ('career.stop_study_on_unlock', 'bool'),
        'career_interval': ('career.check_interval_min', 'int'),
        # 通知渠道（飞书群机器人 / Telegram Bot）——凭据类走 str，服务端 validate_field 校验
        'notify_feishu_enabled': ('notify.feishu_enabled', 'bool'),
        'notify_feishu_webhook': ('notify.feishu_webhook', 'str'),
        'notify_feishu_secret': ('notify.feishu_secret', 'str'),
        'notify_feishu_access_token': ('notify.feishu_access_token', 'str'),
        'notify_telegram_enabled': ('notify.telegram_enabled', 'bool'),
        'notify_telegram_token': ('notify.telegram_token', 'str'),
        'notify_telegram_chat_id': ('notify.telegram_chat_id', 'str'),
        'notify_career': ('notify.career_notify', 'bool'),
        'notify_quota_done': ('notify.quota_done', 'bool'),
        'notify_event_notify': ('notify.event_notify', 'bool'),
        'notify_error_notify': ('notify.error_notify', 'bool'),
        # ---- TG 控制机器人（反向通道）----
        # token 留空 = 复用 notify.telegram_token；allowed_chats 留空 = 只认
        # notify.telegram_chat_id（见 src/tg_bot.py 的 _resolve_token / _allowed_chats）。
        'tgbot_enabled': ('tgbot.enabled', 'bool'),
        'tgbot_token': ('tgbot.token', 'str'),
        'tgbot_allowed_chats': ('tgbot.allowed_chats', 'str'),
        'tgbot_llm_enabled': ('tgbot.llm_enabled', 'bool'),
        'tgbot_llm_base_url': ('tgbot.llm_base_url', 'str'),
        'tgbot_llm_model': ('tgbot.llm_model', 'str'),
        'tgbot_llm_api_key': ('tgbot.llm_api_key', 'str'),
    }
    data = S.load_raw()
    applied, rejected = {}, []
    for field, value in (updates or {}).items():
        if field not in mapping:
            rejected.append(f'{field}: 不支持')
            continue
        key, kind = mapping[field]
        if field == 'stop_total_hours':
            # 合计停止点：校验一次（复用 work_stop_hours 的 0..24 规则），
            # 通过后三个键一起写，保证"填多少就多少小时全停"
            ok, fixed = S.validate_field('schedule.work_stop_hours', value)
            if not ok:
                rejected.append(f'{field}: 非法值 {value!r}')
                continue
            fixed = int(fixed)
            for k in stop_total_keys:
                S.set_value(data, k, fixed)
            applied[field] = fixed
            continue
        if kind == 'bool':
            if not isinstance(value, bool):
                rejected.append(f'{field}: 需要布尔值')
                continue
            # 复合任务：任务级 + 场景级两个开关同时写，避免另一个残留 false
            for k in task_scene_pairs.get(field, (key,)):
                S.set_value(data, k, value)
            applied[field] = value
            continue
        ok, fixed = S.validate_field(key, value)
        if not ok:
            rejected.append(f'{field}: 非法值 {value!r}')
            continue
        if kind == 'int':
            fixed = int(fixed)
        S.set_value(data, key, fixed)
        applied[field] = ('[configured]' if fixed else '') if field in (
            'notify_feishu_webhook', 'notify_feishu_secret', 'notify_feishu_access_token',
            'notify_telegram_token', 'notify_telegram_chat_id',
            # TG 控制机器人：token 与模型 key 同样只回「已配置/已清空」，
            # 否则 apply_settings 的返回值（以及 /api/settings 的响应体）会把
            # 密钥原样回显一遍，等于把凭据写到日志和浏览器里。
            'tgbot_token', 'tgbot_llm_api_key') else fixed
    if applied:
        S.save_raw(data)
    return {'ok': not rejected, 'applied': applied, 'rejected': rejected}


def _adb_settings() -> tuple[str, str]:
    """读 config.yaml 的 adb 段 → (配置的 adb 路径, 配置的设备序列号)。"""
    try:
        import yaml
        cfg = yaml.safe_load((APP_ROOT / 'config.yaml').read_text('utf-8')) or {}
    except Exception:
        return '', ''
    adb_cfg = cfg.get('adb') or {}
    return str(adb_cfg.get('path') or ''), str(adb_cfg.get('device_serial') or '')


def _tg_bot_status() -> dict:
    """TG 控制机器人线程状态（模块没起来/导入失败都返回空壳，绝不让页面挂掉）。"""
    try:
        from src import tg_bot
        return tg_bot.status()
    except Exception:  # noqa: BLE001
        return {}


def _tg_logs(n: int) -> list[str]:
    """最近 n 行日志（今天没有就翻到上一个有内容的日志文件）。"""
    p = today_log_path()
    return tail_lines(p, n) if p else []


def _tg_status() -> dict:
    """给机器人看的运行摘要：复用 /api/data 的那几个构件，但不做全量聚合
    （build_data 会读截图列表、友邻列表等，机器人用不上，还慢）。"""
    return {
        'scheduler': scheduler_info(),
        'queue': read_json('queue_status.json'),
        'config': config_summary(),
        'today_duration': _tg_today_duration(),
        'work_eta': _tg_work_eta(),
        'last_line': (_tg_logs(1) or [''])[-1],
        # 宠物属性与职业进度（力/智/魅、工分、金币门槛、解锁路线）。
        # **不能漏**：用户问"我现在力量多少"时，机器人早先只能回"我看不到"——
        # 因为它只被喂了设置项与调度状态，属性数据在 career_plan.json 里。
        'attrs': _tg_attrs(),
        'pet': _tg_pet_status(),
    }


def _tg_attrs() -> dict:
    """力/智/魅 + 职业解锁进度（数据源与仪表盘「职业」页同源：career_plan.json）。"""
    try:
        d = plan_data()
    except Exception:  # noqa: BLE001
        return {}
    values = d.get('values') or {}
    return {
        'force': values.get('力', 0),        # 力量
        'intel': values.get('智', 0),        # 智力
        'charm': values.get('魅', 0),        # 魅力
        'total': d.get('total', 0),
        'total_target': d.get('total_target', 0),
        'workpoints': values.get('工分', 0),
        'coins': values.get('金币', 0),
        'gates': d.get('gates') or {},
        'steps': (d.get('steps') or [])[:6],
        'updated': d.get('updated', ''),
    }


def _tg_pet_status() -> dict:
    """宠物当前状态环（体力/清洁/心情/金币/库存），数据源 runs/status_cache.json。"""
    try:
        cache = read_json('status_cache.json') or {}
        info = (cache.get('accounts') or {}).get('default') or {}
    except Exception:  # noqa: BLE001
        return {}
    keep = ('energy', 'clean', 'mood', 'coins', 'soap', 'cookie', 'name', 'updated')
    return {k: info[k] for k in keep if k in info}


def _tg_today_duration():
    try:
        import yaml
        sched = (yaml.safe_load((APP_ROOT / 'config.yaml').read_text('utf-8')) or {}).get('schedule') or {}
    except Exception:  # noqa: BLE001
        sched = {}
    p = today_log_path()
    return today_duration(cross_day_log_lines(tail_lines(p, 400), p) if p else [],
                          efficiency_tiers_of(sched))


def _tg_work_eta():
    p = today_log_path()
    if not p:
        return None
    return work_eta(cross_day_log_lines(tail_lines(p, 400), p))


def start_tg_bot() -> None:
    """装配 Bridge 并启动轮询线程（失败只记日志，绝不影响仪表盘启动）。"""
    try:
        from src import tg_bot
        from src.tg_bot import Bridge

        def _scheduler_action(action: str) -> dict:
            fn = {'start': scheduler_start, 'stop': scheduler_stop,
                  'pause': scheduler_pause, 'resume': scheduler_resume}.get(action)
            if fn is None:
                return {'ok': False, 'msg': f'不支持的动作：{action}'}
            return fn()

        def _tg_apply(updates: dict) -> dict:
            """TG 改配置走这里 —— **必须留审计**。

            HTTP 那条路的审计写在 /api/settings 路由里（`audit('设置保存(来自 ip)')`），
            而 TG 经 Bridge 直接调 apply_settings，不经过那个路由。不在这里补一笔，
            聊天通道改了配置就查无实据 —— 出问题时没法回答"是谁、什么时候改的"。
            """
            result = apply_settings(updates)
            audit(f'设置保存(来自 TG 机器人): 提交 {updates} '
                  f'→ 生效 {result["applied"]}'
                  + (f' 拒绝 {result["rejected"]}' if result['rejected'] else ''))
            # 与 HTTP 路径一致：唤醒调度器立即重读配置，别等它 30s 轮询睡醒
            if result.get('ok') and result.get('applied'):
                try:
                    write_text_atomic(RUNS / 'reload.signal', str(time.time()))
                except OSError:
                    pass
            return result

        bridge = Bridge(
            settings_snapshot=editable_snapshot,
            apply_settings=_tg_apply,
            scheduler_info=scheduler_info,
            scheduler_action=_scheduler_action,
            status=_tg_status,
            logs=_tg_logs,
            log=audit,
        )
        worker = tg_bot.start(bridge)
        st = worker.status() if worker else {}
        audit(f'TG 控制机器人: 线程已起（enabled={st.get("enabled")} '
              f'llm={st.get("llm_enabled")} 白名单={st.get("allowed")}）')
    except Exception as e:  # noqa: BLE001
        audit(f'TG 控制机器人启动失败（仪表盘继续运行）: {type(e).__name__}: {e}')


def _resolve_adb(configured: str) -> tuple[str, str]:
    """解析出实际可用的 adb 可执行文件路径 → (路径, 错误说明)。"""
    try:
        import sys as _sys
        if str(BASE) not in _sys.path:
            _sys.path.insert(0, str(BASE))
        from src.config import find_adb
        return find_adb(configured), ''
    except Exception as e:  # noqa: BLE001
        return '', str(e)


def adb_info() -> dict:
    """ADB 配置 + 在线设备列表（设置页「连接手机」卡片用）。

    `adb devices -l` 解析出 serial / state / model。adb 不可用、超时、命令失败都
    只记进 error 字段，不抛异常——界面显示空列表即可，不影响仪表盘其他功能。
    """
    import subprocess
    configured, serial = _adb_settings()
    resolved, err = _resolve_adb(configured)
    devices: list[dict] = []
    if resolved:
        try:
            out = subprocess.run([resolved, 'devices', '-l'], capture_output=True,
                                 text=True, timeout=10)
            for ln in (out.stdout or '').splitlines()[1:]:
                ln = ln.strip()
                if not ln or ln.startswith('*'):
                    continue
                # 用正则而不是 split()：`(no serial number)   device usb:…` 这类行
                # 的"序列号"自带空格，split 会把它拆成 serial="(no" / state="serial"
                # （实测踩过）。\S+ 匹配不到带空格的伪序列号，正好跳过。
                mo = re.match(r'^(\S+)\s+(device|offline|unauthorized|no permissions'
                              r'|bootloader|recovery|sideload)\b(.*)$', ln)
                if not mo:
                    continue
                d = {'serial': mo.group(1), 'state': mo.group(2)}
                for p in mo.group(3).split():
                    if p.startswith('model:'):
                        d['model'] = p[6:].replace('_', ' ')
                    elif p.startswith('product:'):
                        d['product'] = p[8:]
                devices.append(d)
            if out.returncode != 0 and not devices:
                err = (out.stderr or '').strip() or f'adb 返回码 {out.returncode}'
        except subprocess.TimeoutExpired:
            err = 'adb devices 超时（10s）——adb server 可能卡住，可试 adb kill-server'
        except Exception as e:  # noqa: BLE001
            err = f'执行 adb 失败：{e}'
    return {'path': configured, 'resolved': resolved, 'serial': serial,
            'devices': devices, 'error': err}


def adb_connect(addr: str) -> dict:
    """`adb connect <addr>`（无线调试 / 模拟器）。返回 {ok, msg}。"""
    import subprocess
    addr = (addr or '').strip()
    if not addr:
        return {'ok': False, 'msg': '请填写地址，如 192.168.1.5:5555 或 127.0.0.1:7555'}
    configured, _ = _adb_settings()
    resolved, err = _resolve_adb(configured)
    if not resolved:
        return {'ok': False, 'msg': f'找不到 adb：{err}'}
    if ':' not in addr:
        addr = f'{addr}:5555'          # 省略端口时按 adb 默认无线调试端口补全
    try:
        r = subprocess.run([resolved, 'connect', addr], capture_output=True,
                           text=True, timeout=20)
        out = ((r.stdout or '') + (r.stderr or '')).strip()
        ok = 'connected' in out and 'cannot' not in out.lower() and 'failed' not in out.lower()
        return {'ok': ok, 'msg': out or f'返回码 {r.returncode}', 'addr': addr}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'msg': 'adb connect 超时（20s）'}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'msg': f'执行失败：{e}'}


def build_data() -> dict:
    log_lines = []
    p = today_log_path()
    if p:
        log_lines = tail_lines(p, 400)
    accounts = (read_json('status_cache.json').get('accounts') or {})
    key_file = Path(os.environ.get('QQPET_DASHBOARD_ACCESS_FILE')
                    or RUNS / 'dashboard_access.token')
    try:
        import yaml
        sched_cfg = (yaml.safe_load((APP_ROOT / 'config.yaml').read_text('utf-8')) or {}).get('schedule') or {}
    except Exception:
        sched_cfg = {}
    return {
        'now': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'scheduler': scheduler_info(),
        'queue': read_json('queue_status.json'),
        'status': accounts.get('default') or {},
        'progress': load_progress(),
        'config': config_summary(),
        'shots': list_shots(),
        'work_eta': work_eta(cross_day_log_lines(log_lines, p)),
        'today_duration': today_duration(log_lines, efficiency_tiers_of(sched_cfg)),
        'last_line': log_lines[-1] if log_lines else '',
        'editable': editable_snapshot(),
        'friends': list_friends(),
        'onboarding': onboarding_status(),
        # 访问权限卡（设置 → 访问码）用；不回传访问码本身
        'access': {'custom': is_custom_key(key_file),
                   'min_length': MIN_KEY_LENGTH},
    }


# ---------------------------------------------------------------- HTTP



class Handler(BaseHTTPRequestHandler):
    # ------------------------------------------------------------ client protocol v1

    def _protocol_store(self):
        store = getattr(self.server, 'client_protocol_store', None)
        if store is None:
            store = _default_client_store()
            self.server.client_protocol_store = store
        return store

    def _auth_context(self):
        """Authenticate once per request and cache the non-secret context."""
        if not hasattr(self, '_request_auth_context'):
            store = self._protocol_store()
            key = getattr(self.server, 'access_key', None)
            bearer = bearer_credential(self.headers)
            # Device tokens may authenticate cross-origin; the legacy access
            # key remains same-origin-only so a leaked key cannot be embedded
            # in a remote web page.  Heartbeats are a device-token-only route
            # and perform the atomic last_seen update below.
            authenticator = None
            if bearer and not (key and hmac.compare_digest(bearer, key)):
                touch = urlparse(self.path).path == '/api/client/heartbeat'
                authenticator = lambda token: store.resolve_device(token, touch=touch)
            context = authorize(self.headers, key, authenticator)
            if (context and context.get('type') == 'access_key'
                    and not same_origin(self.headers)):
                context = None
            if (context and context.get('type') == 'cookie'
                    and not same_origin(self.headers)):
                context = None
            self._request_auth_context = context
        return self._request_auth_context

    def _authenticated(self) -> bool:
        return self._auth_context() is not None

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self._send(code, 'application/json; charset=utf-8', body)

    def _send_public_json(self, code: int, payload: dict) -> None:
        """JSON response for public cross-origin client bootstrap calls.

        The payload contains no credentials by construction.  Only origins
        that use Bearer auth are allowed to call us cross-origin in practice,
        so a browser can read this handshake before pairing.
        """
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        origin = self.headers.get('Origin')
        headers = ([('Access-Control-Allow-Origin', origin),
                    ('Vary', 'Origin')] if origin else [])
        self._send(code, 'application/json; charset=utf-8', body,
                   extra_headers=headers)

    def _cors_headers(self) -> list[tuple[str, str]]:
        context = self._auth_context()
        if not cors_granted(self.headers, context):
            return []
        return [
            ('Access-Control-Allow-Origin', self.headers.get('Origin')),
            ('Access-Control-Allow-Credentials', 'false'),
            ('Vary', 'Origin'),
        ]

    def _read_json(self, limit: int = 16384) -> dict:
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            raise ValueError('Invalid Content-Length')
        if length < 0 or length > limit:
            raise ValueError('JSON body is too large')
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode('utf-8') or '{}')
        except (UnicodeDecodeError, ValueError):
            raise ValueError('Invalid JSON body')
        if not isinstance(payload, dict):
            raise ValueError('JSON body must be an object')
        return payload

    def _request_scheme(self) -> str:
        # Dashboard authentication is currently HTTP-only.  Do not trust a
        # client-supplied forwarded scheme when deriving pairing URLs.
        return 'http'

    def _server_port(self) -> int:
        try:
            return int(self.server.server_port)
        except (AttributeError, TypeError, ValueError):
            try:
                return int((self.headers.get('Host') or '').rsplit(':', 1)[1])
            except (IndexError, TypeError, ValueError):
                return DEFAULT_PORT

    def _client_meta(self) -> dict:
        meta = client_meta(bool(self._protocol_store().pairing_available()))
        meta['server'] = {
            'name': meta.pop('server_name'),
            'protocol_version': meta['protocol_version'],
        }
        meta['runtime'] = {
            'packaged': bool(getattr(sys, 'frozen', False)),
            'desktop_session': True,
        }
        return meta

    def _session_payload(self, context: dict) -> dict:
        device = context.get('device') if context else None
        payload = self._client_meta()
        payload.update({
            'authenticated': True,
            'auth_type': context.get('type') if context else None,
            'device': device,
        })
        return payload

    def _pairing_payload(self) -> dict:
        store = self._protocol_store()
        created = store.create_ticket()
        base_urls = candidate_base_urls(
            self.headers.get('Host') or '',
            self._server_port(),
            scheme=self._request_scheme(),
        )
        payload = {
            'ok': True,
            'protocol_version': PROTOCOL_VERSION,
            'ticket': created['ticket'],
            'expires_at': created['expires_at'],
            'expires_at_iso': created['expires_at_iso'],
            'ticket_ttl_seconds': created['ticket_ttl_seconds'],
            'candidate_base_urls': base_urls,
        }
        payload.update(pairing_payloads(
            created['ticket'], created['expires_at'], base_urls))
        # The browser renders a standard PNG from the server instead of
        # maintaining a partial QR encoder. This keeps the pairing entry
        # compatible with phone scanners across versions and ECC modes.
        try:
            import base64
            from io import BytesIO
            try:
                import qrcode  # optional dependency
            except ImportError:
                # Keep the link/pairing flow usable even before `pip install -r
                # requirements.txt` is rerun. The UI renders its explicit
                # "copy link" fallback instead of crashing the whole route.
                raise
            link = payload.get('deep_link') or payload.get('payload') or ''
            if link:
                img = qrcode.make(link)
                buf = BytesIO()
                img.save(buf, format='PNG')
                payload['qr_image'] = ('data:image/png;base64,'
                                       + base64.b64encode(buf.getvalue()).decode('ascii'))
        except Exception:
            # The link remains copyable; the UI shows a clear fallback.
            pass
        return payload

    def _cors_preflight(self) -> None:
        origin = self.headers.get('Origin')
        request_method = (self.headers.get('Access-Control-Request-Method') or '').upper()
        request_headers = {
            item.strip().lower()
            for item in (self.headers.get('Access-Control-Request-Headers') or '').split(',')
            if item.strip()
        }
        allowed_methods = {'GET', 'POST', 'DELETE'}
        allowed_headers = {'content-type', 'authorization'}
        if (not origin or request_method not in allowed_methods
                or not request_headers.issubset(allowed_headers)):
            self._send(403, 'application/json',
                       b'{"ok":false,"msg":"CORS preflight rejected"}',
                       extra_headers=[('Vary', 'Origin')])
            return
        self._send(204, 'text/plain; charset=utf-8', b'', extra_headers=[
            ('Access-Control-Allow-Origin', origin),
            ('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS'),
            ('Access-Control-Allow-Headers', 'Content-Type, Authorization'),
            ('Access-Control-Max-Age', '600'),
            ('Vary', 'Origin'),
        ])

    def _handle_client_route(self, method: str, path: str) -> bool:
        """Handle protocol-v1 routes; return ``False`` when path is unrelated."""
        if not path.startswith('/api/client/'):
            return False
        if path == '/api/client/meta' and method == 'GET':
            self._send_public_json(200, self._client_meta())
            return True
        if path == '/api/client/pair' and method == 'POST':
            return self._client_pair(path)
        if path == '/api/client/pair':
            self._send_json(405, {'ok': False, 'error': 'method_not_allowed'})
            return True
        context = self._auth_context()
        if (not same_origin(self.headers) and bearer_credential(self.headers)
                and not context):
            # A cross-origin request that presents the legacy access key is
            # rejected explicitly rather than downgraded to a generic 401.
            self._send(403, 'application/json',
                       b'{"ok":false,"msg":"Cross-origin request rejected"}')
            return True
        if not context:
            self._send_json(401, {
                'ok': False,
                'error': 'authentication_required',
                'msg': '需要 Bearer 设备令牌或同源浏览器会话',
            })
            return True

        if path == '/api/client/session' and method == 'GET':
            self._send_json(200, self._session_payload(self._auth_context()))
            return True
        if path == '/api/client/devices' and method == 'GET':
            self._send_json(200, {
                'ok': True,
                'devices': self._protocol_store().list_devices(),
            })
            return True
        if path == '/api/client/heartbeat' and method == 'POST':
            context = self._auth_context()
            device = context.get('device') if context else None
            if not device:
                self._send_json(403, {
                    'ok': False,
                    'error': 'device_token_required',
                    'msg': '心跳只能由已配对设备发送',
                })
                return True
            updated = self._protocol_store().resolve_device(
                bearer_credential(self.headers) or '', touch=True)
            if not updated:
                self._send_json(401, {
                    'ok': False,
                    'error': 'device_revoked',
                    'msg': '设备令牌已失效',
                })
                return True
            self._send_json(200, {'ok': True, 'device': updated})
            return True
        if method == 'DELETE' and path.startswith('/api/client/devices/'):
            device_id = path[len('/api/client/devices/'):]
            if not re.fullmatch(r'[0-9a-f]{16}', device_id or ''):
                self._send_json(404, {'ok': False, 'error': 'device_not_found'})
                return True
            removed = self._protocol_store().revoke_device(device_id)
            if not removed:
                self._send_json(404, {'ok': False, 'error': 'device_not_found'})
                return True
            audit(f'客户端设备已撤销(来自 {self.client_address[0]}): {device_id}')
            self._send_json(200, {'ok': True, 'revoked': device_id})
            return True

        if path == '/api/client/pairing' and method == 'POST':
            # Authenticated creation route is implemented below after the
            # shared origin policy; return False to let do_POST continue.
            return False
        if method != 'GET' and method != 'DELETE':
            self._send_json(405, {'ok': False, 'error': 'method_not_allowed'})
            return True
        self._send_json(404, {'ok': False, 'error': 'not_found'})
        return True

    def _client_pair(self, path: str) -> bool:
        """Exchange a one-time ticket without requiring a browser session."""
        identity = self.client_address[0]
        retry_after = PAIRING_FAILURES.retry_after(identity)
        if retry_after:
            self._send_public_json(429, {
                'ok': False,
                'error': 'pairing_rate_limited',
                'msg': f'配对尝试过于频繁，请 {retry_after} 秒后再试',
                'retry_after': retry_after,
            })
            return True
        try:
            body = self._read_json(4096)
            name = body.get('name') or body.get('device_name')
            token, device = self._protocol_store().pair(
                body.get('ticket'), name, body.get('platform'))
        except InvalidPairingTicket:
            PAIRING_FAILURES.record_failure(identity)
            self._send_public_json(401, {
                'ok': False,
                'error': 'pairing_ticket_invalid',
                'msg': '配对票据无效、已过期或已使用',
            })
            return True
        except (ValueError, ProtocolStoreError) as exc:
            self._send_public_json(400 if isinstance(exc, ValueError) else 500, {
                'ok': False,
                'error': ('pairing_invalid_request'
                          if isinstance(exc, ValueError) else 'pairing_failed'),
                'msg': str(exc),
            })
            return True
        PAIRING_FAILURES.clear(identity)
        audit(f'客户端配对成功(来自 {identity}): {device["name"]} / {device["platform"]}')
        self._send_public_json(200, {
            'ok': True,
            'protocol_version': PROTOCOL_VERSION,
            'token': token,
            'token_type': 'Bearer',
            'device': device,
        })
        return True

    def _send(self, code: int, ctype: str, body: bytes, cache: str = 'no-store',
              extra_headers: list[tuple[str, str]] | None = None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', cache)
        headers = list(extra_headers or ())
        if not any(key.lower() == 'access-control-allow-origin'
                   for key, _ in headers):
            headers.extend(self._cors_headers())
        for key, value in headers:
            if value:
                self.send_header(key, value)
        # 文本类按客户端声明压缩（实测 index-*.js 470510 → 107851 B）。
        # ⚠ 压缩必须在 Content-Length 之前完成，否则长度对不上会截断下载（页面白屏）。
        if (len(body) >= _GZIP_MIN
                and _GZIP_TYPES.match(ctype)
                and 'gzip' in (self.headers.get('Accept-Encoding') or '')):
            body = gzip.compress(body, 6)
            self.send_header('Content-Encoding', 'gzip')
            # 同一 URL 因 Accept-Encoding 返回不同字节 → 必须声明 Vary，
            # 否则中间缓存可能把 gzip 版本发给不支持的客户端。
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _send_media(self, ctype: str, body: bytes, cache: str = 'no-store') -> None:
        """发送媒体，支持单个 bytes=start-end / start- / -suffix 范围。

        未知单位、畸形、逆序及多段请求忽略 Range，返回完整的 200 响应。
        有效但不可满足的范围返回 416；空媒体按 RFC 9110 14.2 忽略 Range，
        避免生成不存在的字节区间。206 的长度和 Content-Range 都对应实际片段。
        """
        total = len(body)
        rng = str(self.headers.get('Range') or '').strip()
        start, end, partial, unsatisfiable = 0, total - 1, False, False
        unit, separator, spec = rng.partition('=')
        if total and separator and unit.lower() == 'bytes':
            match = re.fullmatch(r'([0-9]*)-([0-9]*)', spec.strip())
            if match and any(match.groups()):
                first, last = match.groups()
                left, right = first.lstrip('0') or '0', last.lstrip('0') or '0'
                ordered = not first or not last or (len(left), left) <= (len(right), right)
                if ordered:
                    def bounded_number(digits: str) -> int:
                        # Range 数值可能长于 Python 的 int 转换上限；只需区分是否超过总长。
                        digits = digits.lstrip('0') or '0'
                        return total if len(digits) > len(str(total)) else min(int(digits), total)

                    if first:
                        start = bounded_number(first)
                        end = min(bounded_number(last), total - 1) if last else total - 1
                        unsatisfiable = start >= total
                    else:
                        suffix = bounded_number(last)
                        start = total - suffix
                        unsatisfiable = suffix == 0
                    partial = not unsatisfiable
        if unsatisfiable:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{total}')
            payload = b''
        elif partial:
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{total}')
            payload = body[start:end + 1]
        else:
            self.send_response(200)
            payload = body
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', cache)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(len(payload)))
        for key, value in self._cors_headers():
            if value:
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            pass

    def do_DELETE(self):
        u = urlparse(self.path)
        if not same_origin(self.headers):
            context = self._auth_context()
            if not (context and context.get('type') == 'device'):
                self._send(403, 'application/json',
                           b'{"ok":false,"msg":"Cross-origin request rejected"}')
                return
        if self._handle_client_route('DELETE', u.path):
            return
        if u.path == '/api/client/pairing':
            self._send_json(405, {'ok': False, 'error': 'method_not_allowed'})
            return
        self._send_json(404, {'ok': False, 'error': 'not_found'})

    def do_OPTIONS(self):
        if not urlparse(self.path).path.startswith('/api/'):
            self._send(404, 'text/plain', b'not found')
            return
        self._cors_preflight()

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if self._handle_client_route('GET', path):
            return
        needs_auth = (path.startswith(('/api/', '/files/'))
                      or path in ('/', '/index.html', '/next', '/next/'))
        # Remote/native clients need the app shell before they have a cookie.
        # API endpoints and private files stay behind auth; the shell itself
        # contains no data and its JS immediately shows the pairing gate.
        if path in ('/', '/index.html', '/next', '/next/'):
            needs_auth = False
        if needs_auth and not (self._authenticated() or _local_control_authorized(self)):
            if path.startswith(('/api/', '/files/')):
                self._send_json(401, {
                    'ok': False,
                    'error': 'authentication_required',
                    'msg': '请刷新页面并完成访问验证',
                })
            else:
                self._send(200, 'text/html; charset=utf-8', LOGIN_HTML.encode())
            return
        try:
            if path in ('/', '/index.html'):
                # 新版前端（React + TS）的构建产物。旧版那 4214 行 HTML 常量已随迁移完成删除。
                # `/next` 与 `/assets/` 都走同一个静态目录（见下面的分支），老书签/主屏图标还能用。
                fp = (BASE / 'web' / 'dist' / 'index.html').resolve()
                if fp.is_file():
                    self._send(200, 'text/html; charset=utf-8', fp.read_bytes())
                else:
                    self._send(503, 'text/plain; charset=utf-8',
                               b'web/dist not built: cd web && ./build.sh')
            elif path == '/manifest.json':
                self._send(200, 'application/manifest+json',
                           MANIFEST_JSON.encode('utf-8'))
            elif path == '/sw.js':
                self._send(200, 'application/javascript', SW_JS.encode('utf-8'))
            elif path.startswith('/assets/') or path.startswith('/next'):
                # 新版前端的静态资源（web/dist/）。
                # - `/assets/*` 是 Vite 的产物路径（base = '/'）
                # - `/next*` 是迁移期的兼容别名，老页面/书签仍指着它
                # 构建：cd web && ./build.sh（Vite；需要用户自己装的 node）。
                rel = (path[len('/next'):].lstrip('/') if path.startswith('/next')
                       else path.lstrip('/')) or 'index.html'
                fp = (BASE / 'web' / 'dist' / rel).resolve()
                root = (BASE / 'web' / 'dist').resolve()
                _NCT = {'.html': 'text/html; charset=utf-8',
                        '.js': 'application/javascript',
                        '.css': 'text/css', '.json': 'application/json',
                        '.png': 'image/png',
                        '.svg': 'image/svg+xml', '.woff2': 'font/woff2'}
                # 注：**不再放行 `.map`** —— 它含完整 TS/TSX 源码，而仪表盘无鉴权，
                # 内网任何人可直接下载。构建侧也已关掉 sourcemap（vite.config.ts），
                # 这里兜底防"将来有人又把 sourcemap 打开"。见 03 文档 D 节。
                # 与 /qp-icons 同样的归属校验：resolve 后必须仍在 web/dist 内
                if fp.is_file() and fp.is_relative_to(root) and fp.suffix.lower() in _NCT:
                    # 带内容 hash 的产物可长缓存（Vite 文件名自带 hash）；
                    # index.html 等不带 hash 的仍 no-store，否则发现不了新版本。
                    cache = (CACHE_IMMUTABLE if _HASHED_ASSET_RE.search(fp.name)
                             else CACHE_NONE)
                    self._send(200, _NCT[fp.suffix.lower()], fp.read_bytes(), cache)
                else:
                    self._send(404, 'text/plain; charset=utf-8',
                               b'not found (build first: cd web && ./build.sh)')
            elif path.startswith('/qp-icons/'):
                # 路径归属校验：resolve 后必须仍在 static/qp-icons 内
                # （不要用 `'qp-icons' in fp.parts` 那种写法——`/qp-icons/../x.png`
                #   的 parts 里同样含 'qp-icons'，会放行穿越，实测返回 200）
                fp = (BASE / 'static' / path.lstrip('/')).resolve()
                root = (BASE / 'static' / 'qp-icons').resolve()
                # 白名单扩展名（背景是 .jpg；仍不接受任意后缀，避免误发其它文件）。
                # .mp4 = 「花田诗意」动态背景的视频层（见 web/src/lib/scene.ts 的 video 字段）。
                CTYPE = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                         '.webp': 'image/webp', '.svg': 'image/svg+xml',
                         '.mp4': 'video/mp4'}
                suffix = fp.suffix.lower()
                if fp.exists() and suffix in CTYPE and fp.is_relative_to(root):
                    # 视频必须支持 Range 请求：iOS Safari 对 <video> 强制走 206，
                    # 服务器不认 Range 只回 200 全量时，Safari 直接**不播**
                    # （Chrome/桌面端能播，所以这个坑只在真机上暴露）。
                    if suffix == '.mp4':
                        self._send_media(CTYPE[suffix], fp.read_bytes(), 'max-age=3600')
                    else:
                        self._send(200, CTYPE[suffix], fp.read_bytes(), 'max-age=3600')
                else:
                    self._send(404, 'text/plain', b'not found')
            elif path in ('/icon-192.png', '/icon-512.png', '/apple-touch-icon.png'):
                # apple-touch-icon.png：iOS 主屏图标（HTML 里 <link rel="apple-touch-icon"> 指向它）。
                # 单独一个文件名是为了「换图标时能换 URL」—— iOS 按 URL 缓存该图标，
                # 复用 icon-192.png 的话内容更新了主屏也不会变（见 HTML 里的注释）。
                fp = BASE / 'static' / path.lstrip('/')
                if fp.exists():
                    data = fp.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Cache-Control', 'max-age=3600')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send(404, 'text/plain', b'not found')
            elif path == '/api/data':
                body = json.dumps(build_data(), ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif path == '/api/runner/pause':
                body = json.dumps({
                    'ok': True,
                    'paused': PAUSE_SIGNAL.exists(),
                    'scheduler': scheduler_info(),
                }, ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif path == '/api/notify/history':
                from src.notify import HISTORY_FILE
                records = []
                # 历史文件是首次发送成功后才创建的；缺失不是错误，空列表即可
                # （否则手机端"最近发送的通知"首屏直接 500）。
                history_lines = tail_lines(HISTORY_FILE, 20) if HISTORY_FILE.is_file() else []
                for line in history_lines:
                    try:
                        record = json.loads(line)
                        if isinstance(record, dict):
                            records.append(record)
                    except ValueError:
                        pass
                self._send(200, 'application/json; charset=utf-8',
                           json.dumps({'records': records[::-1]},
                                      ensure_ascii=False).encode())
            elif path == '/api/adb':
                # 单独一个接口（而不是并进 /api/data）：它要真跑一次 adb devices，
                # 几百毫秒起步，放 /api/data 会拖慢每 6 秒一次的整体刷新。
                body = json.dumps(adb_info(), ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif path == '/api/adventure':
                q = parse_qs(u.query)
                body = json.dumps(adventure_data((q.get('date') or [''])[0]),
                                  ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif path == '/api/rewards':
                q = parse_qs(u.query)
                body = json.dumps(rewards_data((q.get('date') or [''])[0]),
                                  ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif path == '/api/friends':
                body = json.dumps({'friends': list_friends(), **friends_meta()},
                                  ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif path == '/api/plan':
                body = json.dumps(plan_data(), ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif path == '/api/logs':
                q = parse_qs(u.query)
                try:
                    n = min(int((q.get('tail') or ['250'])[0]), 1000)
                except ValueError:
                    n = 250
                p = today_log_path()
                lines = tail_lines(p, n) if p else []
                body = json.dumps({'name': p.name if p else None,
                                   'total': len(lines), 'lines': lines},
                                  ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif path == '/api/screenshot':
                try:
                    data, at, _cached = capture_phone()
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Shot-At', at)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as e:
                    if _shot_cache['data'] and time.time() - _shot_cache['ts'] < 120:
                        data = _shot_cache['data']
                        self.send_response(200)
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('X-Shot-At', _shot_cache['at'] + '(缓存)')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    else:
                        self._send(503, 'text/plain; charset=utf-8',
                                   f'截图失败: {e}'.encode('utf-8'))
            elif path.startswith('/files/'):
                name = path[len('/files/'):]
                f = RUNS / name
                if re.fullmatch(r'[A-Za-z0-9_.\-]+\.png', name) and f.is_file():
                    self._send(200, 'image/png', f.read_bytes(), cache='private, max-age=86400')
                else:
                    self._send(404, 'text/plain', b'not found')
            else:
                self._send(404, 'text/plain', b'not found')
        except Exception as e:
            try:
                self._send(500, 'text/plain; charset=utf-8', str(e).encode('utf-8'))
            except Exception:
                pass

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == '/api/client/pair':
            self._handle_client_route('POST', u.path)
            return
        if u.path.startswith('/api/client/'):
            if self._handle_client_route('POST', u.path):
                return
            # Only pairing-ticket creation falls through to the shared
            # origin/auth policy below; all other protocol POSTs are handled
            # by the protocol dispatcher.
        if u.path == '/api/auth':
            if not same_origin(self.headers):
                self._send(403, 'application/json',
                           b'{"ok":false,"msg":"Cross-origin request rejected"}')
                return
            retry_after = auth_retry_after(self.client_address[0])
            if retry_after:
                self._send(429, 'application/json; charset=utf-8',
                           json.dumps({'ok': False, 'msg': f'尝试过于频繁，请 {retry_after} 秒后再试'},
                                      ensure_ascii=False).encode())
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
                if length < 0 or length > 4096:
                    raise ValueError('Invalid authentication payload')
                body = self.rfile.read(length).decode('utf-8')
                is_json = 'application/json' in self.headers.get('Content-Type', '')
                key = (json.loads(body).get('key', '') if is_json
                       else parse_qs(body).get('key', [''])[0])
                access_key = getattr(self.server, 'access_key', None)
                valid = bool(access_key and isinstance(key, str)
                             and hmac.compare_digest(key.strip(), access_key))
            except (ValueError, TypeError, AttributeError):
                valid = False
                is_json = True
            if not valid:
                auth_record_failure(self.client_address[0])
                if is_json:
                    self._send(401, 'application/json; charset=utf-8',
                               json.dumps({'ok': False, 'msg': '访问码不正确'},
                                          ensure_ascii=False).encode())
                else:
                    self._send(401, 'text/html; charset=utf-8',
                               LOGIN_HTML.replace('<form ', '<p role="alert">访问码不正确，请重试。</p><form ', 1).encode())
                return
            auth_clear_failures(self.client_address[0])
            self.send_response(200 if is_json else 303)
            self.send_header('Set-Cookie', f'{COOKIE_NAME}={session_value(access_key)}; '
                             'Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000')
            self.send_header('Cache-Control', 'no-store')
            if not is_json:
                self.send_header('Location', '/')
            payload = b'{"ok":true}' if is_json else b''
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        local_control = _local_control_authorized(self)
        if not same_origin(self.headers) and not local_control:
            context = self._auth_context()
            # Native device tokens may call cross-origin, but the response must
            # carry CORS headers or browser clients cannot read it.  Cookies
            # and the legacy access-key Bearer remain same-origin only.
            if not (context and context.get('type') == 'device'):
                self._send(403, 'application/json',
                           b'{"ok":false,"msg":"Cross-origin request rejected"}')
                return

        if not (self._authenticated() or local_control):
            self._send_json(401, {'ok': False, 'msg': '请刷新页面并完成访问验证'})
            return
        try:
            if u.path == '/api/desktop/session':
                if not local_control or not same_origin(self.headers):
                    self._send_json(403, {
                        'ok': False, 'error': 'local_control_required',
                    })
                    return
                key = getattr(self.server, 'access_key', None)
                if not key:
                    self._send_json(503, {
                        'ok': False, 'error': 'session_unavailable',
                    })
                    return
                self._send_json(200, {
                    'cookieName': COOKIE_NAME,
                    'cookieValue': session_value(key),
                })
            elif u.path == '/api/client/pairing':
                try:
                    result = self._pairing_payload()
                except ProtocolStoreError as exc:
                    self._send_json(500, {
                        'ok': False,
                        'error': 'pairing_unavailable',
                        'msg': str(exc),
                    })
                    return
                audit(f'客户端配对票据创建(来自 {self.client_address[0]})')
                self._send_json(200, result)
            elif u.path == '/api/onboarding':
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if length < 0 or length > 4096:
                        raise ValueError('Invalid onboarding payload')
                    payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                except (ValueError, UnicodeDecodeError):
                    self._send_json(400, {
                        'ok': False, 'error': 'invalid_onboarding_action',
                    })
                    return
                if not isinstance(payload, dict) or payload.get('action') != 'complete':
                    self._send_json(400, {
                        'ok': False, 'error': 'invalid_onboarding_action',
                    })
                    return
                self._send_json(200, complete_onboarding())
            elif u.path == '/api/access-key':
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                ok, fixed = validate_new_key(payload.get('key'))
                if not ok:
                    body = json.dumps({'ok': False, 'msg': fixed}, ensure_ascii=False).encode()
                    self._send(400, 'application/json; charset=utf-8', body)
                    return
                key_file = getattr(self.server, 'access_key_file', None)
                if key_file is None:
                    body = json.dumps({'ok': False, 'msg': '当前仪表盘没有可写的访问码文件'},
                                      ensure_ascii=False).encode()
                    self._send(400, 'application/json; charset=utf-8', body)
                    return
                # 会话是访问码的 HMAC：换了访问码所有旧会话立即失效，
                # 当前浏览器要**同时**换发新 Cookie，否则这次操作会把自己踢下线。
                with AUTH_LOCK:
                    save_access_key(Path(key_file), fixed)
                    self.server.access_key = fixed
                    auth_clear_failures(self.client_address[0])
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Set-Cookie', f'{COOKIE_NAME}={session_value(fixed)}; '
                                 'Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000')
                payload_out = json.dumps({'ok': True, 'msg': '访问码已更新'}, ensure_ascii=False).encode()
                self.send_header('Content-Length', str(len(payload_out)))
                self.end_headers()
                self.wfile.write(payload_out)
                audit(f'访问码已更新(来自 {self.client_address[0]})：其余设备需重新验证')
            elif u.path == '/api/settings':
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                updates = payload.get('updates') or {}
                result = apply_settings(updates)
                audit(f'设置保存(来自 {self.client_address[0]}): 提交 {updates} '
                      f'→ 生效 {result["applied"]}'
                      + (f' 拒绝 {result["rejected"]}' if result['rejected'] else ''))
                # 唤醒调度器立即重读配置（否则它可能正在 30s 轮询睡眠里等下一轮）
                if result.get('ok') and result.get('applied'):
                    try:
                        write_text_atomic(RUNS / 'reload.signal', str(time.time()))
                    except OSError:
                        pass
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self._send(200 if result['ok'] else 400,
                           'application/json; charset=utf-8', body)
            elif u.path == '/api/adb/connect':
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                result = adb_connect(str(payload.get('addr') or ''))
                audit(f'ADB 连接(来自 {self.client_address[0]}): {payload.get("addr")} → {result.get("msg")}')
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self._send(200 if result.get('ok') else 400,
                           'application/json; charset=utf-8', body)
            elif u.path == '/api/plan':
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                result = apply_plan(payload.get('updates') or {})
                audit(f'职业计划进度更新(来自 {self.client_address[0]}): {result["applied"]}')
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self._send(200 if result['ok'] else 400,
                           'application/json; charset=utf-8', body)
            elif u.path == '/api/plan/sync':
                result = career_sync()
                audit(f'职业进度自动识别(来自 {self.client_address[0]}): {result}')
                body = json.dumps({'sync': result, 'plan': plan_data()},
                                  ensure_ascii=False).encode('utf-8')
                self._send(200, 'application/json; charset=utf-8', body)
            elif u.path == '/api/preset/alt':
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                name = str(payload.get('name') or '').strip()
                if not name:
                    body = json.dumps({'ok': False, 'error': '缺少大号名称', 'applied': {}, 'rejected': []},
                                      ensure_ascii=False).encode('utf-8')
                    self._send(400, 'application/json; charset=utf-8', body)
                else:
                    from src.presets import apply_alt_preset
                    result = apply_alt_preset(name, dry_run=bool(payload.get('dry')))
                    result['ok'] = not result['rejected']
                    audit(f'应用小号预设(来自 {self.client_address[0]}): 大号={name} '
                          f'→ 生效 {len(result["applied"])} 项')
                    body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                    self._send(200 if result['ok'] else 400,
                               'application/json; charset=utf-8', body)
            elif u.path == '/api/notify/test':
                # 设置页「发送测试通知」：按当前 config.yaml 的 notify 配置发一条
                length = int(self.headers.get('Content-Length') or 0)
                try:
                    payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                except Exception:
                    payload = {}
                from src.notify import test_notify
                result = test_notify(str(payload.get('target') or 'all'))
                audit(f'测试通知(来自 {self.client_address[0]}): {result.get("msg")}')
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self._send(200 if result.get('ok') else 400,
                           'application/json; charset=utf-8', body)
            elif u.path == '/api/runner/start':
                result = scheduler_start()
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self._send(200 if result.get('ok') else 400,
                           'application/json; charset=utf-8', body)
            elif u.path in ('/api/runner/pause', '/api/runner/resume'):
                result = scheduler_pause() if u.path.endswith('pause') else scheduler_resume()
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self._send(200 if result.get('ok') else 400,
                           'application/json; charset=utf-8', body)
            elif u.path == '/api/runner/stop':
                result = scheduler_stop()
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self._send(200 if result.get('ok') else 400,
                           'application/json; charset=utf-8', body)
            else:
                self._send(404, 'text/plain', b'not found')
        except Exception as e:
            try:
                self._send(500, 'text/plain; charset=utf-8', str(e).encode('utf-8'))
            except Exception:
                pass

    def log_message(self, format, *args):  # noqa: A002 - 静音访问日志
        pass


def main():
    port = DEFAULT_PORT
    bind = '0.0.0.0'
    if '--port' in sys.argv:
        port = int(sys.argv[sys.argv.index('--port') + 1])
    if '--bind' in sys.argv:
        bind = sys.argv[sys.argv.index('--bind') + 1]

    # 端口占用时不要甩一堆 traceback 就死——给出可操作的提示（常见于重复启动/上次没退干净）
    try:
        srv = ThreadingHTTPServer((bind, port), Handler)
        key_file = Path(os.environ.get('QQPET_DASHBOARD_ACCESS_FILE')
                        or RUNS / 'dashboard_access.token')
        srv.access_key = load_access_key(key_file)
        srv.access_key_file = key_file      # /api/access-key 改码时写回同一个文件
        set_client_protocol_store(ClientProtocolStore(RUNS / 'client_protocol.json'))
        _local_control_key()                # create the 0600 desktop control credential
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, errno.EACCES):
            print(f'端口 {port} 已被占用，仪表盘未启动。'
                  f'先停掉占用进程：kill $(lsof -nP -iTCP:{port} -sTCP:LISTEN -t)',
                  file=sys.stderr, flush=True)
            audit(f'启动失败：端口 {port} 被占用（{e}）')
            return 1
        raise

    # 记录生命周期：进程是"被谁杀/何时死"的唯一线索（此前日志被启动脚本覆盖，无从排查）
    port = srv.server_port
    audit(f'仪表盘启动: pid={os.getpid()} port={port} '
          f'ppid={os.getppid()} argv={" ".join(sys.argv[1:]) or "-"}')
    print(f'QQ宠物托管仪表盘已启动: http://{bind}:{port} (Ctrl+C 停止)', flush=True)
    print(f'首次访问需验证；访问码文件: {key_file}', flush=True)

    # TG 控制机器人（反向通道）：后台长轮询。**必须在 serve_forever 之前起**，
    # 否则第一次 /api/data 里 tgbot_running 会是 false（设置页显示"线程没跑"）。
    # 没开开关 / 没配 token 时线程只睡不联网，见 src/tg_bot.py 的 Worker._run。
    if os.environ.get('QQPET_DISABLE_TG') != '1':
        start_tg_bot()


    # 客户端的半截连接（刷新/切页/手机休眠断连）不该打堆栈刷屏——静音，交给下面统一记账
    def _quiet_conn_error(request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        srv._real_handle_error(request, client_address)

    srv._real_handle_error = srv.handle_error
    srv.handle_error = _quiet_conn_error

    def _on_signal(signum, _frame):
        raise KeyboardInterrupt

    # 关终端/被 launchd 或 kill 收走时留下死因（SIGTERM/SIGHUP 默认直接死，静默无痕）
    for _name in ('SIGTERM', 'SIGHUP'):
        _sig = getattr(signal, _name, None)
        if _sig is None:
            continue
        try:
            signal.signal(_sig, _on_signal)
        except (ValueError, OSError):
            pass

    code = 0
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        audit('仪表盘退出: 收到中断信号（Ctrl+C / SIGTERM / SIGHUP）')
    except SystemExit:
        audit('仪表盘退出: SystemExit')
        raise
    except BaseException as e:
        # 兜底：任何意外都不许静默死掉，记录后以非零码退出，便于外部守护脚本分辨
        audit(f'仪表盘异常退出: {type(e).__name__}: {e}')
        raise
    else:
        audit('仪表盘退出: serve_forever 正常返回')
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
    return code


if __name__ == '__main__':
    sys.exit(main())
