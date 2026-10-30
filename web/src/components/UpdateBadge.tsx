import { useCallback, useEffect, useRef, useState } from 'react'

/** Tauri updater status, mirrored from desktop/macos/src/main.rs. */
type UpdatePhase =
  | 'idle'
  | 'checking'
  | 'latest'
  | 'available'
  | 'downloading'
  | 'installing'
  | 'restarting'
  | 'error'

interface UpdateStatus {
  phase: UpdatePhase
  current: string
  latest: string
  notes: string
  url: string
  error: string
  lastChecked: number | null
  installable: boolean
  done: number
  total: number | null
}

type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>

/** The dashboard is served over localhost and also opened in phones' browsers.
 *  Only the Tauri webview has the internal IPC bridge, so the update entry is
 *  hidden everywhere else (and in older app builds whose ACL does not allow it). */
function tauriInvoke(): Invoke | null {
  const internals = (window as unknown as {
    __TAURI_INTERNALS__?: { invoke?: Invoke }
  }).__TAURI_INTERNALS__
  return typeof internals?.invoke === 'function'
    ? internals.invoke.bind(internals)
    : null
}

const ACTIVE = new Set<UpdatePhase>(['downloading', 'installing', 'restarting'])
// `checking` also needs polling: the menu-bar panel may already be checking,
// in which case panel_update_check returns the current `checking` snapshot
// immediately instead of waiting for the newer result.
const POLL = new Set<UpdatePhase>(['checking', ...ACTIVE])
const DENIED = /not allowed|not permitted|unknown command|command .* not found/i
/** While the pill is hidden (up to date), ask again this often. Rust still
 *  throttles real network checks to one every six hours, so this only decides
 *  how soon a release published meanwhile can surface. */
const IDLE_RECHECK_MS = 30 * 60 * 1000
/** A failed background check (app launched before Wi-Fi) retries on its own. */
const ERROR_RETRY_MS = 5 * 60 * 1000

const formatMb = (bytes: number) => `${(bytes / 1048576).toFixed(1)} MB`

const percentOf = (status: UpdateStatus): number | null => {
  const total = Number(status.total) || 0
  const done = Number(status.done) || 0
  return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null
}

/** Top-right update entry for the dashboard.
 *
 *  The button is intentionally mounted only on the overview page (App decides
 *  that) and only when `__TAURI_INTERNALS__` is present. It shares all state
 *  with the menu-bar panel: every action goes through the same Rust commands,
 *  so there is exactly one download / install in flight. */
export function UpdateBadge() {
  const invoke = useRef<Invoke | null>(null)
  const [supported, setSupported] = useState(true)
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const markDenied = useCallback((error: unknown): boolean => {
    if (!DENIED.test(String(error))) return false
    setSupported(false)
    return true
  }, [])

  /** `quiet` keeps background re-checks invisible: a pill that is already on
   *  screen must not blink away while a timer-driven check is in flight. */
  const check = useCallback(async (force: boolean, quiet = false) => {
    const call = invoke.current
    if (!call) return
    // panel_update_check returns the in-flight snapshot only to a *second*
    // caller; the call that starts the check stays pending for the whole
    // network round trip (two timeouts at worst). Reflect it locally so the
    // modal shows progress and the poll effect starts watching the result.
    if (!quiet) {
      setStatus((current) => current && ACTIVE.has(current.phase)
        ? current
        : current
          ? { ...current, phase: 'checking', error: '' }
          : {
              phase: 'checking', current: '', latest: '', notes: '', url: '',
              error: '', lastChecked: null, installable: false, done: 0, total: null,
            })
    }
    try {
      setStatus(await call('panel_update_check', { force }) as UpdateStatus)
    } catch (error) {
      if (markDenied(error)) return
      setStatus((current) => current
        ? { ...current, phase: 'error', error: String(error) }
        : {
            phase: 'error', current: '', latest: '', notes: '', url: '',
            error: String(error), lastChecked: null, installable: false,
            done: 0, total: null,
          })
    }
  }, [markDenied])

  useEffect(() => {
    invoke.current = tauriInvoke()
    if (!invoke.current) {
      setSupported(false)
      return
    }
    void check(true)
  }, [check])

  // While a download/install is running, keep the badge and modal in sync with
  // the Rust state. The menu-bar panel polls the same commands independently.
  useEffect(() => {
    if (!status || !POLL.has(status.phase)) return
    const timer = window.setInterval(() => {
      const call = invoke.current
      if (!call) return
      void call('panel_update_status')
        .then((next) => setStatus(next as UpdateStatus))
        .catch(() => { /* the app is restarting; the webview is about to go away */ })
    }, 700)
    return () => window.clearInterval(timer)
  }, [status?.phase])

  // The pill is hidden while up to date, so without a slow re-check a release
  // published while the app stays open would never surface on this page.
  // Only re-check while up to date: a failed check clears the pending update
  // in Rust, so it must never run while an update is already on offer.
  useEffect(() => {
    if (status?.phase !== 'latest' && status?.phase !== 'idle') return
    const timer = window.setInterval(() => void check(false, true), IDLE_RECHECK_MS)
    return () => window.clearInterval(timer)
  }, [status?.phase, check])

  // A check that fails in the background (for example the app auto-starts
  // before Wi-Fi is up) must not leave a permanent orange "更新失败" pill.
  // Quiet: the pill keeps its state until the retry actually resolves.
  useEffect(() => {
    if (status?.phase !== 'error') return
    const timer = window.setInterval(() => void check(true, true), ERROR_RETRY_MS)
    return () => window.clearInterval(timer)
  }, [status?.phase, check])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !ACTIVE.has(status?.phase ?? 'idle')) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, status?.phase])

  const phase = status?.phase ?? 'idle'
  const active = ACTIVE.has(phase)
  // magpie's rule: the Update pill is hidden while checking or up to date.
  // It appears only when a new version is available (or after the user has
  // started downloading it, so the same pill can show progress).
  const showPill = phase === 'available' || active || phase === 'error'

  // The drag strip injected by window-chrome.js sits above the page; keep its
  // reserved right edge exactly as wide as the pill (update.css reads
  // `--upd-pill-w`). A fixed reserve would make longer version labels
  // unclickable once the strip overlaps them.
  const pillRef = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    const pill = pillRef.current
    if (!pill) return
    const root = document.documentElement
    const sync = () => root.style.setProperty('--upd-pill-w', `${pill.offsetWidth}px`)
    sync()
    const observer = new ResizeObserver(sync)
    observer.observe(pill)
    return () => {
      observer.disconnect()
      root.style.removeProperty('--upd-pill-w')
    }
  }, [showPill])

  if (!supported || !invoke.current) return null

  const latest = status?.latest ? `v${status.latest}` : ''
  const current = status?.current ? `v${status.current}` : ''
  const percent = status ? percentOf(status) : null
  const showModal = open && !!status
  if (!showPill && !showModal) return null

  const label = phase === 'available' ? latest
    : phase === 'downloading' ? (percent == null ? '更新中…' : `更新 ${percent}%`)
      : phase === 'installing' ? '正在安装…'
        : phase === 'restarting' ? '正在重启…'
          : '更新失败'
  const hot = phase === 'available' || active || phase === 'error'

  const install = async () => {
    const call = invoke.current
    if (!call || busy) return
    setBusy(true)
    try {
      setStatus(await call('panel_update_install') as UpdateStatus)
    } catch (error) {
      if (!markDenied(error)) {
        setStatus((currentStatus) => currentStatus
          ? { ...currentStatus, phase: 'error', error: String(error) }
          : currentStatus)
      }
    } finally {
      setBusy(false)
    }
  }

  const openRelease = () => {
    void invoke.current?.('panel_update_open_release').catch(() => {})
  }

  const modalTitle = phase === 'available' ? `发现新版本 ${latest}`
    : phase === 'downloading' ? '正在下载更新'
      : phase === 'installing' ? '正在安装更新'
        : phase === 'restarting' ? '正在重启'
          : phase === 'error' ? '更新失败'
            : phase === 'checking' ? '正在检查更新'
              : '已是最新版本'

  return (
    <>
      {showPill ? (
        <button
          ref={pillRef}
          type="button"
          className={`upd-fab${hot ? ' hot' : ''}`}
          title={phase === 'available' ? `发现 ${latest}，点击查看更新日志`
            : phase === 'error' ? '更新检查失败，点击查看详情'
              : '更新进度'}
          aria-label={phase === 'available' ? `发现新版本 ${latest}`
            : phase === 'error' ? '更新检查失败，点击重试'
              : label}
          onClick={() => setOpen(true)}
        >
          <svg className="upd-ico" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            <path d="M8 2.2v6.6M5.4 6.4 8 9l2.6-2.6M3.4 11.4v1.1c0 .8.6 1.4 1.4 1.4h6.4c.8 0 1.4-.6 1.4-1.4v-1.1" />
          </svg>
          {label}
        </button>
      ) : null}

      {showModal ? (
        <div
          className="upd-mask"
          role="presentation"
          onClick={() => { if (!ACTIVE.has(phase)) setOpen(false) }}
        >
          <div
            className="upd-card"
            role="dialog"
            aria-modal="true"
            aria-label={modalTitle}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="upd-head">
              <div>
                <div className="upd-title">{modalTitle}</div>
                <div className="upd-sub">
                  {current ? `当前 ${current}` : 'QQPet-Steward'}
                  {phase === 'available' && latest ? ` · 新版本 ${latest}` : ''}
                </div>
              </div>
              {!ACTIVE.has(phase) ? (
                <button
                  type="button"
                  className="upd-close"
                  aria-label="关闭"
                  onClick={() => setOpen(false)}
                >×</button>
              ) : null}
            </div>

            {phase === 'checking' ? (
              <p className="upd-copy">正在连接更新源…</p>
            ) : active ? (
              <>
                <div className="upd-track">
                  <i
                    style={{ width: percent == null ? '34%' : `${percent}%` }}
                    className={percent == null && phase === 'downloading' ? 'indeterminate' : ''}
                  />
                </div>
                <p className="upd-copy">
                  {phase === 'downloading'
                    ? (percent == null
                      ? `下载中… ${formatMb(status.done)}`
                      : `下载中 ${percent}% · ${formatMb(status.done)} / ${formatMb(status.total ?? 0)}`)
                    : phase === 'installing'
                      ? '下载完成，正在安装…'
                      : '正在重启以完成更新…'}
                </p>
              </>
            ) : phase === 'available' ? (
              <>
                <div className="upd-notes">
                  {status.notes.trim() || '新版本已准备好，下载后会自动安装并重启。'}
                </div>
                {!status.installable ? (
                  <p className="upd-copy">当前是开发/源码模式，请打开发布页手动更新。</p>
                ) : null}
              </>
            ) : phase === 'error' ? (
              <p className="upd-copy upd-error">{status.error || '更新失败，请稍后重试。'}</p>
            ) : (
              <p className="upd-copy">当前 {current || '版本'} 已是最新，没有可下载的更新。</p>
            )}

            {!ACTIVE.has(phase) ? (
              <div className="upd-actions">
                <button type="button" className="upd-secondary" onClick={() => setOpen(false)}>
                  {phase === 'available' ? '稍后' : '关闭'}
                </button>
                {phase === 'available' && status.installable ? (
                  <button type="button" className="upd-primary" disabled={busy} onClick={() => void install()}>
                    {busy ? '准备中…' : '下载并安装'}
                  </button>
                ) : null}
                {phase === 'available' && !status.installable ? (
                  <button type="button" className="upd-primary" onClick={openRelease}>
                    打开发布页
                  </button>
                ) : null}
                {phase === 'error' || phase === 'latest' || phase === 'idle' ? (
                  <button type="button" className="upd-primary" onClick={() => void check(true)}>
                    重新检查
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  )
}
