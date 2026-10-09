import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchNotifyHistory, testNotify } from '../api'
import type { NotificationRecord } from '../api'
import { FGroup, FRow, NoteRow, Switch } from './form'

/** `editable` 是个松散的 Record<string, unknown>，取值统一走这两个小工具 */
const boolOf = (ed: Record<string, unknown>, k: string): boolean => !!ed[k]
function CredentialInput({ field, configured, secret = true, placeholder, onSave }: {
  field: string
  configured: boolean
  secret?: boolean
  placeholder: string
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
    <div style={{ width: '100%', minWidth: 0 }}>
      <input type={secret ? 'password' : 'text'} value={draft} disabled={saving}
        style={{ width: '100%', minWidth: 0 }} autoComplete="off" aria-label={field}
        placeholder={configured ? '已配置 · 留空保持不变，输入新值替换' : placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { if (draft.trim()) void save(draft).catch(() => undefined) }} />
      {configured && <button className="minibtn" type="button" disabled={saving}
        onClick={() => void save('').catch(() => undefined)}>清除已保存凭据</button>}
    </div>
  )
}

/** 通知页：飞书 / Telegram / 事件开关 / 异常提醒 + 配置说明。
 *  开关点一下**立即保存**（与设置页同一套「改动即自动保存」），文本框失焦时保存。 */
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
  const [testing, setTesting] = useState(false)
  const [pendingKeys, setPendingKeys] = useState<string[]>([])
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
    const task = onSave(updates).catch((e: unknown) => {
      setSaveError(`保存失败：${String(e)}`)
      throw e
    })
    pendingSaves.current.add(task)
    const finish = () => pendingSaves.current.delete(task)
    void task.then(finish, finish)
    return task
  }
  const credential = (field: string, placeholder: string, secret = true) => (
    <CredentialInput field={field} configured={boolOf(editable, `${field}_set`)}
      placeholder={placeholder} secret={secret} onSave={saveChanges} />
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

  return (
    <div className="form" id="notifyForm">
      <div role={saveError ? 'alert' : 'status'} aria-live="polite"
        style={{ color: saveError ? '#a63224' : 'var(--sub)', fontSize: 12,
          height: '3em', lineHeight: '1.5em', overflowY: 'auto' }}>
        {saveError || '修改会自动保存；凭据留空保持不变。'}
      </div>
      <FGroup title="飞书群机器人">
        <FRow k="启用">
          <Switch on={on('notify_feishu_enabled')} disabled={pendingKeys.includes('notify_feishu_enabled')} onToggle={() => void toggle('notify_feishu_enabled')} title="开=用飞书自定义机器人推送" />
        </FRow>
        <FRow k="webhook">
          {credential('notify_feishu_webhook', 'https://open.feishu.cn/open-apis/bot/v2/hook/…', false)}
        </FRow>
        <FRow k="加签密钥">
          {credential('notify_feishu_secret', '安全设置选「签名校验」时必填，否则留空')}
        </FRow>
        <FRow k="图片上传令牌">
          {credential('notify_feishu_access_token', '可选：应用 access token，未配置时仅发文本')}
        </FRow>
      </FGroup>

      <FGroup title="Telegram Bot">
        <FRow k="启用">
          <Switch on={on('notify_telegram_enabled')} disabled={pendingKeys.includes('notify_telegram_enabled')} onToggle={() => void toggle('notify_telegram_enabled')} title="开=用 Telegram Bot 推送" />
        </FRow>
        <FRow k="Bot Token">
          {credential('notify_telegram_token', '123456789:AAE…（@BotFather 获取）')}
        </FRow>
        <FRow k="Chat ID">
          {credential('notify_telegram_chat_id', '私聊填数字 id；群/频道填 -100…', false)}
        </FRow>
      </FGroup>

      <FGroup title="推送哪些事件">
        <FRow k="收工总账 / 冒险日报">
          <Switch
            on={on('notify_quota_done')}
            disabled={pendingKeys.includes('notify_quota_done')}
            onToggle={() => void toggle('notify_quota_done')}
            title="开=学习与打工当天都跑完时推送收工总账（各跑了多久 + 学习三维属性/学分、打工工分/金币），23:59 再推当天冒险总次数与总金币"
          />
        </FRow>
        <FRow k="隐藏职业解锁">
          <Switch
            on={on('notify_career')}
            disabled={pendingKeys.includes('notify_career')}
            onToggle={() => void toggle('notify_career')}
            title="开=武术家/梦境旅人/大明星解锁时推送（含职业树截图）"
          />
        </FRow>
        <FRow k="完成类通知总开关">
          <Switch
            on={on('notify_event_notify')}
            disabled={pendingKeys.includes('notify_event_notify')}
            onToggle={() => void toggle('notify_event_notify')}
            title="关掉后所有「完成」类通知（收工总账、冒险日报）都不发；任务失败告警不受影响"
          />
        </FRow>
      </FGroup>

      <FGroup title="异常提醒">
        <FRow k="异常降级提醒">
          <Switch
            on={on('notify_error_notify')}
            disabled={pendingKeys.includes('notify_error_notify')}
            onToggle={() => void toggle('notify_error_notify')}
            title="开=出现「没崩但静默降级」的错误时推送，同类错误 30 分钟内最多一条"
          />
        </FRow>
        <FRow k="" block>
          <span style={{ color: 'var(--sub)', fontSize: 12, lineHeight: 1.6 }}>
            任务失败告警（学习/打工反复失败后退出调度器）
            <b>始终尝试发送</b>，不受事件开关影响；仍依赖已启用且配置正确的渠道。
          </span>
        </FRow>
      </FGroup>

      <FGroup title="测试与说明">
        <FRow k="测试">
          <span className="ctrl">
            <span id="notifyTestMsg">{testMsg}</span>
            <button className="minibtn" type="button" disabled={testing} onClick={() => void sendTest()}>
              发送测试通知
            </button>
          </span>
        </FRow>
        <NoteRow t="飞书">
          群 → 右上角设置 → 群机器人 → 添加机器人 → 自定义机器人，复制 webhook 地址；安全设置选「签名校验」就把密钥填到加签密钥（选「自定义关键词」可留空，关键词需含"QQ宠物"）。
        </NoteRow>
        <NoteRow t="Telegram">
         跟 @BotFather 发 /newbot 建机器人拿 Token；<b>先给机器人发一条消息</b>，再用 @userinfobot 查自己的 Chat ID（群/频道是 -100 开头的负数）。
        </NoteRow>
        <NoteRow t="告警">任务失败告警不受事件开关控制；职业解锁独立于“完成类通知总开关”，由自己的开关控制。</NoteRow>
        <NoteRow t="凭据">仅写入，不回传原值。留空保持不变，使用“清除已保存凭据”明确删除。</NoteRow>
        <NoteRow t="截图">飞书需要应用图片上传令牌及对应权限；令牌过期或未配置时降级为文本。Telegram 可直接发图。</NoteRow>
        <NoteRow t="送达">启用远程渠道时，只有远程渠道成功才算送达；桌面通知成功不会掩盖远程发送失败。</NoteRow>
      </FGroup>
      <FGroup title="最近发送的通知">
        {!history.length && <NoteRow t="记录">升级后会保留通知正文与各渠道结果；以前的日志未保存完整正文。</NoteRow>}
        {history.map((record, i) => (
          <FRow key={`${record.ts}-${i}`} k={record.ts} block>
            <div style={{ overflowWrap: 'anywhere' }}>
              <b>{record.title}</b>
              <p style={{ whiteSpace: 'pre-wrap', margin: '6px 0' }}>{record.content}</p>
              <small>{record.ok ? '已送达' : '未送达'} · {Object.entries(record.results)
                .map(([channel, result]) => `${channel}: ${result}`).join('、')}</small>
            </div>
          </FRow>
        ))}
      </FGroup>
    </div>
  )
}
