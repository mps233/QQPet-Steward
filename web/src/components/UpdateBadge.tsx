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
const DENIED = /not allowed|not permitted|unknown command|command .* not found/i

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

  const check = useCallback(async (force: boolean) => {
    const call = invoke.current
    if (!call) return
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
    if (!status || !ACTIVE.has(status.phase)) return
    const timer = window.setInterval(() => {
      const call = invoke.current
      if (!call) return
      void call('panel_update_status')
        .then((next) => setStatus(next as UpdateStatus))
        .catch(() => { /* the app is restarting; the webview is about to go away */ })
    }, 700)
    return () => window.clearInterval(timer)
  }, [status?.phase])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !ACTIVE.has(status?.phase ?? 'idle')) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, status?.phase])

  if (!supported || !invoke.current) return null

  const phase = status?.phase ?? 'idle'
  const latest = status?.latest ? `v${status.latest}` : ''
  const current = status?.current ? `v${status.current}` : ''
  const percent = status ? percentOf(status) : null
  const active = ACTIVE.has(phase)

  const label = phase === 'available' ? `升级 ${latest}`
    : phase === 'downloading' ? (percent == null ? '更新中…' : `更新 ${percent}%`)
      : phase === 'installing' ? '正在安装…'
        : phase === 'restarting' ? '正在重启…'
          : phase === 'checking' ? '检查中…'
            : phase === 'error' ? '检查更新'
              : '检查更新'
  const hot = phase === 'available' || active

  const openModal = () => {
    setOpen(true)
    if (phase === 'latest' || phase === 'idle' || phase === 'error') void check(true)
  }

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
      <button
        type="button"
        className={`upd-fab${hot ? ' hot' : ''}`}
        title={phase === 'available' ? `发现 ${latest}，点击查看更新日志` : '检查更新'}
        aria-label={phase === 'available' ? `发现新版本 ${latest}` : '检查更新'}
        onClick={openModal}
      >
        <span className="upd-spark" aria-hidden="true">↥</span>
        {label}
      </button>

      {open && status ? (
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
