import { useCallback, useEffect, useRef, useState } from 'react'
import { changeAccessKey, type AccessInfo } from '../api'
import {
  GROUPS,
  LEGACY_GROUP_ALIASES,
  MENU,
  resolveOpts,
  type FieldDef,
} from '../lib/settings-schema'
import { Switch } from './form'
import { NavHead } from './NavHead'

const strOf = (ed: Record<string, unknown>, k: string): string => {
  const v = ed[k]
  return v === null || v === undefined ? '' : String(v)
}
const boolOf = (ed: Record<string, unknown>, k: string): boolean => !!ed[k]
const numOf = (ed: Record<string, unknown>, k: string, fallback: number): number => {
  const n = typeof ed[k] === 'number' ? (ed[k] as number) : Number(ed[k])
  return Number.isFinite(n) ? n : fallback
}
const pad2 = (n: number) => String(n).padStart(2, '0')
const clock = () => {
  const d = new Date()
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

/** 顶部状态条：与通知页同一套（自动保存 / 保存失败） */
function SetBand({ error = '', savedAt = '', text = '修改会自动保存' }: {
  error?: string
  savedAt?: string
  text?: string
}) {
  const err = !!error
  return (
    <div className={'set-status' + (err ? ' err' : '')} role={err ? 'alert' : 'status'} aria-live="polite">
      <i className={'set-status-dot' + (err ? ' err' : '')} aria-hidden="true" />
      <span>{error || (savedAt ? `已自动保存 · ${savedAt}` : text)}</span>
    </div>
  )
}

/** 凭据输入（`kind: 'secret'`）：**只写不回显**。
 *
 *  后端 `editable_snapshot()` 对凭据只下发 `<key>_set`（bool），原值永不出后端，
 *  所以这里没有"当前值"可回填 —— 输入框始终是空的，`draft` 只来自用户输入：
 *  **留空 = 保持不变**（失焦不保存）、输入非空 = 失焦保存替换、
 *  「清除凭据」= 保存空串（唯一明确的删除入口）。 */
function SecretControl({
  label,
  tip,
  field,
  configured,
  disabled,
  onSave,
}: {
  label: string
  tip?: string
  field: string
  /** 后端 `editable[<field>_set]` */
  configured: boolean
  /** 依赖的开关没开（`enabledWhen`）时置灰 */
  disabled: boolean
  onSave: (u: Record<string, unknown>) => Promise<void>
}) {
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  /** 保存串行化：点「清除」时输入框会先 blur、blur 里若已有一份草稿也会触发保存，
   *  两次保存并发的话，清空可能被"保存草稿"抢先写回去（点了清除却没清掉）。
   *  排队执行 → 后点的「清除」一定是最后一次写。 */
  const queue = useRef<Promise<void>>(Promise.resolve())
  const save = (value: string) => {
    const v = value.trim()   // Token 末尾的空格会让鉴权 401，且界面上完全看不出来
    queue.current = queue.current.then(async () => {
      setSaving(true)
      try {
        await onSave({ [field]: v })
        // 原文不会回显：保存成功后清空输入框；清除操作无论有没有草稿都清空
        setDraft((cur) => (v === '' || cur.trim() === v ? '' : cur))
      } catch {
        // 失败原因由 SettingsPage 的保存状态条显示；草稿保留，用户可以直接重试
      } finally {
        setSaving(false)
      }
    })
    return queue.current
  }
  const id = `set-${field}`
  return (
    <div className="set-field">
      <div className="set-field-head">
        <label className="set-label" htmlFor={id}>{label}</label>
        <span className={'set-chip' + (configured ? ' ok' : '')}>
          {configured ? '已配置' : '未配置'}
        </span>
      </div>
      <input
        id={id}
        className="set-input"
        type="password"
        value={draft}
        disabled={disabled || saving}
        autoComplete="new-password"
        placeholder={configured ? '留空保持不变，输入新值替换' : '输入后失焦即保存'}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { if (draft.trim()) void save(draft) }}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
      />
      <div className="set-field-foot">
        {tip ? <p className="set-hint">{tip}</p> : <span />}
        {configured && (
          <button
            className="set-clear"
            type="button"
            disabled={disabled || saving}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => void save('')}
          >
            清除凭据
          </button>
        )}
      </div>
    </div>
  )
}

/** 单个字段的控件（按 kind 分派）。**改动即保存**，与旧版一致。 */
function Field({ f, editable, onSave, on, onToggle, pending }: {
  f: FieldDef
  editable: Record<string, unknown>
  onSave: (u: Record<string, unknown>) => Promise<void>
  /** 开关的**乐观值**（保存是异步的，拿服务端值会让开关"点一下弹回去"） */
  on: (k: string) => boolean
  onToggle: (k: string) => void
  /** 正在保存的开关键：保存期间禁用，避免连点两下把值写反 */
  pending: string[]
}) {
  const [local, setLocal] = useState<string>(() => strOf(editable, f.key))
  const serverVal = strOf(editable, f.key)
  /** 依赖的开关没开 → 本项置灰不可改（如"等他空闲时"依赖"等TA空闲"）。
   *  **判断也走乐观值**：刚把开关点开的那一瞬间下面几项就该解禁，不用等下一轮轮询。 */
  const locked = !!f.enabledWhen && !on(f.enabledWhen)
  const id = `set-${f.key}`

  // 后端数据回来时同步（例如别处改了同一项）。
  // **不能每次轮询都无脑覆盖**：`/api/data` 每 6 秒给一个全新的 editable 对象，
  // 按其身份做依赖会把正在输入框里打的字用服务端旧值冲掉。
  // 只有"服务端值真的变了"才覆盖本地草稿。
  const lastServer = useRef(serverVal)
  useEffect(() => {
    if (lastServer.current === serverVal) return
    lastServer.current = serverVal
    setLocal(serverVal)
  }, [serverVal])

  if (f.kind === 'bool') {
    return (
      <div className="set-row">
        <div className="set-row-main">
          <span className="set-row-label">{f.label}</span>
          {f.tip ? <span className="set-row-desc">{f.tip}</span> : null}
        </div>
        <Switch
          on={on(f.key)}
          title={f.tip}
          disabled={pending.includes(f.key)}
          onToggle={() => onToggle(f.key)}
        />
      </div>
    )
  }

  if (f.kind === 'secret') {
    return (
      <SecretControl
        label={f.label}
        tip={f.tip}
        field={f.key}
        configured={boolOf(editable, `${f.key}_set`)}
        disabled={locked}
        onSave={onSave}
      />
    )
  }

  if (f.kind === 'select') {
    const opts = resolveOpts(f.opts, editable)
    return (
      <div className="set-field">
        <div className="set-field-head">
          <label className="set-label" htmlFor={id}>{f.label}</label>
        </div>
        <select
          id={id}
          className="set-select"
          value={local}
          disabled={locked}
          onChange={(e) => {
            setLocal(e.target.value)
            void onSave({ [f.key]: e.target.value })
          }}
        >
          {opts.map((o) => (
            <option key={o} value={o}>
              {f.optLabels?.[o] ?? o}
            </option>
          ))}
        </select>
        {f.tip ? <p className="set-hint">{f.tip}</p> : null}
      </div>
    )
  }

  if (f.kind === 'int') {
    return (
      <div className="set-row">
        <div className="set-row-main">
          <label className="set-row-label" htmlFor={id}>{f.label}</label>
          {f.tip ? <span className="set-row-desc">{f.tip}</span> : null}
        </div>
        <span className="set-numwrap">
          <input
            id={id}
            className="set-num"
            type="number"
            value={local}
            disabled={locked}
            placeholder={f.placeholder}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={() => {
              if (local !== serverVal) void onSave({ [f.key]: local })
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            }}
          />
          {f.unit ? <span className="set-unit">{f.unit}</span> : null}
        </span>
      </div>
    )
  }

  // text
  return (
    <div className="set-field">
      <div className="set-field-head">
        <label className="set-label" htmlFor={id}>{f.label}</label>
      </div>
      <input
        id={id}
        className="set-input"
        type="text"
        value={local}
        disabled={locked}
        placeholder={f.placeholder}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={() => {
          if (local !== serverVal) void onSave({ [f.key]: local })
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        }}
      />
      {f.tip ? <p className="set-hint">{f.tip}</p> : null}
    </div>
  )
}

/** 收益档说明（只读）：数字来自 editable，不写死。 */
function EfficiencyTable({ editable }: { editable: Record<string, unknown> }) {
  const tier1 = numOf(editable, 'efficiency_tier1_hours', 8)
  const stop = numOf(editable, 'stop_total_hours', 0)
  return (
    <div className="set-explain">
      <table className="set-mintbl">
        <thead>
          <tr><th>什么时候</th><th>会发生什么</th></tr>
        </thead>
        <tbody>
          <tr><td>没到 {tier1} 小时</td><td>正常收益</td></tr>
          <tr><td>满 {tier1} 小时</td><td>收益降到 25%（只提示，不自动停）</td></tr>
          <tr>
            <td>{stop > 0 ? `满 ${stop} 小时` : '不设停止点'}</td>
            <td>{stop > 0 ? '学习、打工两项都停，之后只跑冒险' : '一直按每日配额运行'}</td>
          </tr>
          <tr><td>次日 0 点</td><td>时长清零，恢复</td></tr>
        </tbody>
      </table>
    </div>
  )
}

/** 访问码卡片：把自动生成的长随机串换成自己好记的口令。
 *
 *  两个必须点：① 成功后后端会**换发新 Cookie**（会话是访问码的 HMAC），
 *  当前浏览器不会掉线、但其它已登录设备会被踢回验证页 —— 界面要提前说明；
 *  ② 访问码只写不回显，所以这里只有"新码 + 确认"两个框，没有"当前码"。
 *  长度下限由后端给（`access.min_length`），不写死在前端。 */
function AccessKeyPanel({ access }: { access?: AccessInfo }) {
  const minLength = access?.min_length && access.min_length > 0 ? access.min_length : 8
  const [value, setValue] = useState('')
  const [again, setAgain] = useState('')
  const [state, setState] = useState<'idle' | 'saving' | 'done'>('idle')
  const [error, setError] = useState('')
  const mismatch = again.length > 0 && value !== again
  const tooShort = value.length > 0 && value.length < minLength
  const canSubmit = value.length >= minLength && value === again && state !== 'saving'

  const submit = async () => {
    if (!canSubmit) return
    setState('saving')
    setError('')
    try {
      await changeAccessKey(value)
      setValue('')
      setAgain('')
      setState('done')
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
      setState('idle')
    }
  }

  const message = error
    || (tooShort ? `访问码至少 ${minLength} 位`
      : mismatch ? '两次输入不一致'
        : state === 'done' ? '已更新：当前浏览器已换用新码，其它设备需要重新验证。'
          : '留空不会修改。访问码只写入、不回显。')

  return (
    <>
      <section className="set-sec">
        <h3>当前访问码</h3>
        <div className="set-card">
          <div className="set-row">
            <div className="set-row-main">
              <span className="set-row-label">访问码</span>
              <span className="set-row-desc">当前浏览器已通过验证，其它设备需要各自的会话。</span>
            </div>
            <span className={'set-chip' + (access?.custom ? ' ok' : '')}>
              {access?.custom ? '已自定义' : '自动生成'}
            </span>
          </div>
          <p className="set-footnote">
            忘了访问码就去电脑上看 <b>runs/dashboard_access.token</b>；嫌难记可以在下面换掉。
          </p>
        </div>
      </section>

      <section className="set-sec">
        <h3>修改访问码</h3>
        <div className="set-card">
          <div className="set-field">
            <div className="set-field-head">
              <label className="set-label" htmlFor="set-new-access-key">新访问码</label>
            </div>
            <input
              id="set-new-access-key"
              className="set-input"
              type="password"
              autoComplete="new-password"
              value={value}
              placeholder={`至少 ${minLength} 位，建议用一句你记得住的话`}
              onChange={(e) => { setValue(e.target.value); setState('idle'); setError('') }}
              onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
            />
          </div>
          <div className="set-field">
            <div className="set-field-head">
              <label className="set-label" htmlFor="set-confirm-access-key">再输一遍</label>
            </div>
            <input
              id="set-confirm-access-key"
              className="set-input"
              type="password"
              autoComplete="new-password"
              value={again}
              placeholder="与上面保持一致"
              onChange={(e) => { setAgain(e.target.value); setState('idle'); setError('') }}
              onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
            />
          </div>
          <button className="set-primary" type="button" disabled={!canSubmit} onClick={() => void submit()}>
            {state === 'saving' ? '保存中…' : '保存新访问码'}
          </button>
          <p className={'set-msg' + (error ? ' err' : state === 'done' ? '' : ' sub')} role={error ? 'alert' : 'status'} aria-live="polite">
            {message}
          </p>
        </div>
      </section>

      <section className="set-sec">
        <h3>说明</h3>
        <div className="set-card">
          <div className="set-note">
            <b>生效范围</b>
            <span>访问码换掉后，所有旧会话立即失效（当前这台浏览器会自动换成新会话）。</span>
          </div>
          <div className="set-note">
            <b>安全</b>
            <span>只在本机/可信内网使用；登录失败会限流，但短口令仍不如长口令安全，别做公网端口映射。</span>
          </div>
        </div>
      </section>
    </>
  )
}

/** TG 机器人的**只读状态**（后端 `editable_snapshot()` 里那三个 `tgbot_*` 状态位）。 */
function TgbotStatus({ editable }: { editable: Record<string, unknown> }) {
  const has = (k: string) => k in editable
  const running = has('tgbot_running') ? boolOf(editable, 'tgbot_running') : null
  const err = strOf(editable, 'tgbot_last_error')
  const allowed = typeof editable.tgbot_allowed_count === 'number'
    ? (editable.tgbot_allowed_count as number) : null
  return (
    <>
      <div className="set-row">
        <div className="set-row-main">
          <span className="set-row-label">运行状态</span>
          <span className="set-row-desc">后台长轮询线程是否真的起来了（没配 Token、或开关没开时都不会起）</span>
        </div>
        <span className={'set-chip' + (running ? ' ok' : '')}>
          {running === null ? '—' : running ? '轮询中' : '未运行'}
        </span>
      </div>
      <div className="set-row">
        <div className="set-row-main">
          <span className="set-row-label">可操作会话</span>
          <span className="set-row-desc">按白名单算出来的会话数（allowed_chats 为空时来自通知里的 telegram_chat_id）</span>
        </div>
        <span className="set-chip">{allowed === null ? '—' : `${allowed} 个`}</span>
      </div>
      {err ? (
        <div className="set-field">
          <div className="set-field-head">
            <span className="set-label">最近错误</span>
          </div>
          <p className="set-hint set-hint-err">{err}</p>
        </div>
      ) : null}
    </>
  )
}

/** 设置页：一级菜单（3 个分区 / 7 个二级页）→ 二级表单。
 *
 *  **grp 由 App 从 URL 派生后传进来**（不再在这里自持 state + replaceState）：
 *  二级必须是历史栈里的**真一层**，侧滑返回才能退回一级菜单 —— 早先用
 *  replaceState 改写当前条目，栈里只有"总览→设置"两条，右滑一下就直接回总览
 *  （用户实报）。返回按钮同样走 `onBackSub()`（history.back()），与手势同一条路。
 *
 *  视觉与通知页同一套：白卡分节、顶部自动保存状态条、字段标签在上/输入整行，
 *  行内直接写说明（手机上没有 hover）。 */
export function SettingsPage({
  editable,
  access,
  onSave,
  onExit,
  grp,
  onOpen,
  onBackSub,
}: {
  editable: Record<string, unknown>
  /** 访问权限状态（设置 → 仪表盘访问码 用） */
  access?: AccessInfo
  onSave: (u: Record<string, unknown>) => Promise<void>
  onExit: () => void
  /** 当前二级分组（null = 一级菜单列表） */
  grp: string | null
  onOpen: (id: string) => void
  onBackSub: () => void
}) {
  /** 开关的乐观值 + 保存锁（与 NotifyPage 同一套写法）：保存是异步的，
   *  等下一轮 `/api/data` 回来才算数的话，开关会"点一下弹回去"。
   *  `locks` 在保存期间挡住连点，保存结束就放行；`optimistic` 只保留
   *  「仍在保存」或「与服务端值不一致」的项，服务端追上来自动消解。 */
  const [optimistic, setOptimistic] = useState<Record<string, boolean>>({})
  const [pending, setPending] = useState<string[]>([])
  const [savedAt, setSavedAt] = useState('')
  const [saveError, setSaveError] = useState('')
  const locks = useRef(new Set<string>())
  useEffect(() => {
    setOptimistic((current) => Object.fromEntries(Object.entries(current)
      .filter(([key, value]) => locks.current.has(key) || boolOf(editable, key) !== value)))
  }, [editable, pending])

  /** 所有字段保存都走这里：保留 App 的 toast，同时把成功时间/失败原因写进状态条 */
  const save = useCallback((updates: Record<string, unknown>) => {
    setSaveError('')
    return onSave(updates).then(() => {
      setSavedAt(clock())
    }).catch((e: unknown) => {
      setSaveError(`保存失败：${String(e)}`)
      throw e
    })
  }, [onSave])

  const on = (k: string) => optimistic[k] ?? boolOf(editable, k)
  const onToggle = (k: string) => {
    if (locks.current.has(k)) return
    locks.current.add(k)
    setPending([...locks.current])
    const next = !on(k)
    setOptimistic((s) => ({ ...s, [k]: next }))
    // save 失败时会 rethrow（并已在状态条显示原因）：这里把它撤掉，
    // 让开关回落到服务端值，而不是停在"看起来改了其实没写进去"的假状态。
    void save({ [k]: next }).catch(() => {
      setOptimistic((s) => {
        const copy = { ...s }
        delete copy[k]
        return copy
      })
    }).finally(() => {
      locks.current.delete(k)
      setPending([...locks.current])
    })
  }

  const gid = grp ? (GROUPS[grp] ? grp : LEGACY_GROUP_ALIASES[grp] ?? null) : null
  const g = gid ? GROUPS[gid] ?? null : null

  if (!g) {
    const total = MENU.reduce((n, section) => n + section.items.length, 0)
    return (
      <>
        <NavHead title="设置" onBack={onExit} />
        <div className="set-page" id="setMenu">
          <SetBand text={`${total} 个设置分组 · 改动会自动保存`} />
          {MENU.map(({ section, items }) => {
            const rows = items.filter((it) => GROUPS[it.id])
            if (!rows.length) return null
            return (
              <section className="set-sec" key={section}>
                <h3>{section}</h3>
                <div className="set-card">
                  {rows.map((it) => (
                    <button className="set-menu-row" type="button" key={it.id} onClick={() => onOpen(it.id)}>
                      <span className="set-menu-main">
                        <span className="set-menu-label">{it.title}</span>
                        <span className="set-menu-desc">{GROUPS[it.id]?.desc}</span>
                      </span>
                      <span className="set-menu-chev" aria-hidden="true">›</span>
                    </button>
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      </>
    )
  }

  return (
    <>
      <div className="navhead">
        <button className="backbtn" title="返回设置列表" onClick={onBackSub}>
          <img src="/qp-icons/official/off_l1_back.png" alt="" />
        </button>
        <span className="navtitle">{g.title}</span>
      </div>
      <div className="set-page" id="setForm">
        <SetBand
          error={saveError}
          savedAt={savedAt}
          text={g.id === 'access' ? '访问码只写入、不回显' : '修改会自动保存'}
        />
        {g.id === 'access' ? (
          <AccessKeyPanel access={access} />
        ) : (
          g.sections.map((sec) => (
            <section className="set-sec" key={sec.title}>
              <h3>{sec.title}</h3>
              <div className="set-card">
                {sec.fields.map((f) => (
                  <Field key={f.key} f={f} editable={editable} onSave={save}
                    on={on} onToggle={onToggle} pending={pending} />
                ))}
                {sec.extra === 'efficiency' ? <EfficiencyTable editable={editable} /> : null}
                {sec.extra === 'tgbot-status' ? <TgbotStatus editable={editable} /> : null}
                {sec.notes?.map((n) => (
                  <div className="set-note" key={n.t}>
                    <b>{n.t}</b>
                    <span>{n.b}</span>
                  </div>
                ))}
              </div>
            </section>
          ))
        )}
      </div>
    </>
  )
}
