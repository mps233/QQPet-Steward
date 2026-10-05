import { useEffect, useState } from 'react'
import { GROUPS, MENU, resolveOpts, type FieldDef } from '../lib/settings-schema'
import { FGroup, FRow, Switch } from './form'
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

/** 设置页：一级菜单（4 个分区 / 17 个二级页）→ 二级表单。
 *  旧版 是把所有字段渲染进 `#setForm` 再用 `.hide` 切组，这边**只渲染当前组**。
 *
 *  **grp 由 App 从 URL 派生后传进来**（不再在这里自持 state + replaceState）：
 *  二级必须是历史栈里的**真一层**，侧滑返回才能退回一级菜单 —— 早先用
 *  replaceState 改写当前条目，栈里只有"总览→设置"两条，右滑一下就直接回总览
 *  （用户实报）。返回按钮同样走 `onBackSub()`（history.back()），与手势同一条路。 */
export function SettingsPage({
  editable,
  onSave,
  onExit,
  grp,
  onOpen,
  onBackSub,
}: {
  editable: Record<string, unknown>
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
        <FGroup title={g.title} id={g.id}>
          {g.fields.map((f) => (
            <Field key={f.key} f={f} editable={editable} onSave={onSave} />
          ))}
        </FGroup>
      </div>
    </>
  )
}
