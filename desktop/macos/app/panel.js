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
let snapshot = null;
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

const secsText = (value) => {
  const n = Math.max(0, Number(value) || 0);
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = Math.floor(n % 60);
  return h ? `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
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
  const scheduler = data?.scheduler || {};
  const queue = data?.queue || {};
  const today = data?.today_duration || {};
  const eta = data?.work_eta || {};
  const alive = !!scheduler.alive;
  setConnection('online', '本机在线');
  runnerDot.className = 'runner-dot ' + (!alive ? 'idle' : paused ? 'paused' : 'on');
  runnerState.textContent = !alive ? '托管未启动' : paused ? '托管已暂停' : '托管运行中';
  const remain = Number(eta.remaining || 0);
  remaining.textContent = remain > 0 ? secsText(remain) : '';
  activity.textContent = !alive ? '调度器当前未运行'
    : queue.current ? `${queue.current}进行中`
      : queue.next ? `等待下一项：${queue.next}` : '等待下一项任务';
  detail.textContent = queue.next_reason || data?.last_line?.replace(/^\[[\d:]+\]\s*/, '') || '';
  setText(learnMin, today.learn_min);
  setText(workMin, today.work_min);
  setText(effPct, today.eff_pct == null ? null : `${today.eff_pct}%`);
  setText(todayTotal, today.total_min);
  setText(queuePending, queue.pending_count ?? 0);
  setText(uptime, scheduler.uptime || '—');
  setText(nextTask, queue.next ? `下一项 ${queue.next}` : '');
  toggleRunner.textContent = alive ? '停止托管' : '启动托管';
  toggleRunner.classList.toggle('running', alive);
  pauseRunner.textContent = paused ? '继续' : '暂停';
  setBusy(busy);
  place();
  document.body.dataset.ready = 'true';
};

const renderOffline = (message) => {
  setConnection('offline', '本机离线');
  runnerDot.className = 'runner-dot error';
  runnerState.textContent = '托管服务离线';
  remaining.textContent = '';
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

const refresh = async () => {
  try {
    const data = await call('panel_snapshot');
    paused = !!data?.paused;
    render(data?.data);
  } catch (error) {
    renderOffline(String(error));
  }
};

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

scrim.addEventListener('click', () => void call('panel_hide'));
panelCard.addEventListener('pointerdown', (event) => {
  // Keep clicks inside the card from behaving like a click on the scrim.
  event.stopPropagation();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') void call('panel_hide');
});

window.__qpetRefresh = refresh;
window.__qpetPlace = place;
window.addEventListener('resize', place);
void refresh();
window.addEventListener('qpet-panel-show', () => void refresh());
const timer = window.setInterval(() => {
  if (document.visibilityState === 'visible') void refresh();
}, 5000);
window.addEventListener('beforeunload', () => window.clearInterval(timer));
