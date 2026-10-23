import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  completePairing,
  ConnectionError,
  fetchClientMeta,
  readConnection,
  refreshClientSession,
  saveServer,
  clearDeviceToken,
  probeClientMeta,
  type ClientCapabilities,
} from '../api'
import { parsePairLink } from '../lib/connection'
import { usePoll } from '../lib/usePoll'

type Step = 'choose' | 'manual' | 'pairing' | 'connecting'

type Props = {
  /** Re-render the app after the server/token pair is stored. */
  onConnected: () => void
}

/**
 * First-run gate for the Android remote-control build.
 *
 * Same-origin browser users never see it: an empty server URL with a valid
 * cookie continues to work exactly as before. A remote user can paste a
 * pairing link or scan its QR code; the ticket is exchanged for a device token
 * before the dashboard is rendered.
 */
export function ConnectionGate({ onConnected }: Props) {
  const [initial] = useState(() => readConnection())
  const [step, setStep] = useState<Step>(initial.server ? 'connecting' : 'choose')
  const [server, setServer] = useState(initial.server)
  const [link, setLink] = useState('')
  const [name, setName] = useState('我的手机')
  const [error, setError] = useState('')
  const [meta, setMeta] = useState<ClientCapabilities | null>(null)
  const [pairServer, setPairServer] = useState('')
  const [ticket, setTicket] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [hasToken, setHasToken] = useState(!!initial.token)

  // Validate a previously paired remote connection before showing the
  // dashboard. A 401 means the device was revoked, not "network down".
  useEffect(() => {
    if (!initial.server || !hasToken) {
      if (initial.server) setStep('pairing')
      return
    }
    let alive = true
    ;(async () => {
      try {
        const m = await fetchClientMeta()
        if (!alive) return
        setMeta(m)
        await refreshClientSession()
        if (!alive) return
        onConnected()
      } catch (e) {
        if (!alive) return
        if (e instanceof ConnectionError && e.status === 401) {
          clearDeviceToken()
          setHasToken(false)
          setError('这台设备已被撤销配对，请重新连接。')
        } else {
          setError('托管电脑暂时连不上；确认电脑在线、仪表盘已启动，然后重试。')
        }
        setStep('connecting')
      }
    })()
    return () => { alive = false }
    // Re-run on explicit retry. A stored server without a token is already
    // handled above by switching to the pairing form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, hasToken])

  const connectLink = useCallback(async (raw: string) => {
    setError('')
    try {
      const parsed = parsePairLink(raw)
      if (parsed.expires && parsed.expires * 1000 < Date.now()) {
        throw new Error('这个配对链接已经过期，请在电脑上重新生成')
      }
      const candidate = saveServer(parsed.server)
      setServer(candidate.server)
      setPairServer(parsed.server)
      setTicket(parsed.ticket)
      setMeta(await fetchClientMeta())
      setStep('pairing')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // Deep-link handoff from the native shell / QR scanner:
  // qpet://pair?v=1&base=...&ticket=...
  useEffect(() => {
    const raw = location.hash.replace(/^#/, '')
    if (!raw) return
    const params = new URLSearchParams(raw)
    const base = params.get('base') || params.get('server')
    const ticket = params.get('ticket')
    if (!base || !ticket) return
    const link = `qpet://pair?${new URLSearchParams({
      v: params.get('v') || '1',
      base,
      ticket,
      ...(params.get('expires') ? { expires: params.get('expires') as string } : {}),
    })}`
    void connectLink(link)
  }, [connectLink])

  const connectManual = async (e?: FormEvent) => {
    e?.preventDefault()
    setError('')
    try {
      const candidate = saveServer(server)
      setServer(candidate.server)
      setMeta(await probeClientMeta())
      setStep('pairing')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const finishPair = async () => {
    setError('')
    setStep('connecting')
    try {
      await completePairing(ticket, pairServer, name.trim() || '我的手机')
      try {
        history.replaceState(null, '', location.pathname + location.search)
      } catch {
        /* embedding webview with history restrictions */
      }
      setHasToken(true)
      onConnected()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setStep('pairing')
    }
  }

  const reset = () => {
    setError('')
    setStep(hasToken ? 'connecting' : 'choose')
    setMeta(null)
  }

  return (
    <div className="connpage">
      <section className="conncard">
        <header className="connhead">
          <span className="connbadge">手机遥控</span>
          <h1>连接托管电脑</h1>
          <p>Android 只负责遥控。学习、打工和 OCR 仍在 Windows / Mac 上运行。</p>
        </header>

        {step === 'choose' ? (
          <>
            <button type="button" className="connchoice" onClick={() => setStep('pairing')}>
              <b>扫码配对</b>
              <span>在电脑的「设置 → 手机遥控」生成二维码，再用手机扫码。</span>
              <em>推荐</em>
            </button>
            <button type="button" className="connchoice" onClick={() => setStep('manual')}>
              <b>手动输入地址</b>
              <span>电脑和手机在同一 Wi-Fi，或电脑能从私有网络访问。</span>
            </button>
            <details className="connhelp">
              <summary>第一次使用？</summary>
              <p>先在这台电脑启动仪表盘，再打开「设置 → 手机遥控」，生成一次性配对码。配对码 5 分钟内有效。</p>
            </details>
          </>
        ) : null}

        {step === 'manual' ? (
          <form className="connform" onSubmit={(e) => void connectManual(e)}>
            <label htmlFor="conn-server">托管电脑地址</label>
            <input
              id="conn-server"
              type="url"
              inputMode="url"
              autoCapitalize="none"
              autoCorrect="off"
              placeholder="http://192.168.1.20:8787"
              value={server}
              onChange={(e) => setServer(e.target.value)}
            />
            <button className="connprimary" type="submit" disabled={!server.trim()}>
              继续连接
            </button>
            <button className="connlink" type="button" onClick={reset}>返回</button>
          </form>
        ) : null}

        {step === 'pairing' ? (
          <div className="connform">
            <label htmlFor="conn-link">配对链接或配对码</label>
            <textarea
              id="conn-link"
              rows={3}
              autoCapitalize="none"
              autoCorrect="off"
              placeholder="粘贴电脑上显示的配对链接"
              value={link}
              onChange={(e) => setLink(e.target.value)}
            />
            <button
              type="button"
              className="connprimary"
              disabled={!link.trim()}
              onClick={() => void connectLink(link)}
            >
              解析配对链接
            </button>
            {ticket ? (
              <>
                <div className="connserver">
                  <span>服务器</span>
                  <b>{pairServer}</b>
                  {meta ? <small>协议 v{meta.protocol_version} · {meta.server.name}</small> : null}
                </div>
                <label htmlFor="conn-name">设备名称</label>
                <input
                  id="conn-name"
                  value={name}
                  maxLength={64}
                  onChange={(e) => setName(e.target.value)}
                />
                <button type="button" className="connprimary" onClick={() => void finishPair()}>
                  完成配对
                </button>
              </>
            ) : null}
            <button className="connlink" type="button" onClick={reset}>返回</button>
          </div>
        ) : null}

        {step === 'connecting' ? (
          <div className="connwait">
            <span className="connspinner" aria-hidden="true" />
            <b>正在连接托管电脑</b>
            <span>{server || '尚未设置服务器地址'}</span>
            <div className="connwaitactions">
              <button type="button" className="connprimary" onClick={() => {
                if (server) setAttempt((n) => n + 1)
                else setStep('choose')
              }}>重试</button>
              <button type="button" className="connlink" onClick={reset}>更换服务器</button>
            </div>
          </div>
        ) : null}

        {meta?.pairing.available && step === 'pairing' && !ticket ? (
          <div className="connhint">这台服务器支持一次性配对码。</div>
        ) : null}
        {error ? <div className="connerror" role="alert">{error}</div> : null}
      </section>
    </div>
  )
}

/** Same-origin access-code form used after the public shell loads. */
export function AccessKeyGate({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (e?: FormEvent) => {
    e?.preventDefault()
    if (!key.trim()) return
    setBusy(true)
    setError('')
    try {
      const r = await fetch('/api/auth', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key.trim() }),
      })
      const body = await r.json().catch(() => null) as { msg?: string } | null
      if (!r.ok) throw new Error(body?.msg || `验证失败 HTTP ${r.status}`)
      onAuthenticated()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="connpage">
      <section className="conncard">
        <header className="connhead">
          <span className="connbadge">访问验证</span>
          <h1>打开仪表盘</h1>
          <p>输入托管电脑上保存的访问码。验证后这台浏览器会自动记住。</p>
        </header>
        <form className="connform" onSubmit={(e) => void submit(e)}>
          <label htmlFor="access-key">仪表盘访问码</label>
          <input
            id="access-key"
            type="password"
            autoComplete="current-password"
            value={key}
            autoFocus
            placeholder="runs/dashboard_access.token"
            onChange={(e) => setKey(e.target.value)}
          />
          <button className="connprimary" type="submit" disabled={busy || !key.trim()}>
            {busy ? '验证中…' : '验证并进入'}
          </button>
        </form>
        {error ? <div className="connerror" role="alert">{error}</div> : null}
      </section>
    </div>
  )
}

/** Remote-managed local settings. These never modify the host dashboard config. */
export function ConnectionSettings({ onDone }: { onDone: () => void }) {
  const initial = readConnection()
  const [server, setServer] = useState(initial.server)
  const [error, setError] = useState('')
  const save = () => {
    try {
      saveServer(server)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }
  return (
    <div className="connpage">
      <section className="conncard">
        <header className="connhead">
          <span className="connbadge">连接设置</span>
          <h1>托管服务器</h1>
          <p>只保存手机上的服务器地址；设置修改仍需通过仪表盘的「设置」页面。</p>
        </header>
        <div className="connform">
          <label htmlFor="conn-server">服务器地址</label>
          <input
            id="conn-server"
            type="url"
            value={server}
            inputMode="url"
            autoCapitalize="none"
            placeholder="http://192.168.1.20:8787"
            onChange={(e) => setServer(e.target.value)}
          />
          <button className="connprimary" type="button" onClick={save}>保存地址</button>
          <button className="connlink" type="button" onClick={onDone}>返回</button>
        </div>
        {error ? <div className="connerror" role="alert">{error}</div> : null}
      </section>
    </div>
  )
}

/** Remote client connection panel used by Settings → 手机遥控 on a phone. */
export function RemoteSettingsPanel() {
  const initial = readConnection()
  const [server, setServer] = useState(initial.server)
  const [deviceId, setDeviceId] = useState(initial.deviceId)
  const [error, setError] = useState('')
  const connected = !!initial.token
  const disconnect = () => {
    clearDeviceToken()
    setDeviceId('')
  }
  const save = () => {
    try {
      saveServer(server)
      window.location.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }
  return (
    <div className="set-page">
      <section className="set-sec">
        <h3>当前连接</h3>
        <div className="set-card">
          <div className="set-row">
            <div className="set-row-main">
              <span className="set-row-label">托管服务器</span>
              <span className="set-row-desc">{server || '尚未连接'}</span>
            </div>
            <span className={'set-chip' + (connected ? ' ok' : '')}>
              {connected ? '已配对' : '未配对'}
            </span>
          </div>
          {deviceId ? (
            <div className="set-row">
              <div className="set-row-main">
                <span className="set-row-label">设备</span>
                <span className="set-row-desc">{deviceId}</span>
              </div>
            </div>
          ) : null}
        </div>
      </section>
      <section className="set-sec">
        <h3>服务器</h3>
        <div className="set-card">
          <div className="set-field">
          <label htmlFor="remote-server">服务器地址</label>
          <input
            className="set-input"
            id="remote-server"
            type="url"
            value={server}
            onChange={(e) => setServer(e.target.value)}
          />
            <button className="set-primary" type="button" onClick={save}>保存地址</button>
          </div>
        </div>
      </section>
      {connected ? (
        <section className="set-sec">
          <h3>配对</h3>
          <div className="set-card">
            <div className="set-note">
              <b>说明</b>
              <span>断开只会清除这台手机的设备令牌；电脑上的调度器不会停止。</span>
            </div>
            <button className="set-primary set-danger" type="button" onClick={disconnect}>
              断开这台设备
            </button>
          </div>
        </section>
      ) : null}
      {error ? <div className="connerror" role="alert">{error}</div> : null}
    </div>
  )
}

/** Render the QR returned by the dashboard's authenticated pairing endpoint. */
export function PairingPreview({ payload, qrUrl }: { payload: string; qrUrl?: string }) {
  const [copied, setCopied] = useState('')
  return (
    <div className="pairpreview">
      {qrUrl ? (
        <img className="conn-qr-img" src={qrUrl} alt="配对二维码" />
      ) : (
        <div className="connerror">二维码暂时不可用，请复制下面的配对链接。</div>
      )}
      <textarea
        className="paircopy"
        readOnly
        value={payload}
        aria-label="配对链接"
        onFocus={(e) => e.currentTarget.select()}
      />
      <button
        type="button"
        className="connprimary"
        onClick={() => {
          navigator.clipboard?.writeText(payload).then(
            () => setCopied('已复制配对链接'),
            () => setCopied('复制失败，请手动选中上面的文字'),
          )
        }}
      >
        复制配对链接
      </button>
      {copied ? <p className="set-msg sub">{copied}</p> : null}
    </div>
  )
}

/**
 * Poll the paired server in the background. A connection failure must not
 * blank the dashboard: the last data remains visible and the chip turns amber.
 */
export function useConnectionState() {
  const c = readConnection()
  const remote = !!c.server || !!c.token
  const { error, data } = usePoll(
    async () => remote ? fetchClientMeta() : null,
    15000,
  )
  return {
    remote,
    connected: !remote || (!!data && !error),
    label: !remote
      ? '本机'
      : error
        ? '托管离线'
        : data
          ? '托管在线'
          : '连接中',
  }
}
