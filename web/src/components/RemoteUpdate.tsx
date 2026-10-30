import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchRemoteUpdate,
  requestRemoteUpdate,
  type RemoteUpdateStatus,
} from '../api'

const ACTIVE = new Set(['checking', 'downloading', 'installing', 'restarting'])
/** 桌面壳最多 2 秒取一次请求、单次检查最长 20 秒；超过这个时间还没结果就
 *  先把转圈停下，避免 App 中途退出把按钮永久卡在「检查中」。 */
const CHECK_TIMEOUT_MS = 30_000

/** 设置 → 版本与更新里的「电脑端更新」。
 *
 *  手机浏览器没有 Tauri IPC，更新动作必须由电脑端的桌面壳执行：这里只写
 *  `runs/update_request.json`，壳轮询执行后再把状态镜像回来
 *  （`runs/update_status.json`，读不到/过期 = 电脑端 App 没开）。 */
export function RemoteUpdate({ fallbackVersion }: { fallbackVersion?: string }) {
  const [state, setState] = useState<{
    running: boolean
    status: RemoteUpdateStatus | null
  } | null>(null)
  const [busy, setBusy] = useState(false)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const [offline, setOffline] = useState(false)
  const alive = useRef(true)
  /** 点击检查时状态里的 lastChecked；它一变说明这次检查已经有结果了。 */
  const checkBaseline = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const next = await fetchRemoteUpdate()
      if (alive.current) {
        setState({ running: !!next.running, status: next.status ?? null })
        setOffline(false)
        // 桥恢复了（App 重新运行）：上一次点击失败的提示已经过时
        if (next.running) setError('')
      }
    } catch {
      // 单次轮询失败（手机切网、仪表盘重启）不能把上一份状态冲成「App 未运行」，
      // 否则检查中的转圈会被误清、按钮闪回来。保留上一份数据，只标记不可达。
      if (alive.current) setOffline(true)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    void refresh()
    return () => { alive.current = false }
  }, [refresh])

  useEffect(() => {
    // 本地文件读取，很便宜；3 秒一轮才能让「检查更新」点下去马上有反馈
    // （请求由桌面壳每 2 秒取一次）。离开设置页组件就卸载，不会常驻轮询。
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => window.clearInterval(timer)
  }, [refresh])

  // 壳拿到请求后才会把 phase 置成 checking；在那之前本地先转圈，
  // 等 lastChecked 变成新值（检查真正结束）再恢复。
  useEffect(() => {
    if (!checking) return
    if (state && !state.running) {
      setChecking(false)
      return
    }
    const status = state?.status
    if (status && status.phase !== 'checking'
      && status.lastChecked !== checkBaseline.current) {
      setChecking(false)
    }
  }, [checking, state])

  useEffect(() => {
    if (!checking) return
    const timer = window.setTimeout(() => setChecking(false), CHECK_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [checking])

  const act = async (action: 'check' | 'install') => {
    if (busy) return
    if (action === 'install'
      && !window.confirm('更新会重启电脑上的 QQPet-Steward，确定继续？')) return
    setBusy(true)
    setError('')
    if (action === 'check') {
      checkBaseline.current = state?.status?.lastChecked ?? null
      setChecking(true)
    }
    try {
      const result = await requestRemoteUpdate(action)
      if (!result.ok) {
        setError(result.msg || '请求失败')
        if (action === 'check') setChecking(false)
      }
      await refresh()
    } catch (cause) {
      // 只显示服务器给的说明，不把 ConnectionError/TypeError 这类类名丢到界面上
      setError(cause instanceof Error ? cause.message : String(cause))
      if (action === 'check') setChecking(false)
    } finally {
      setBusy(false)
    }
  }

  const status = state?.status ?? null
  const running = state?.running === true
  const phase = status?.phase ?? ''
  const checkingNow = running && (checking || phase === 'checking')
  const current = status?.current ? `v${status.current}` : ''
  const latest = status?.latest ? `v${status.latest}` : ''
  const percent = status && status.total && status.total > 0
    ? Math.min(100, Math.round(((status.done || 0) / status.total) * 100))
    : null

  let text = offline ? '无法连接托管服务，重试中…' : '读取中…'
  if (state) {
    if (!running) {
      text = '电脑端 App 未运行，无法远程更新'
    } else if (checkingNow) {
      text = '检查中…'
    } else if (phase === 'available') {
      text = status?.installable
        ? `发现新版本 ${latest}`
        : `发现 ${latest}（当前是开发/源码模式，不能自动安装）`
    } else if (phase === 'downloading') {
      text = percent == null ? '正在下载更新…' : `正在下载更新 ${percent}%`
    } else if (phase === 'installing') {
      text = '正在安装更新…'
    } else if (phase === 'restarting') {
      text = '正在重启电脑端 App…'
    } else if (phase === 'error') {
      text = `更新检查失败：${status?.error || '未知错误'}`
    } else if (phase === 'latest') {
      text = current ? `电脑端已是最新 ${current}` : '电脑端已是最新版本'
    } else {
      text = current ? `当前 ${current}` : '待检查'
    }
  }

  const active = ACTIVE.has(phase)
  // available 且可自动安装时才只给「更新」；available 但 installable=false
  // （开发/源码或 transient 模式）必须保留「检查更新」，否则整张卡片没有按钮。
  const canInstall = phase === 'available' && status?.installable === true
  const showCheck = running && !checkingNow && !active && !canInstall
  const showInstall = running && !checkingNow && canInstall
  const hasActions = showInstall || checkingNow || showCheck

  // 版本号必须显示「正在运行的 App」的版本：源码模式下仪表盘读的是仓库里的
  // tauri.conf.json（可能已经是下一版），不能代表桌面壳实际运行在哪个版本。
  // 只有 App 没在运行（桥状态缺失/过期）时才退回仪表盘自己的版本。
  const shownVersion = running && status?.current
    ? `v${status.current}`
    : (fallbackVersion ? `v${fallbackVersion}` : '版本未知')

  return (
    <>
      <div className="set-version">
        <span>QQPet-Steward</span>
        <b>{shownVersion}</b>
      </div>
      <div className="set-update">
        <div className="set-update-main">
          <span className="set-update-label">电脑端更新</span>
          <span className={`set-update-state${phase === 'error' ? ' err' : ''}`}>{text}</span>
          {phase === 'downloading' && percent != null ? (
            <div className="set-update-bar" aria-hidden="true">
              <i style={{ width: `${percent}%` }} />
            </div>
          ) : null}
          {error && running ? <span className="set-update-error">{error}</span> : null}
        </div>
        {hasActions ? (
          <div className="set-update-actions">
            {showInstall ? (
              <button className="set-primary" type="button" disabled={busy}
                onClick={() => void act('install')}>更新</button>
            ) : null}
            {checkingNow ? (
              <button className="set-primary" type="button" disabled>
                <span className="set-update-spin" aria-hidden="true" />
                检查更新
              </button>
            ) : null}
            {showCheck ? (
              <button className="set-primary" type="button" disabled={busy}
                onClick={() => void act('check')}>
                {phase === 'error' ? '重试' : '检查更新'}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </>
  )
}
