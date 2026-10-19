import { useEffect, useRef, useState } from 'react'
import { changeAccessKey, type AccessInfo } from '../api'
import { GROUPS, MENU, resolveOpts, type FieldDef } from '../lib/settings-schema'
import { FGroup, FRow, NoteRow, Switch } from './form'
import { NavHead } from './NavHead'

const strOf = (ed: Record<string, unknown>, k: string): string => {
  const v = ed[k]
  return v === null || v === undefined ? '' : String(v)
}
const boolOf = (ed: Record<string, unknown>, k: string): boolean => !!ed[k]

/** 凭据输入（`kind: 'secret'`）：**只写不回显**。
 *
 *  后端 `editable_snapshot()` 对凭据只下发 `<key>_set`（bool），原值永不出后端，
 *  所以这里没有"当前值"可回填 —— 输入框始终是空的，`draft` 只来自用户输入：
 *  **留空 = 保持不变**（失焦不保存）、输入非空 = 失焦保存替换、
 *  「清除已保存凭据」= 保存空串（唯一明确的删除入口）。
 *  与 NotifyPage 的 CredentialInput 同一套语义（这里不跨文件 import 那个未导出的
 *  组件，改成设置页自带一份，免得通知页的布局改动波及这边）。 */
function SecretControl({
  field,
  configured,
  disabled,
  onSave,
}: {
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
        // 原文本来就不会回显，留着只会让人以为"里面存的是这个"
        setDraft((cur) => (cur.trim() === v ? '' : cur))
      } catch {
        // 失败原因由 App.saveOne 统一走 #torderToast；草稿保留，用户可以直接重试
      } finally {
        setSaving(false)
      }
    })
    return queue.current
  }
  return (
    <span style={{
      flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column',
      alignItems: 'flex-end', gap: 6,
    }}>
      <input
        type="password"
        value={draft}
        disabled={disabled}
        aria-label={field}
        autoComplete="new-password"
        placeholder={configured ? '已配置 · 留空保持不变，输入新值替换' : '未配置 · 输入后失焦即保存'}
        // 自带样式、不并进 `.form input[type=text]` 那条公共规则：那边是右对齐灰字，
        // 密码框右对齐会让"输入了什么"完全看不出来（通知页的凭据框是同一套值）
        style={{
          width: '100%', minWidth: 0, boxSizing: 'border-box',
          border: '1px solid var(--line)', borderRadius: 'var(--r-sm)',
          background: '#FFFDF8', color: '#1C1C1E', textAlign: 'left',
          padding: '6px 8px', fontSize: 'calc(var(--u) * 12)',
        }}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { if (draft.trim()) void save(draft) }}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
      />
      {configured && (
        <button className="minibtn" type="button" disabled={disabled || saving}
          onClick={() => void save('')}>清除已保存凭据</button>
      )}
    </span>
  )
}

/** 单个字段的控件（按 kind 分派）。**改动即保存**，与 旧版 一致。 */
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
  /** 依赖的开关没开 → 本项置灰不可改（如"等他空闲时"依赖"等TA空闲"：
   *  没开"等TA空闲"，也就没有什么"等他空闲时干什么"可言）。
   *  **判断也走乐观值**：刚把开关点开的那一瞬间下面几项就该解禁，不用等下一轮轮询。 */
  const locked = !!f.enabledWhen && !on(f.enabledWhen)

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
      <FRow k={f.label} tip={f.tip}>
        <Switch
          on={on(f.key)}
          title={f.tip}
          disabled={pending.includes(f.key)}
          onToggle={() => onToggle(f.key)}
        />
      </FRow>
    )
  }

  if (f.kind === 'secret') {
    return (
      <FRow k={f.label} tip={f.tip}>
        <SecretControl
          field={f.key}
          configured={boolOf(editable, `${f.key}_set`)}
          disabled={locked}
          onSave={onSave}
        />
      </FRow>
    )
  }

  if (f.kind === 'select') {
    const opts = resolveOpts(f.opts, editable)
    return (
      <FRow k={f.label} tip={f.tip}>
        <select
          value={local}
          disabled={locked}
          style={f.w ? { width: `calc(var(--u) * ${f.w + 40})` } : undefined}
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
      </FRow>
    )
  }

  // int / text
  return (
    <FRow k={f.label} tip={f.tip} block={f.wide}>
      <span className="ctrl" style={f.wide ? { width: '100%' } : undefined}>
        <input
          type={f.kind === 'int' ? 'number' : 'text'}
          value={local}
          disabled={locked}
          placeholder={f.placeholder}
          style={
            f.wide
              // 整行模式：`max-width:58%` / 窄屏 `52%` 那两条规则会把它压回半行，
              // 单行样式里必须一起覆盖掉，width:100% 才真的满宽。
              ? { width: '100%', maxWidth: '100%', textAlign: 'left' }
              : f.w
                ? { width: `calc(var(--u) * ${f.w})` }
                : undefined
          }
          onChange={(e) => setLocal(e.target.value)}
          onBlur={() => {
            if (local !== serverVal) void onSave({ [f.key]: local })
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          }}
        />
        {/* 单位跟在**输入框后面**（旧版 的写法：`<input ...><span class="u">小时</span>`，
            CSS 的 .form .u 也是 margin-left —— 放前面会变成「秒 [60]」） */}
        {f.unit ? <span className="u">{f.unit}</span> : null}
      </span>
    </FRow>
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

  return (
    <>
      <FGroup title="仪表盘访问码" id="access">
        <FRow k="状态">
          <span style={{ color: 'var(--sub)', fontSize: 12 }}>
            {access?.custom ? '已自定义' : '自动生成的随机码'}
          </span>
        </FRow>
        <FRow k="新访问码">
          <span className="ctrl">
            <input
              type="password"
              aria-label="new_access_key"
              autoComplete="new-password"
              value={value}
              placeholder={`至少 ${minLength} 位，建议用一句你记得住的话`}
              onChange={(e) => { setValue(e.target.value); setState('idle'); setError('') }}
              onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
            />
          </span>
        </FRow>
        <FRow k="再输一遍">
          <span className="ctrl">
            <input
              type="password"
              aria-label="confirm_access_key"
              autoComplete="new-password"
              value={again}
              onChange={(e) => { setAgain(e.target.value); setState('idle'); setError('') }}
              onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
            />
          </span>
        </FRow>
        <FRow k="">
          <span className="ctrl">
            <button className="minibtn" type="button" disabled={!canSubmit}
              onClick={() => void submit()}>
              {state === 'saving' ? '保存中…' : '保存新访问码'}
            </button>
          </span>
        </FRow>
        <FRow k="" block>
          <span role={error ? 'alert' : 'status'} aria-live="polite"
            style={{ color: error ? '#a63224' : 'var(--sub)', fontSize: 12, lineHeight: 1.6 }}>
            {error
              || (tooShort ? `访问码至少 ${minLength} 位`
                : mismatch ? '两次输入不一致'
                  : state === 'done' ? '已更新：当前浏览器已换用新码，其它设备需要重新验证。'
                    : '留空不会修改。访问码只写入、不回显；忘了就去电脑上看 runs/dashboard_access.token。')}
          </span>
        </FRow>
        <NoteRow t="生效范围">
          访问码换掉后，所有旧会话立即失效（当前这台浏览器会自动换成新会话）。
        </NoteRow>
        <NoteRow t="安全">
          只在本机/可信内网使用；登录失败会限流，但短口令仍不如长口令安全，别做公网端口映射。
        </NoteRow>
      </FGroup>
    </>
  )
}

/** TG 机器人的**只读状态**（后端 `editable_snapshot()` 里那三个 `tgbot_*` 状态位）。
 *
 *  没有这块的话，开关打开后完全看不出后台线程到底起没起、白名单生效了几个人——
 *  用户只能去翻日志。三个字段都只读、不参与保存。
 *  注意 `editable` 是 `Record<string, unknown>`，取不到时按"未知"显示而不是伪造 false。 */
function TgbotStatus({ editable }: { editable: Record<string, unknown> }) {
  const has = (k: string) => k in editable
  const running = has('tgbot_running') ? boolOf(editable, 'tgbot_running') : null
  const err = strOf(editable, 'tgbot_last_error')
  const allowed = typeof editable.tgbot_allowed_count === 'number'
    ? (editable.tgbot_allowed_count as number) : null
  const sub = { color: 'var(--sub)', fontSize: 'calc(var(--u) * 12)' }
  return (
    <>
      <FRow k="运行状态" tip="后台长轮询线程是否真的起来了（没配 Token、或开关没开时都不会起）">
        <span style={sub}>
          {running === null ? '—' : running ? '轮询中' : '未运行'}
        </span>
      </FRow>
      <FRow k="可操作会话" tip="按 current 生效的白名单算出来的会话数（allowed_chats 为空时来自通知里的 telegram_chat_id）">
        <span style={sub}>{allowed === null ? '—' : `${allowed} 个`}</span>
      </FRow>
      {err ? (
        <FRow k="最近错误" block>
          <span style={{ ...sub, overflowWrap: 'anywhere' }}>{err}</span>
        </FRow>
      ) : null}
    </>
  )
}

/** 设置页：一级菜单（4 个分区 / 16 个二级页）→ 二级表单。
 *  旧版 是把所有字段渲染进 `#setForm` 再用 `.hide` 切组，这边**只渲染当前组**。
 *
 *  **grp 由 App 从 URL 派生后传进来**（不再在这里自持 state + replaceState）：
 *  二级必须是历史栈里的**真一层**，侧滑返回才能退回一级菜单 —— 早先用
 *  replaceState 改写当前条目，栈里只有"总览→设置"两条，右滑一下就直接回总览
 *  （用户实报）。返回按钮同样走 `onBackSub()`（history.back()），与手势同一条路。 */
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
  const g = grp ? GROUPS[grp] : null

  /** 开关的乐观值 + 保存锁（与 NotifyPage 同一套写法）：保存是异步的，
   *  等下一轮 `/api/data` 回来才算数的话，开关会"点一下弹回去"。
   *  `locks` 在保存期间挡住连点，保存结束就放行；`optimistic` 只保留
   *  「仍在保存」或「与服务端值不一致」的项，服务端追上来自动消解。 */
  const [optimistic, setOptimistic] = useState<Record<string, boolean>>({})
  const [pending, setPending] = useState<string[]>([])
  const locks = useRef(new Set<string>())
  useEffect(() => {
    setOptimistic((current) => Object.fromEntries(Object.entries(current)
      .filter(([key, value]) => locks.current.has(key) || boolOf(editable, key) !== value)))
  }, [editable, pending])

  const on = (k: string) => optimistic[k] ?? boolOf(editable, k)
  const onToggle = (k: string) => {
    if (locks.current.has(k)) return
    locks.current.add(k)
    setPending([...locks.current])
    const next = !on(k)
    setOptimistic((s) => ({ ...s, [k]: next }))
    // saveOne 失败时会 rethrow（并已在 toast 上显示原因）：这里把它撤掉，
    // 让开关回落到服务端值，而不是停在"看起来改了其实没写进去"的假状态。
    void onSave({ [k]: next }).catch(() => {
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

  if (!g) {
    return (
      <>
        <NavHead title="设置" onBack={onExit} />
        <div className="form" id="setMenu">
        {MENU.map(({ section, items }) => {
          const rows = items.filter((it) => GROUPS[it.id])
          if (!rows.length) return null
          return (
            <div className="msec" key={section}>
              <div className="fsect">{section}</div>
              <div className="fsec">
                {rows.map((it) => (
                  <div className="frow menurow" key={it.id} onClick={() => onOpen(it.id)}>
                    <span className="k">{it.title}</span>
                    <span className="chev">›</span>
                  </div>
                ))}
              </div>
            </div>
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
      <div className="form" id="setForm">
        {g.id === 'access' ? (
          <AccessKeyPanel access={access} />
        ) : (
          <FGroup title={g.title} id={g.id}>
            {g.fields.map((f) => (
              <Field key={f.key} f={f} editable={editable} onSave={onSave}
                on={on} onToggle={onToggle} pending={pending} />
            ))}
            {g.id === 'tgbot' && <TgbotStatus editable={editable} />}
            {g.notes?.map((n) => <NoteRow key={n.t} t={n.t}>{n.b}</NoteRow>)}
          </FGroup>
        )}
      </div>
    </>
  )
}
