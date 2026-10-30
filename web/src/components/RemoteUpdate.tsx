import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchRemoteUpdate,
  requestRemoteUpdate,
  type RemoteUpdateStatus,
} from '../api'

const ACTIVE = new Set(['checking', 'downloading', 'installing', 'restarting'])

/** 设置 → 版本与更新里的「电脑端更新」。
 *
 *  手机浏览器没有 Tauri IPC，更新动作必须由电脑端的桌面壳执行：这里只写
 *  `runs/update_request.json`，壳轮询执行后再把状态镜像回来
 *  （`runs/update_status.json`，读不到/过期 = 电脑端 App 没开）。 */
export function RemoteUpdate() {
  const [state, setState] = useState<{
    running: boolean
    status: RemoteUpdateStatus | null
  } | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const next = await fetchRemoteUpdate()
      if (alive.current) {
        setState({ running: !!next.running, status: next.status ?? null })
      }
    } catch {
      if (alive.current) setState({ running: false, status: null })
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

  const act = async (action: 'check' | 'install') => {
    if (busy) return
    if (action === 'install'
      && !window.confirm('更新会重启电脑上的 QQPet-Steward，确定继续？')) return
    setBusy(true)
    setNotice('')
    try {
      const result = await requestRemoteUpdate(action)
      setNotice(result.ok
        ? (action === 'install' ? '已开始更新，电脑端 App 会自动重启' : '已通知电脑端检查更新')
        : (result.msg || '请求失败'))
      await refresh()
    } catch (error) {
      setNotice(String(error))
    } finally {
      setBusy(false)
    }
  }

  const status = state?.status ?? null
  const running = state?.running === true
  const current = status?.current ? `v${status.current}` : ''
  const latest = status?.latest ? `v${status.latest}` : ''
  const percent = status && status.total && status.total > 0
    ? Math.min(100, Math.round(((status.done || 0) / status.total) * 100))
    : null

  let text = '读取中…'
  if (state) {
    if (!running) {
      text = '电脑端 App 未运行，无法远程更新'
    } else if (status?.phase === 'available') {
      text = status.installable
        ? `发现新版本 ${latest}`
        : `发现 ${latest}（当前是开发/源码模式，不能自动安装）`
    } else if (status?.phase === 'downloading') {
      text = percent == null ? '正在下载更新…' : `正在下载更新 ${percent}%`
    } else if (status?.phase === 'installing') {
      text = '正在安装更新…'
    } else if (status?.phase === 'restarting') {
      text = '正在重启电脑端 App…'
    } else if (status?.phase === 'checking') {
      text = '正在检查更新…'
    } else if (status?.phase === 'error') {
      text = `更新检查失败：${status.error || '未知错误'}`
    } else if (status?.phase === 'latest') {
      text = current ? `电脑端已是最新 ${current}` : '电脑端已是最新版本'
    } else {
      text = current ? `当前 ${current}` : '待检查'
    }
  }

  const active = ACTIVE.has(status?.phase ?? '')
  const showCheck = running && !active
  const showInstall = running && status?.phase === 'available' && status.installable

  return (
    <div className="set-update">
      <div className="set-update-main">
        <span className="set-update-label">电脑端更新</span>
        <span className={`set-update-state${status?.phase === 'error' ? ' err' : ''}`}>{text}</span>
        {notice ? <span className="set-update-note">{notice}</span> : null}
      </div>
      <div className="set-update-actions">
        {showInstall ? (
          <button className="set-primary" type="button" disabled={busy}
            onClick={() => void act('install')}>更新</button>
        ) : null}
        {showCheck ? (
          <button className="set-primary" type="button" disabled={busy}
            onClick={() => void act('check')}>
            {status?.phase === 'error' ? '重试' : '检查更新'}
          </button>
        ) : null}
      </div>
    </div>
  )
}
