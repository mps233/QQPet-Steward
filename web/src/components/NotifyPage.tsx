import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchNotifyHistory, testNotify } from '../api'
import type { NotificationRecord } from '../api'
import { Switch } from './form'

/** `editable` 是个松散的 Record<string, unknown>，取值统一走这两个小工具 */
const boolOf = (ed: Record<string, unknown>, k: string): boolean => !!ed[k]

function CredentialInput({ label, field, configured, secret = true, placeholder, hint, onSave }: {
  label: string
  field: string
  configured: boolean
  secret?: boolean
  placeholder: string
  hint?: string
  onSave: (updates: Record<string, unknown>) => Promise<void>
}) {
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const save = async (value: string) => {
    setSaving(true)
    try {
      await onSave({ [field]: value })
      setDraft('')
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className="ntf-field">
      <div className="ntf-field-head">
        <label className="ntf-label" htmlFor={`ntf-${field}`}>{label}</label>
        <span className={'ntf-chip' + (configured ? ' ok' : '')}>
          {configured ? '已配置' : '未配置'}
        </span>
      </div>
      <input
        id={`ntf-${field}`}
        className="ntf-input"
        type={secret ? 'password' : 'text'}
        value={draft}
        disabled={saving}
        autoComplete="off"
        aria-label={label}
        placeholder={configured ? '留空保持不变，输入新值替换' : placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { if (draft.trim()) void save(draft).catch(() => undefined) }}
        onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
      />
      <div className="ntf-field-foot">
        {hint ? <p className="ntf-hint">{hint}</p> : <span />}
        {configured && (
          <button className="ntf-clear" type="button" disabled={saving}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => void save('').catch(() => undefined)}>清除凭据</button>
        )}
      </div>
    </div>
  )
}

function ToggleRow({ label, desc, on, disabled, onToggle }: {
  label: string
  desc: string
  on: boolean
  disabled: boolean
  onToggle: () => void
}) {
  return (
    <div className="ntf-row">
      <div className="ntf-row-main">
        <span className="ntf-row-label">{label}</span>
        <span className="ntf-row-desc">{desc}</span>
      </div>
      <Switch on={on} disabled={disabled} onToggle={onToggle} title={desc} />
    </div>
  )
}

const isOffResult = (value: string): boolean => /已关闭|未配置|跳过/.test(value)
const isFailResult = (value: string): boolean => /失败|未送达|错误/.test(value)

function historyTitle(record: NotificationRecord): string {
  const first = (record.content || '').split('\n').find((line) => line.trim()) || ''
  const raw = (record.title || '').trim() || first
  return raw.replace(/<[^>]*>/g, '').replace(/^[^\p{L}\p{N}\[]+/u, '').trim() || '通知'
}

/** 正文首行就是标题时（日报都是这样）不再重复渲染一遍 */
function historyBody(record: NotificationRecord): string {
  const lines = (record.content || '').split('\n')
  const title = historyTitle(record)
  const index = lines.findIndex((line) => line.trim())
  if (index >= 0) {
    const first = (lines[index] ?? '').replace(/<[^>]*>/g, '').replace(/^[^\p{L}\p{N}\[]+/u, '').trim()
    if (first === title) {
      lines.splice(index, 1)
      while (true) {
        const next = lines[index]
        if (next === undefined || next.trim()) break
        lines.splice(index, 1)
      }
    }
  }
  return lines.join('\n').trim()
}

/** 历史记录的短分类：只用于左侧色标，不影响正文 */
function historyKind(record: NotificationRecord): { label: string; cls: string } {
  const text = `${record.title || ''} ${(record.content || '').slice(0, 160)}`
  if (/冒险日报/.test(text)) return { label: '冒险', cls: 'adv' }
  if (/打工学习日报|收工总账/.test(text)) return { label: '日报', cls: 'daily' }
  if (/告警|失败|异常|错误/.test(text)) return { label: '告警', cls: 'alert' }
  if (/测试/.test(text)) return { label: '测试', cls: 'test' }
  return { label: '通知', cls: '' }
}

/** 通知页：飞书 / Telegram / 事件开关 / 异常提醒 + 配置说明。
 *  开关点一下**立即保存**（与设置页同一套「改动即自动保存」），文本框失焦时保存。
 *  历史记录默认只显示一行摘要，点开才渲染正文 —— 20 条完整日报铺开会把页面拉到 1 万像素。 */
export function NotifyPage({
  editable,
  onSave,
}: {
  editable: Record<string, unknown>
  onSave: (updates: Record<string, unknown>) => Promise<void>
}) {
  // 本地即时态：保存是异步的，先乐观更新避免开关"点一下弹回去"
  const [optimistic, setOptimistic] = useState<Record<string, boolean>>({})
  const [testMsg, setTestMsg] = useState('')
  const [saveError, setSaveError] = useState('')
  const [savedAt, setSavedAt] = useState('')
  const [testing, setTesting] = useState(false)
  const [pendingKeys, setPendingKeys] = useState<string[]>([])
  const [openRecord, setOpenRecord] = useState('')
  const locks = useRef(new Set<string>())
  const pendingSaves = useRef(new Set<Promise<void>>())
  const [history, setHistory] = useState<NotificationRecord[]>([])
  const refreshHistory = useCallback(() => {
    void fetchNotifyHistory().then((r) => setHistory(r.records)).catch(() => undefined)
  }, [])

  useEffect(() => {
    refreshHistory()
    const timer = window.setInterval(refreshHistory, 6000)
    return () => window.clearInterval(timer)
  }, [refreshHistory])
  useEffect(() => {
    setOptimistic((current) => Object.fromEntries(Object.entries(current)
      .filter(([key, value]) => locks.current.has(key) || boolOf(editable, key) !== value)))
  }, [editable, pendingKeys])

  const saveChanges = (updates: Record<string, unknown>) => {
    setSaveError('')
    const task = onSave(updates).then(() => {
      const now = new Date()
      setSavedAt([now.getHours(), now.getMinutes(), now.getSeconds()]
        .map((n) => String(n).padStart(2, '0')).join(':'))
    }).catch((e: unknown) => {
      setSaveError(`保存失败：${String(e)}`)
      throw e
    })
    pendingSaves.current.add(task)
    const finish = () => pendingSaves.current.delete(task)
    void task.then(finish, finish)
    return task
  }
  const credential = (field: string, label: string, placeholder: string, hint?: string, secret = true) => (
    <CredentialInput field={field} label={label} configured={boolOf(editable, `${field}_set`)}
      placeholder={placeholder} hint={hint} secret={secret} onSave={saveChanges} />
  )

  const on = (k: string) => optimistic[k] ?? boolOf(editable, k)
  const toggle = async (k: string) => {
    if (locks.current.has(k)) return
    locks.current.add(k)
    setPendingKeys([...locks.current])
    const next = !on(k)
    setOptimistic((s) => ({ ...s, [k]: next }))
    try {
      await saveChanges({ [k]: next })
    } catch {
      setOptimistic((s) => {
        const copy = { ...s }
        delete copy[k]
        return copy
      })
    } finally {
      locks.current.delete(k)
      setPendingKeys([...locks.current])
    }
  }

  const sendTest = async () => {
    if (testing) return
    setTesting(true)
    setTestMsg('等待保存并发送…')
    try {
      await Promise.all([...pendingSaves.current])
      const r = await testNotify()
      setTestMsg(`${r.ok ? '已送达' : '未送达'}：${r.msg || '未知结果'}`)
      refreshHistory()
    } catch (e) {
      setTestMsg(`未发送：${String(e)}`)
    } finally {
      setTesting(false)
    }
  }

  const toggleRow = (key: string, label: string, desc: string) => (
    <ToggleRow label={label} desc={desc} on={on(key)}
      disabled={pendingKeys.includes(key)} onToggle={() => void toggle(key)} />
  )

  return (
    <div className="form notify-page" id="notifyForm">
      <div className={'ntf-status' + (saveError ? ' err' : '')} role={saveError ? 'alert' : 'status'} aria-live="polite">
        <i className={'ntf-status-dot' + (saveError ? ' err' : '')} aria-hidden="true" />
        <span>{saveError || (savedAt ? `已自动保存 · ${savedAt}` : '修改会自动保存，凭据留空保持不变')}</span>
      </div>

      <section className="ntf-sec">
        <h3>推送渠道</h3>
        <div className="ntf-card">
          <div className="ntf-ch-head">
            <div className="ntf-ch-main">
              <h4>飞书群机器人</h4>
              <p>{on('notify_feishu_enabled') ? '已启用 · 通过群机器人 Webhook 推送' : '未启用 · 打开开关后生效'}</p>
            </div>
            <Switch on={on('notify_feishu_enabled')}
              disabled={pendingKeys.includes('notify_feishu_enabled')}
              onToggle={() => void toggle('notify_feishu_enabled')}
              title="开=用飞书自定义机器人推送" />
          </div>
          <div className="ntf-ch-body">
            {credential('notify_feishu_webhook', 'Webhook 地址',
              'https://open.feishu.cn/open-apis/bot/v2/hook/…', undefined, false)}
            {credential('notify_feishu_secret', '加签密钥',
              '安全设置选「签名校验」时必填，否则留空', '选「自定义关键词」时可留空，关键词需含「QQ宠物」。')}
            {credential('notify_feishu_access_token', '图片上传令牌',
              '可选：应用 access token，未配置时仅发文本', '用于在飞书通知里附带截图。')}
          </div>
        </div>
      </section>

      <section className="ntf-sec">
        <h3>Telegram Bot</h3>
        <div className="ntf-card">
          <div className="ntf-ch-head">
            <div className="ntf-ch-main">
              <h4>Telegram Bot</h4>
              <p>{on('notify_telegram_enabled') ? '已启用 · 支持富消息表格与截图' : '未启用 · 打开开关后生效'}</p>
            </div>
            <Switch on={on('notify_telegram_enabled')}
              disabled={pendingKeys.includes('notify_telegram_enabled')}
              onToggle={() => void toggle('notify_telegram_enabled')}
              title="开=用 Telegram Bot 推送" />
          </div>
          <div className="ntf-ch-body">
            {credential('notify_telegram_token', 'Bot Token',
              '123456789:AAE…（@BotFather 获取）', '跟 @BotFather 发 /newbot 建机器人拿 Token。')}
            {credential('notify_telegram_chat_id', 'Chat ID',
              '私聊填数字 id；群/频道填 -100…', '先给机器人发一条消息，再用 @userinfobot 查自己的 Chat ID。', false)}
          </div>
        </div>
      </section>

      <section className="ntf-sec">
        <h3>推送哪些事件</h3>
        <div className="ntf-card">
          {toggleRow('notify_event_notify', '完成类通知',
            '总开关。关闭后收工总账、冒险日报都不发；任务失败告警不受影响。')}
          {toggleRow('notify_quota_done', '收工总账 / 冒险日报',
            '学习与打工当天都跑完时推收工总账；23:59 推当天冒险总次数与总金币。')}
          {toggleRow('notify_career', '隐藏职业解锁',
            '武术家 / 梦境旅人 / 大明星解锁时推送（含职业树截图）。')}
        </div>
      </section>

      <section className="ntf-sec">
        <h3>异常提醒</h3>
        <div className="ntf-card">
          {toggleRow('notify_error_notify', '异常降级提醒',
            '没崩但静默降级时推送；同类错误 30 分钟内最多一条。')}
          <p className="ntf-footnote">
            任务失败告警（学习/打工反复失败后退出调度器）<b>始终尝试发送</b>，不受上方开关影响；
            仍依赖至少一个已启用且配置正确的渠道。
          </p>
        </div>
      </section>

      <section className="ntf-sec">
        <h3>发送测试</h3>
        <div className="ntf-card ntf-testcard">
          <p className="ntf-testhint">保存后立即发一条测试消息，用来确认渠道配置是否可用。</p>
          <button className="ntf-testbtn" type="button" disabled={testing} onClick={() => void sendTest()}>
            {testing ? '正在发送…' : '发送测试通知'}
          </button>
          {testMsg ? (
            <p className={'ntf-testmsg' + (/已送达/.test(testMsg) ? ' ok' : /未|失败/.test(testMsg) ? ' err' : ' pending')} role="status">
              <i className="ntf-status-dot" aria-hidden="true" />
              {testMsg}
            </p>
          ) : null}
        </div>
      </section>

      <details className="ntf-help">
        <summary>
          <span>配置帮助</span>
          <span className="ntf-help-sub">飞书 / Telegram / 截图与送达规则</span>
        </summary>
        <div className="ntf-help-body">
          <div className="ntf-help-item">
            <b>飞书</b>
            <p>群 → 右上角设置 → 群机器人 → 添加机器人 → 自定义机器人，复制 webhook 地址；安全设置选「签名校验」就把密钥填到加签密钥。</p>
          </div>
          <div className="ntf-help-item">
            <b>Telegram</b>
            <p>跟 @BotFather 发 /newbot 建机器人拿 Token；先给机器人发一条消息，再用 @userinfobot 查自己的 Chat ID（群/频道是 -100 开头的负数）。</p>
          </div>
          <div className="ntf-help-item">
            <b>告警与开关</b>
            <p>任务失败告警不受事件开关控制；职业解锁独立于「完成类通知」总开关，由自己的开关控制。</p>
          </div>
          <div className="ntf-help-item">
            <b>凭据</b>
            <p>仅写入，不回传原值。留空保持不变，使用「清除凭据」明确删除。</p>
          </div>
          <div className="ntf-help-item">
            <b>截图与送达</b>
            <p>飞书需要应用图片上传令牌及对应权限；令牌过期或未配置时降级为文本。Telegram 可直接发图。启用远程渠道时，只有远程渠道成功才算送达，桌面通知成功不会掩盖远程发送失败。</p>
          </div>
        </div>
      </details>

      <section className="ntf-sec ntf-histsec">
        <h3>最近发送的通知</h3>
        <div className="ntf-card">
          <div className="ntf-hist-head">
            <div className="ntf-ch-main">
              <h4>{history.length ? `共 ${history.length} 条` : '暂无记录'}</h4>
              <p>{history.length ? '点开查看正文和渠道结果' : '发送通知后会在这里留下记录（旧日志未保存完整正文）'}</p>
            </div>
            <button className="ntf-refresh" type="button" onClick={refreshHistory}>刷新</button>
          </div>
          {!history.length ? null : (
            <ul className="ntf-hist-list">
              {history.map((record, i) => {
                const key = `${record.ts}-${i}`
                // 展开态跟着时间戳走：6 秒轮询后旧记录可能被裁掉、下标会整体前移
                const open = openRecord === record.ts
                const kind = historyKind(record)
                const channels = Object.entries(record.results || {})
                  .filter(([, value]) => !isOffResult(String(value)))
                return (
                  <li key={key} className={'ntf-hist-item' + (open ? ' open' : '')}>
                    <button className="ntf-hist-sum" type="button" aria-expanded={open}
                      onClick={() => setOpenRecord(open ? '' : record.ts)}>
                      <span className={'ntf-badge ' + kind.cls}>{kind.label}</span>
                      <span className="ntf-hist-main">
                        <span className="ntf-hist-title">{historyTitle(record)}</span>
                        <span className="ntf-hist-meta">
                          {record.ts.slice(5, 16)} · {record.ok ? '已送达' : '未送达'}
                        </span>
                      </span>
                      <i className={'ntf-hist-dot' + (record.ok ? '' : ' err')} aria-hidden="true" />
                      <span className="ntf-hist-chev" aria-hidden="true">{open ? '▴' : '▾'}</span>
                    </button>
                    {open ? (
                      <div className="ntf-hist-body">
                        {channels.length ? (
                          <div className="ntf-hist-chips">
                            {channels.map(([channel, value]) => (
                              <span key={channel}
                                className={'ntf-res' + (isFailResult(String(value)) ? ' err' : '')}>
                                {channel} · {value}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="ntf-hist-note">没有启用中的推送渠道</p>
                        )}
                        <p className="ntf-hist-text">{historyBody(record) || '（无正文）'}</p>
                      </div>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </section>
    </div>
  )
}
