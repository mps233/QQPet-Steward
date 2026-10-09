import { useEffect, useState } from 'react'
import { changeAccessKey, type AccessInfo } from '../api'
import { GROUPS, MENU, resolveOpts, type FieldDef } from '../lib/settings-schema'
import { FGroup, FRow, NoteRow, Switch } from './form'
import { NavHead } from './NavHead'

const strOf = (ed: Record<string, unknown>, k: string): string => {
  const v = ed[k]
  return v === null || v === undefined ? '' : String(v)
}
const boolOf = (ed: Record<string, unknown>, k: string): boolean => !!ed[k]

/** 单个字段的控件（按 kind 分派）。**改动即保存**，与 旧版 一致。 */
function Field({ f, editable, onSave }: {
  f: FieldDef
  editable: Record<string, unknown>
  onSave: (u: Record<string, unknown>) => Promise<void>
}) {
  const [local, setLocal] = useState<string>(() => strOf(editable, f.key))
  const [localBool, setLocalBool] = useState<boolean>(() => boolOf(editable, f.key))
  /** 依赖的开关没开 → 本项置灰不可改（如"等他空闲时"依赖"等TA空闲"：
   *  没开"等TA空闲"，也就没有什么"等他空闲时干什么"可言） */
  const locked = !!f.enabledWhen && !boolOf(editable, f.enabledWhen)

  // 后端数据回来时同步（例如别处改了同一项）
  useEffect(() => {
    setLocal(strOf(editable, f.key))
    setLocalBool(boolOf(editable, f.key))
  }, [editable, f.key])

  if (f.kind === 'bool') {
    return (
      <FRow k={f.label} tip={f.tip}>
        <Switch
          on={localBool}
          title={f.tip}
          onToggle={() => {
            const next = !localBool
            setLocalBool(next)
            void onSave({ [f.key]: next })
          }}
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
    <FRow k={f.label} tip={f.tip}>
      <span className="ctrl">
        <input
          type={f.kind === 'int' ? 'number' : 'text'}
          value={local}
          disabled={locked}
          placeholder={f.placeholder}
          style={f.w ? { width: `calc(var(--u) * ${f.w})` } : undefined}
          onChange={(e) => setLocal(e.target.value)}
          onBlur={() => {
            if (local !== strOf(editable, f.key)) void onSave({ [f.key]: local })
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

/** 设置页：一级菜单（4 个分区 / 17 个二级页）→ 二级表单。
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
              <Field key={f.key} f={f} editable={editable} onSave={onSave} />
            ))}
          </FGroup>
        )}
      </div>
    </>
  )
}
