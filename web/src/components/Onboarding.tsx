import { useEffect, useRef, useState } from 'react'
import {
  completeOnboarding,
  connectAdb,
  fetchAdb,
  fetchDeviceScreenshot,
  type AdbInfo,
  type Data,
} from '../api'
import { assetUrl, readConnection } from '../lib/connection'

const STEPS = ['连接设备', '确认主页', '选择任务', '完成准备']
const TASKS = [
  ['school', '学习', 'study_book.png'],
  ['work', '打工', 'work_coin.png'],
  ['adventure', '冒险', 'cap_compass.png'],
  ['care', '护理', 'soap.png'],
  ['gift_bag', '福袋', 'cap_coin.png'],
  ['visit', '踩踩', 'cap_paw.png'],
  ['pk', 'PK', 'pk_words.png'],
] as const
const DEFAULTS: Record<string, unknown> = {
  school_attribute: '力量',
  school_duration: '短课',
  work_location: '风铃旅社',
  work_duration: '45分钟',
  care_method: '一键护理',
  study_quota_hours: 8,
  work_quota_hours: 8,
  stop_total_hours: 12,
}
const HOURS = ['study_quota_hours', 'work_quota_hours', 'stop_total_hours']
const messageOf = (e: unknown) => e instanceof Error ? e.message : String(e)

function initialDraft(data: Data): Record<string, unknown> {
  const ed = data.editable || {}
  const enabled = data.config.tasks_enabled as Record<string, boolean> | undefined
  return {
    ...Object.fromEntries(Object.entries(DEFAULTS).map(([key, fallback]) => [key, ed[key] ?? fallback])),
    ...Object.fromEntries(TASKS.map(([key]) => [
      `${key}_enabled`, enabled?.[key] ?? ed[`${key}_enabled`] ?? true,
    ])),
  }
}

const deviceHint = (state: string): string => {
  if (state === 'device') return '已连接'
  if (state === 'unauthorized') return '请在手机上允许 USB 调试'
  if (state === 'offline') return '设备离线，请重新连接后检测'
  return `当前状态：${state}`
}

/** First-run preparation is separate from scheduler startup. */
export function Onboarding({
  data,
  onSave,
  onDone,
  onLater,
}: {
  data: Data
  onSave: (updates: Record<string, unknown>) => Promise<void>
  onDone: () => void
  onLater: () => void
}) {
  const [step, setStep] = useState(0)
  const [mode, setMode] = useState<'usb' | 'network'>('usb')
  const [adb, setAdb] = useState<AdbInfo | null>(null)
  const [serial, setSerial] = useState(String(data.editable?.adb_serial || ''))
  const [address, setAddress] = useState('')
  const [adbPath, setAdbPath] = useState(String(data.editable?.adb_path || ''))
  const [draft, setDraft] = useState(() => initialDraft(data))
  const [homeConfirmed, setHomeConfirmed] = useState(false)
  const [verifiedPath, setVerifiedPath] = useState<string | null>(null)
  const [shot, setShot] = useState<{ url: string; at: string } | null>(null)
  const [shotError, setShotError] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const requestId = useRef(0)
  const actionLock = useRef(false)
  const scroll = useRef<HTMLDivElement>(null)
  const feedback = useRef<HTMLDivElement>(null)
  const title = useRef<HTMLHeadingElement>(null)
  const alive = useRef(true)
  const latestData = useRef(data)
  latestData.current = data

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; requestId.current += 1 }
  }, [])
  useEffect(() => {
    scroll.current?.scrollTo(0, 0)
    title.current?.focus({ preventScroll: true })
  }, [step])
  useEffect(() => {
    if (error || notice) feedback.current?.scrollIntoView({ block: 'nearest' })
  }, [error, notice])
  useEffect(() => {
    if (!shot) return
    return () => URL.revokeObjectURL(shot.url)
  }, [shot])

  const patch = (key: string, value: unknown) => {
    setDraft((current) => ({ ...current, [key]: value }))
    setError('')
  }
  const validDevice = !!serial && adb?.devices.filter((d) => d.serial === serial).length === 1
    && adb.devices.some((d) => d.serial === serial && d.state === 'device')
  const running = data.scheduler.alive

  const inspect = async (preferred = serial): Promise<AdbInfo> => {
    const id = ++requestId.current
    const result = await fetchAdb()
    if (id !== requestId.current || !alive.current) throw new Error('设备配置已变更，请重新检测')
    setAdb(result)
    const online = result.devices.filter((d) => d.state === 'device')
    // Never replace an explicitly selected device with another online phone.
    if (!preferred && online.length === 1) setSerial(online[0]!.serial)
    else setSerial(preferred)
    return result
  }

  const act = async (kind: string, work: () => Promise<void>) => {
    if (actionLock.current) return
    actionLock.current = true
    setBusy(kind)
    setError('')
    setNotice('')
    try {
      await work()
    } catch (e) {
      if (alive.current) setError(messageOf(e))
    } finally {
      actionLock.current = false
      if (alive.current) setBusy('')
    }
  }

  const refresh = () => void act('inspect', async () => {
    const result = await inspect()
    if (result.error) setError(result.error)
    else if (!result.devices.length) setNotice('还没有检测到设备。完成上面的连接步骤后，再检测一次。')
  })
  const connect = () => void act('connect', async () => {
    const result = await connectAdb(address.trim())
    if (!result.ok) throw new Error(result.msg || '连接失败，请检查地址')
    await inspect(result.addr || address.trim())
    setHomeConfirmed(false)
    setShot(null)
    setNotice(result.msg || '已发送连接请求，请确认下方设备显示“已连接”。')
  })
  const readShot = () => void act('shot', async () => {
    setShotError('')
    const current = await inspect(serial)
    if (current.serial !== serial || current.path !== verifiedPath) {
      setHomeConfirmed(false)
      setShot(null)
      setStep(0)
      throw new Error('设备配置已经变更，请重新选择并保存设备')
    }
    try {
      const result = await fetchDeviceScreenshot()
      if (!alive.current) return
      setShot({ url: URL.createObjectURL(result.blob), at: result.at })
    } catch (e) {
      if (alive.current) setShotError(messageOf(e))
    }
  })
  const changeDevice = (next: string) => {
    requestId.current += 1
    setSerial(next)
    setHomeConfirmed(false)
    setVerifiedPath(null)
    setShot(null)
    setShotError('')
    setError('')
  }

  const next = () => void act('next', async () => {
    if (running) throw new Error('请先在首页停止托管，再进行设备准备')
    if (step === 0) {
      if (!validDevice) throw new Error('请先检测并选择一台已连接的设备')
      const result = await inspect(serial)
      if (result.error || result.devices.filter((d) => d.serial === serial).length !== 1
        || !result.devices.some((d) => d.serial === serial && d.state === 'device')) {
        throw new Error(result.error || '所选设备已离线，请重新连接并检测')
      }
      if (latestData.current.scheduler.alive) throw new Error('托管已经开始运行，请先回首页停止托管')
      if (serial !== result.serial) await onSave({ adb_serial: serial })
      setVerifiedPath(result.path)
    } else if (step === 1) {
      if (!homeConfirmed) throw new Error('请先确认设备上的 QQ 宠物已停留在自己的主页')
    } else if (step === 2) {
      if (!TASKS.some(([key]) => draft[`${key}_enabled`])) throw new Error('请至少选择一项托管任务')
      for (const key of HOURS) {
        const value = Number(draft[key])
        if (String(draft[key]).trim() === '' || !Number.isInteger(value) || value < 0 || value > 24) {
          throw new Error('学习、打工和合计停止时长请填写 0 到 24 之间的整数')
        }
      }
      const updates = Object.fromEntries(Object.entries(draft)
        .map(([key, value]) => [key, HOURS.includes(key) ? Number(value) : value])
        .filter(([key, value]) => {
          const field = String(key)
          const enabled = data.config.tasks_enabled as Record<string, boolean> | undefined
          const current = field.endsWith('_enabled')
            ? enabled?.[field.slice(0, -8)] ?? data.editable?.[field]
            : data.editable?.[field]
          return current !== value
        }))
      const order = Array.isArray(data.config.task_order)
        ? data.config.task_order.filter((key): key is string => typeof key === 'string') : []
      const missing = TASKS.map(([key]) => key).filter((key) => draft[`${key}_enabled`] && !order.includes(key))
      if (missing.length) updates.task_order = [...order, ...missing].join('>')
      if (Object.keys(updates).length) await onSave(updates)
    } else {
      const result = await inspect(serial)
      if (result.error || result.devices.filter((d) => d.serial === serial).length !== 1
        || !result.devices.some((d) => d.serial === serial && d.state === 'device')
        || result.serial !== serial || result.path !== verifiedPath) {
        setHomeConfirmed(false)
        setShot(null)
        setStep(0)
        throw new Error(result.error || '设备已断开或配置有变，请重新检测并保存设备')
      }
      if (latestData.current.scheduler.alive) throw new Error('托管已经开始运行，请先回首页停止托管')
      const completed = await completeOnboarding()
      if (!completed.ok) throw new Error('引导完成状态未保存，请重试')
      onDone()
      return
    }
    setStep((current) => current + 1)
  })

  const saveAdbPath = () => void act('path', async () => {
    await onSave({ adb_path: adbPath.trim() })
    setAdb(null)
    requestId.current += 1
    setHomeConfirmed(false)
    setVerifiedPath(null)
    setShot(null)
    setNotice('连接工具已保存，请重新检测设备。')
  })

  const chosen = adb?.devices.find((d) => d.serial === serial)
  const enabledNames = TASKS.filter(([key]) => draft[`${key}_enabled`]).map(([, label]) => label)
  const locations = Array.isArray(data.editable?.work_locations)
    ? data.editable.work_locations.filter((v): v is string => typeof v === 'string') : []
  const workLocations = [...new Set([String(draft.work_location), ...locations])]
  const primaryDisabled = !!busy || running || (step === 0 && !validDevice) || (step === 1 && !homeConfirmed)

  return (
    <section className="onboarding-page" data-page="onboarding" aria-label="新手引导">
      <header className="ob-head">
        <span className="ob-brand">
          <img src={assetUrl('/qp-icons/official/mood_smile.png')} alt="" />
          <span>QQ宠物托管<small>新手引导</small></span>
        </span>
        <button className="ob-link" type="button" onClick={onLater} disabled={!!busy}>稍后设置</button>
      </header>
      <nav className="ob-steps" aria-label="准备进度">
        {STEPS.map((label, index) => (
          <button key={label} type="button" className={index === step ? 'current' : index < step ? 'done' : ''}
            aria-current={index === step ? 'step' : undefined}
            disabled={index >= step || !!busy || running}
            onClick={() => { setError(''); setNotice(''); setStep(index) }}>
            <span>{index < step ? '✓' : index + 1}</span>{label}
          </button>
        ))}
      </nav>
      <div className="ob-scroll" ref={scroll}>
        <div className="ob-intro">
          <span className="ob-kicker">第 {step + 1} 步 / 共 4 步</span>
          <h1 ref={title} tabIndex={-1}>
            {['把设备连接到电脑', '让宠物待在自己的主页', '选好今天的托管任务', '准备好了，回家看看'][step]}
          </h1>
          <p>{[
            readConnection().server
              ? '这里检测的是托管电脑连接的 Android 设备。手机遥控端可协助完成设置。'
              : '连接运行 QQ 宠物的 Android 手机或模拟器，让这台电脑照顾它。',
            '在所选设备上登录 QQ，打开 QQ 宠物，停留在自己家的主页。',
            '按你的需要开启任务。之后随时可以在首页和设置里调整。',
            '设备和任务已保存。回到首页点击“启动”，宠物才会开始托管。',
          ][step]}</p>
        </div>

        {running ? (
          <div className="ob-callout" role="status">
            <b>托管正在运行</b>
            <p>请先回到首页停止托管，再进行设备准备。</p>
            <button className="ob-secondary" type="button" onClick={onLater}>返回仪表盘</button>
          </div>
        ) : step === 0 ? (
          <>
            <div className="ob-tabs" aria-label="设备连接方式">
              <button type="button" aria-pressed={mode === 'usb'} onClick={() => setMode('usb')}>USB 手机</button>
              <button type="button" aria-pressed={mode === 'network'} onClick={() => setMode('network')}>无线 / 模拟器</button>
            </div>
            <div className="ob-card">
              {mode === 'usb' ? (
                <ol className="ob-checklist">
                  <li>在手机的开发者选项中开启 <b>USB 调试</b>。</li>
                  <li>用可传输数据的 USB 线连接电脑，保持手机解锁。</li>
                  <li>在手机上点击 <b>允许 USB 调试</b>，然后检测设备。</li>
                </ol>
              ) : (
                <>
                  <p className="ob-small">先开启手机的无线调试，或启动模拟器并开启 ADB，再填入调试地址。</p>
                  <label className="ob-label" htmlFor="ob-address">设备地址</label>
                  <div className="ob-connect">
                    <input id="ob-address" value={address} onChange={(e) => setAddress(e.target.value)}
                      autoCapitalize="none" autoCorrect="off" spellCheck={false}
                      placeholder="192.168.1.20:5555 / 127.0.0.1:7555" disabled={!!busy}
                      onKeyDown={(e) => { if (e.key === 'Enter' && address.trim() && !busy) connect() }} />
                    <button className="ob-secondary" type="button" onClick={connect} disabled={!!busy || !address.trim()}>
                      {busy === 'connect' ? '连接中…' : '连接'}
                    </button>
                  </div>
                  <p className="ob-small ob-muted">手机无线调试需先完成与电脑的配对，再填写连接地址；模拟器的端口请以其设置为准。</p>
                </>
              )}
            </div>
            <div className="ob-card">
              <div className="ob-card-head">
                <h2>选择要托管的设备</h2>
                <button className="ob-secondary" type="button" onClick={refresh} disabled={!!busy}>
                  {busy === 'inspect' ? '检测中…' : adb ? '重新检测' : '检测设备'}
                </button>
              </div>
              {adb?.devices.length ? (
                <div className="ob-devices" role="radiogroup" aria-label="设备列表">
                  {adb.devices.map((device, index) => (
                    <label className={'ob-device' + (serial === device.serial ? ' selected' : '')} key={`${device.serial}:${index}`}>
                      <input type="radio" name="ob-device" checked={serial === device.serial}
                        disabled={!!busy || device.state !== 'device'}
                        onChange={() => changeDevice(device.serial)} />
                      <span><b>{device.model || 'Android 设备'}</b><code>{device.serial}</code></span>
                      <small className={device.state === 'device' ? 'ob-ok' : ''}>{deviceHint(device.state)}</small>
                    </label>
                  ))}
                </div>
              ) : (
                <div className="ob-empty">
                  <img src={assetUrl('/qp-icons/ctrl/phone.svg')} alt="" />
                  <span>{adb ? '还没有检测到设备' : '连接好以后，点一下“检测设备”'}</span>
                </div>
              )}
              {adb && serial && !validDevice ? <p className="ob-small ob-warning">所选设备暂不可用，请授权或重新连接后检测。</p> : null}
            </div>
            <details className="ob-help">
              <summary>检测不到？检查连接工具</summary>
              <p>完整桌面 App 已附带 ADB。源码运行时可安装 Android platform-tools，或手动填写可执行文件路径。</p>
              <label className="ob-label" htmlFor="ob-adb-path">ADB 路径</label>
              <input id="ob-adb-path" value={adbPath} disabled={!!busy}
                onChange={(e) => setAdbPath(e.target.value)} placeholder="留空自动查找" />
              <button className="ob-secondary" type="button" disabled={!!busy} onClick={saveAdbPath}>保存连接工具</button>
            </details>
          </>
        ) : step === 1 ? (
          <>
            <div className="ob-selected"><i />{chosen?.model || 'Android 设备'}<code>{serial}</code></div>
            <div className="ob-card">
              <ol className="ob-checklist">
                <li>在设备上打开 QQ，并登录要托管的账号。</li>
                <li>打开 QQ 宠物，回到 <b>自己的宠物主页</b>。</li>
                <li>保持游戏在前台，让电脑可以读取画面。</li>
              </ol>
            </div>
            <div className="ob-preview">
              {shot ? <img src={shot.url} alt="所选设备的当前画面" /> : (
                <div className="ob-preview-empty">
                  <img src={assetUrl('/qp-icons/ctrl/phone.svg')} alt="" />
                  <span>可以读取一张画面，确认选对了设备</span>
                </div>
              )}
              <button className="ob-secondary" type="button" onClick={readShot} disabled={!!busy}>
                {busy === 'shot' ? '读取中…' : shot ? '重新读取画面' : '读取设备画面（可选）'}
              </button>
              {shot?.at ? <small>{shot.at.includes('缓存') ? '这是缓存画面，请以设备当前页面为准' : `拍摄于 ${shot.at}`}</small> : null}
              {shotError ? <p className="ob-warning" role="alert">画面暂时不可用：{shotError}。也可以直接在设备上确认。</p> : null}
            </div>
            <label className="ob-confirm">
              <input type="checkbox" checked={homeConfirmed} onChange={(e) => setHomeConfirmed(e.target.checked)} disabled={!!busy} />
              <span>QQ 已登录，宠物停在自己的主页</span>
            </label>
          </>
        ) : step === 2 ? (
          <>
            <div className="ob-card ob-task-card">
              {TASKS.map(([key, label, image]) => (
                <label className="ob-task" key={key}>
                  <img src={assetUrl(`/qp-icons/official/${image}`)} alt="" />
                  <span><b>{label}</b><small>{
                    key === 'care' ? '补充体力和清洁'
                      : key === 'gift_bag' ? '领取自己和好友的福袋'
                        : key === 'visit' ? `每天 ${data.config.visit_per_day ?? 10} 次`
                          : key === 'pk' ? `每天 ${data.config.pk_per_day ?? 15} 次`
                            : key === 'adventure' ? `每天 ${data.config.adventure_times ?? 1} 次`
                              : key === 'school' ? '按科目和每日配额上课' : '按地点和每日配额打工'
                  }</small></span>
                  <input type="checkbox" checked={!!draft[`${key}_enabled`]} disabled={!!busy}
                    onChange={(e) => patch(`${key}_enabled`, e.target.checked)} />
                </label>
              ))}
            </div>
            <div className="ob-card ob-fields">
              <h2>先定一个日常节奏</h2>
              <div className="ob-field-grid">
                {[
                  ['study_quota_hours', '学习配额'],
                  ['work_quota_hours', '打工配额'],
                  ['stop_total_hours', '合计满则停止'],
                ].map(([key, label]) => (
                  <label key={key} className="ob-field">
                    <span>{label}</span>
                    <span className="ob-number"><input type="number" min="0" max="24" step="1"
                      value={String(draft[key!] ?? '')} disabled={!!busy}
                      onChange={(e) => patch(key!, e.target.value)} aria-label={label} /><small>小时</small></span>
                  </label>
                ))}
              </div>
              <p className="ob-small ob-muted">学习、打工共用合计时长。配额填 0 表示今天不做；合计停止填 0 表示不限。</p>
              <div className="ob-field-grid two">
                <label className="ob-field"><span>学习科目</span><select value={String(draft.school_attribute)}
                  disabled={!!busy} onChange={(e) => patch('school_attribute', e.target.value)}>
                  {['力量', '智力', '魅力', '夏令营'].map((value) => <option key={value}>{value}</option>)}
                </select></label>
                <label className="ob-field"><span>课时档位</span><select value={String(draft.school_duration)}
                  disabled={!!busy} onChange={(e) => patch('school_duration', e.target.value)}>
                  {['短课', '长课'].map((value) => <option key={value}>{value}</option>)}
                </select></label>
                <label className="ob-field"><span>打工地点</span><select value={String(draft.work_location)}
                  disabled={!!busy} onChange={(e) => patch('work_location', e.target.value)}>
                  {workLocations.map((value) => <option key={value}>{value}</option>)}
                </select></label>
                <label className="ob-field"><span>打工时长</span><select value={String(draft.work_duration)}
                  disabled={!!busy} onChange={(e) => patch('work_duration', e.target.value)}>
                  {['10分钟', '45分钟', '2小时'].map((value) => <option key={value}>{value}</option>)}
                </select></label>
              </div>
            </div>
            <div className="ob-card ob-fields">
              <label className="ob-field"><span>护理方式</span><select value={String(draft.care_method)} disabled={!!busy}
                onChange={(e) => patch('care_method', e.target.value)}>
                <option value="一键护理">一键护理</option>
                <option value="ocr检测">逐项护理（OCR 检测）</option>
              </select></label>
              <p className="ob-small ob-medal">想拿护理勋章，记得选“逐项护理”；一键护理不计入勋章进度。</p>
            </div>
          </>
        ) : (
          <>
            <div className="ob-ready-art">
              <img src={assetUrl('/qp-icons/official/mood_smile.png')} alt="" />
              <span>小屋准备就绪</span>
            </div>
            <div className="ob-card ob-review">
              <div><span>托管设备</span><b>{chosen?.model || 'Android 设备'}</b><code>{serial}</code></div>
              <div><span>游戏页面</span><b>已确认在自己的宠物主页</b></div>
              <div><span>开启任务</span><b>{enabledNames.join('、')}</b></div>
              <div><span>每日配额</span><b>学习 {String(draft.study_quota_hours)} 小时 · 打工 {String(draft.work_quota_hours)} 小时</b></div>
              <div><span>合计停止</span><b>{Number(draft.stop_total_hours) ? `${draft.stop_total_hours} 小时后停止学习和打工` : '不限时长'}</b></div>
            </div>
            <div className="ob-callout">
              <b>接下来怎么用</b>
              <p>首页右侧点“启动”开始托管，点“停止”结束。运行中可以查看日志和设备画面。</p>
              <p>电脑和设备保持连接，游戏保持在前台。任务和通知可以随时到设置里调整。</p>
            </div>
          </>
        )}
        <div ref={feedback}>
          {notice ? <p className="ob-notice" role="status">{notice}</p> : null}
          {error ? <p className="ob-error" role="alert">{error}</p> : null}
        </div>
      </div>
      <footer className="ob-footer">
        <div className="ob-footer-actions">
          {step > 0 ? <button type="button" className="ob-secondary" disabled={!!busy || running}
            onClick={() => { setError(''); setNotice(''); setStep((current) => current - 1) }}>上一步</button> : null}
          <button className="ob-primary" type="button" onClick={next} disabled={primaryDisabled}>
            {busy === 'next' ? '正在保存与检查…' : ['保存设备并继续', '确认主页，继续', '保存任务，继续', '完成引导，进入首页'][step]}
            {!busy ? <span aria-hidden="true">›</span> : null}
          </button>
        </div>
        <p>以后可在“设置 → 新手引导”重新打开</p>
      </footer>
    </section>
  )
}
