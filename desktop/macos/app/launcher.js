/* Native startup owns the local backend and opens the main window.
   This page only shows progress and lets the user retry a failed startup. */

const invoke = window.__TAURI_INTERNALS__?.invoke;
const statusCard = document.querySelector('.status-card');
const statusTitle = document.querySelector('#status-title');
const statusDetail = document.querySelector('#status-detail');
const retryButton = document.querySelector('#retry');
let startupRevision = 0;
let startupError = '';

function setStatus(phase, message) {
  const titles = {
    starting: '正在启动 QQ 宠物托管',
    ready: '正在打开主界面',
    error: '启动遇到问题',
  };
  const details = {
    starting: '正在准备后台服务，完成后将自动打开主界面。',
    ready: '后台已就绪，正在打开主界面。',
    error: '启动失败，请重试。',
  };
  statusCard.dataset.state = phase;
  statusTitle.textContent = titles[phase];
  statusDetail.textContent = message || details[phase];
  retryButton.disabled = phase !== 'error';
}

function showStartupError(message) {
  const detail = String(message || '').trim() || '启动失败，请重试。';
  // Native failures invalidate status reads or retries that are still in flight.
  startupRevision += 1;
  startupError = detail;
  window.__qpetConnectionError = detail;
  setStatus('error', detail);
}

window.addEventListener('qpet-connection-error', (event) => {
  showStartupError(event.detail || window.__qpetConnectionError);
});
if (window.__qpetConnectionError) {
  showStartupError(window.__qpetConnectionError);
}

async function retryStartup() {
  if (retryButton.disabled) return;
  startupError = '';
  delete window.__qpetConnectionError;
  const revision = ++startupRevision;
  setStatus('starting', '正在重新启动，请稍候。');
  try {
    if (!invoke) throw new Error('请通过 macOS 应用打开 QQ 宠物托管。');
    await invoke('retry_startup');
    if (revision !== startupRevision) return;
    setStatus('ready');
  } catch (error) {
    if (revision === startupRevision) showStartupError(error);
  }
}

async function boot() {
  const revision = startupRevision;
  if (!startupError) setStatus('starting');
  try {
    if (!invoke) throw new Error('请通过 macOS 应用打开 QQ 宠物托管。');
    const status = await invoke('get_startup_status');
    if (revision !== startupRevision || startupError) return;
    if (status?.phase === 'error') {
      showStartupError(status.message);
    } else if (status?.phase === 'starting' || status?.phase === 'ready') {
      setStatus(status.phase, status.message);
    } else {
      showStartupError('无法读取应用启动状态，请重试。');
    }
  } catch (error) {
    if (revision === startupRevision && !startupError) showStartupError(error);
  }
}

retryButton.addEventListener('click', retryStartup);
void boot();
