/** 开关按钮：`.sw` + 选中态 `.on`（CSS 里就是按钮样式，不是 checkbox） */
export function Switch({
  on,
  onToggle,
  title,
  disabled = false,
}: {
  on: boolean
  onToggle: () => void
  title?: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      className={'sw' + (on ? ' on' : '')}
      title={title}
      aria-pressed={on}
      disabled={disabled}
      onClick={onToggle}
    />
  )
}
