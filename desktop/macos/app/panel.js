import { panelLayout } from './panel-layout.js';

const invoke = window.__TAURI_INTERNALS__?.invoke;

const $ = (selector) => document.querySelector(selector);
const connState = $('#conn-state');
const runnerDot = $('#runner-dot');
const runnerState = $('#runner-state');
const remaining = $('#remaining');
const activity = $('#activity');
const detail = $('#detail');
const learnMin = $('#learn-min');
const workMin = $('#work-min');
const effPct = $('#eff-pct');
const todayTotal = $('#today-total');
const queuePending = $('#queue-pending');
const uptime = $('#uptime');
const nextTask = $('#next-task');
const toggleRunner = $('#toggle-runner');
const pauseRunner = $('#pause-runner');
const notice = $('#notice');
const panelCard = $('#panel-card');
const scrim = $('#scrim');
const updateCard = $('#update-card');
const updateTitle = $('#update-title');
const updateState = $('#update-state');
const updateDetail = $('#update-detail');
const updateAction = $('#update-action');
const updateCheck = $('#update-check');
const updateProgress = $('#update-progress');
const updateBar = updateProgress?.firstElementChild;
let snapshot = null;
let sampledAt = 0;
let connected = false;
let paused = false;
let busy = false;

const place = () => {
  const anchor = window.__qpetAnchor;
  if (!anchor || !panelCard) return;
  const viewport = {
    anchor,
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
  };
  const scrollTop = panelCard.scrollTop;
  panelCard.style.width = `${panelLayout(viewport).width}px`;
  panelCard.style.maxHeight = 'none';
  const contentHeight = Math.ceil(panelCard.getBoundingClientRect().height);
  const layout = panelLayout({ ...viewport, contentHeight });
  panelCard.style.left = `${layout.left}px`;
  panelCard.style.top = `${layout.top}px`;
  panelCard.style.maxHeight = `${layout.maxHeight}px`;
  panelCard.scrollTop = scrollTop;
};

const setText = (node, value) => {
  if (node) node.textContent = value == null || value === '' ? '—' : String(value);
};

const secsText = (value, showHours = false) => {
  const n = Math.max(0, Number(value) || 0);
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = Math.floor(n % 60);
  return h || showHours ? `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

const uptimeSeconds = (value) => {
  const match = /^(?:(\d+)-)?(\d+):([0-5]\d)(?::([0-5]\d))?$/.exec(value || '');
  if (!match || (match[1] && !match[4])) return null;
  const hours = match[4] ? Number(match[2]) : 0;
  const minutes = Number(match[4] ? match[3] : match[2]);
  const seconds = (Number(match[1] || 0) * 24 + hours) * 3600
    + minutes * 60 + Number(match[4] || match[3]);
  return minutes <= 59 && Number.isSafeInteger(seconds) ? seconds : null;
};

const renderClocks = () => {
  if (!connected || !snapshot) return;
  // Use elapsed time so a throttled or hidden panel catches up on its next tick.
  const elapsed = Math.max(0, Math.floor((Date.now() - sampledAt) / 1000));
  const scheduler = snapshot.scheduler || {};
  const remain = Math.max(0, Number(snapshot.work_eta?.remaining || 0) - elapsed);
  remaining.textContent = scheduler.alive && remain > 0 ? secsText(remain) : '';
  const base = uptimeSeconds(scheduler.uptime);
  setText(uptime, !scheduler.alive ? null
    : base == null ? scheduler.uptime : secsText(base + elapsed, true));
};

const setConnection = (state, label) => {
  connState.dataset.state = state;
  connState.lastElementChild.textContent = label;
};

const setBusy = (value) => {
  busy = value;
  toggleRunner.disabled = value || !snapshot;
  pauseRunner.disabled = value || !snapshot || !snapshot.scheduler?.alive;
};

const render = (data) => {
  snapshot = data;
  sampledAt = Date.now();
  connected = true;
  const scheduler = data?.scheduler || {};
  const queue = data?.queue || {};
  const today = data?.today_duration || {};
  const alive = !!scheduler.alive;
  setConnection('online', '本机在线');
  runnerDot.className = 'runner-dot ' + (!alive ? 'idle' : paused ? 'paused' : 'on');
  runnerState.textContent = !alive ? '托管未启动' : paused ? '托管已暂停' : '托管运行中';
  renderClocks();
  activity.textContent = !alive ? '调度器当前未运行'
    : queue.current ? `${queue.current}进行中`
      : queue.next ? `等待下一项：${queue.next}` : '等待下一项任务';
  detail.textContent = queue.next_reason || data?.last_line?.replace(/^\[[\d:]+\]\s*/, '') || '';
  setText(learnMin, today.learn_min);
  setText(workMin, today.work_min);
  setText(effPct, today.eff_pct == null ? null : `${today.eff_pct}%`);
  setText(todayTotal, today.total_min);
  setText(queuePending, queue.pending_count ?? 0);
  setText(nextTask, queue.next ? `下一项 ${queue.next}` : '');
  toggleRunner.textContent = alive ? '停止托管' : '启动托管';
  toggleRunner.classList.toggle('running', alive);
  pauseRunner.textContent = paused ? '继续' : '暂停';
  setBusy(busy);
  place();
  document.body.dataset.ready = 'true';
};

const renderOffline = (message) => {
  connected = false;
  setConnection('offline', '本机离线');
  runnerDot.className = 'runner-dot error';
  runnerState.textContent = '托管服务离线';
  remaining.textContent = '';
  setText(uptime, null);
  activity.textContent = message || '无法读取本机仪表盘';
  detail.textContent = '请确认后台服务是否仍在运行';
  toggleRunner.disabled = true;
  pauseRunner.disabled = true;
  place();
  document.body.dataset.ready = 'true';
};

const call = async (command, payload = {}) => {
  if (!invoke) throw new Error('菜单栏面板只能从 macOS App 打开');
  return invoke(command, payload);
};

const UPDATE_ACTIVE = new Set(['downloading', 'installing', 'restarting']);
const UPDATE_POLL = new Set(['checking', ...UPDATE_ACTIVE]);
let updateStatus = null;
let updatePhase = 'idle';
let updatePoll = 0;

const refresh = async () => {
  // Starting the dashboard while an update is being installed would bring the
  // old backend back up; the update card owns the panel during this window.
  if (UPDATE_ACTIVE.has(updatePhase)) return;
  try {
    const data = await call('panel_snapshot');
    paused = !!data?.paused;
    render(data?.data);
  } catch (error) {
    renderOffline(String(error));
  }
};

const refreshOnShow = () => {
  renderClocks();
  if (UPDATE_ACTIVE.has(updatePhase)) {
    void refreshUpdateStatus();
    return;
  }
  void refresh();
  void checkForUpdate(false);
};

const firstNoteLine = (notes) => String(notes || '')
  .split('\n')
  .map((line) => line.replace(/^[#>*\-\s]+/, '').trim())
  .find(Boolean) || '';

const formatMb = (bytes) => `${((Number(bytes) || 0) / 1048576).toFixed(1)} MB`;

const renderUpdate = (status) => {
  const phase = status?.phase || 'idle';
  const current = status?.current ? `v${status.current}` : '';
  const latest = status?.latest ? `v${status.latest}` : '';
  const done = Number(status?.done) || 0;
  const total = Number(status?.total) || 0;
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
  const active = UPDATE_ACTIVE.has(phase);
  updateStatus = status;
  updatePhase = phase;
  updateCard.dataset.state = phase;
  updateTitle.textContent = phase === 'available' ? '发现新版本'
    : phase === 'downloading' ? '正在下载更新'
      : phase === 'installing' ? '正在安装更新'
        : phase === 'restarting' ? '正在重启' : '版本更新';
  updateState.textContent = phase === 'available' ? latest
    : phase === 'checking' ? '检查中…'
      : phase === 'latest' ? '已是最新'
        : phase === 'downloading' ? (percent == null ? '下载中' : `${percent}%`)
          : phase === 'installing' ? '安装中'
            : phase === 'restarting' ? '重启中'
              : phase === 'error' ? '更新失败' : '未检查';
  const detail = phase === 'available'
    ? (firstNoteLine(status?.notes) || (status?.installable
      ? `当前 ${current}，点击「更新」下载并安装 ${latest}。`
      : `当前 ${current}，请打开发布页下载 ${latest}。`))
    : phase === 'latest' ? `当前 ${current}，已是最新版本。`
      : phase === 'checking' ? '正在连接更新源…'
        : phase === 'downloading' ? (done === 0 && total === 0
          ? '正在准备更新…'
          : percent == null
            ? `下载中… ${formatMb(done)}`
            : `下载中 ${percent}% · ${formatMb(done)} / ${formatMb(total)}`)
          : phase === 'installing' ? '下载完成，正在安装…'
            : phase === 'restarting' ? '正在重启以完成更新…'
              : phase === 'error' ? (status?.error || '更新失败，请稍后重试。')
                : '点击「检查更新」查看新版本。';
  updateDetail.textContent = detail;
  const showProgress = active;
  updateProgress.hidden = !showProgress;
  if (showProgress) {
    const indeterminate = phase === 'downloading' && percent == null;
    updateProgress.dataset.indeterminate = indeterminate ? 'true' : 'false';
    updateBar.style.width = indeterminate ? '' : `${phase === 'downloading' ? (percent ?? 0) : 100}%`;
  }
  const canInstall = phase === 'available' && !!status?.installable;
  updateAction.hidden = !(phase === 'available' || phase === 'error');
  updateAction.dataset.action = canInstall ? 'install' : 'release';
  updateAction.textContent = canInstall ? '更新' : '打开发布页';
  updateAction.disabled = active;
  updateCheck.hidden = active;
  updateCheck.disabled = phase === 'checking';
  updateCheck.textContent = phase === 'error' ? '重试' : '检查更新';
  scheduleUpdatePoll();
  place();
};

function scheduleUpdatePoll() {
  window.clearTimeout(updatePoll);
  if (!UPDATE_POLL.has(updatePhase)) return;
  updatePoll = window.setTimeout(async () => {
    try {
      renderUpdate(await call('panel_update_status'));
    } catch (error) {
      renderUpdate({ ...(updateStatus || {}), phase: updatePhase, error: String(error) });
    }
  }, 600);
}

async function refreshUpdateStatus() {
  if (!invoke) return;
  try {
    renderUpdate(await call('panel_update_status'));
  } catch {
    // The panel keeps its last state; the periodic snapshot will report errors.
  }
}

async function checkForUpdate(force = false) {
  if (!invoke || UPDATE_ACTIVE.has(updatePhase) || updatePhase === 'checking') return;
  renderUpdate({ ...(updateStatus || {}), phase: 'checking' });
  try {
    renderUpdate(await call('panel_update_check', { force }));
  } catch (error) {
    renderUpdate({ ...(updateStatus || {}), phase: 'error', error: String(error) });
  }
}

async function startUpdate() {
  if (!invoke || updateAction.disabled) return;
  updateAction.disabled = true;
  try {
    renderUpdate(await call('panel_update_install'));
  } catch (error) {
    notice.textContent = String(error);
    await refreshUpdateStatus();
  }
}

const act = async (command, successText) => {
  if (busy) return;
  setBusy(true);
  notice.textContent = '';
  try {
    const result = await call(command);
    if (result?.ok === false) throw new Error(result.msg || result.error || '操作失败');
    notice.textContent = result?.msg || successText;
    await refresh();
  } catch (error) {
    notice.textContent = String(error);
  } finally {
    setBusy(false);
  }
};

toggleRunner.addEventListener('click', () => {
  const alive = !!snapshot?.scheduler?.alive;
  void act(alive ? 'panel_stop_runner' : 'panel_start_runner', alive ? '已停止托管' : '已启动托管');
});
pauseRunner.addEventListener('click', () => {
  void act(paused ? 'panel_resume_runner' : 'panel_pause_runner', paused ? '已继续托管' : '已请求暂停');
});
$('#open-dashboard').addEventListener('click', () => void call('panel_open_dashboard'));
$('#open-settings').addEventListener('click', () => void call('panel_open_settings'));
$('#quit-app').addEventListener('click', () => void call('panel_quit'));
updateCheck.addEventListener('click', () => void checkForUpdate(true));
updateAction.addEventListener('click', () => {
  if (updateAction.dataset.action === 'install') {
    void startUpdate();
    return;
  }
  void call('panel_update_open_release').catch((error) => {
    notice.textContent = String(error);
  });
});

scrim.addEventListener('click', () => void call('panel_hide'));
panelCard.addEventListener('pointerdown', (event) => {
  // Keep clicks inside the card from behaving like a click on the scrim.
  event.stopPropagation();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') void call('panel_hide');
});

window.__qpetRefresh = refreshOnShow;
window.__qpetPlace = place;
window.addEventListener('resize', place);
void refresh();
void refreshUpdateStatus();
void checkForUpdate(false);
window.addEventListener('qpet-panel-show', refreshOnShow);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshOnShow();
});
const clockTimer = window.setInterval(() => {
  if (document.visibilityState === 'visible') renderClocks();
}, 1000);
const timer = window.setInterval(() => {
  if (document.visibilityState === 'visible') void refresh();
}, 5000);
window.addEventListener('beforeunload', () => {
  window.clearInterval(timer);
  window.clearInterval(clockTimer);
});
