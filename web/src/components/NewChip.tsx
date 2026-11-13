/** 「新功能」NEW 标记（注册表见 lib/whats-new.ts）。
 *
 *  两种形态：
 *   - `NewTag`：不可点，用在**本身是 `<button>` 的容器**里 —— 设置页一级菜单行。
 *     HTML 不允许 button 嵌 button，而且点它会把点击冒泡成"打开这个分组"。
 *   - `NewChip`：可点，点一下 = 已读（同一功能在其它页面的标记一起消失）。
 *     任务行挂了 dnd-kit 的长按拖拽，所以按住/点击都要 stopPropagation，
 *     否则长按 NEW 会变成拖动整行。
 */

export function NewTag({ title }: { title: string }) {
  return (
    <span className="newchip" title={title}>
      NEW
    </span>
  )
}

export function NewChip({ title, onDismiss }: {
  /** 说明文字（悬浮/无障碍标签用） */
  title: string
  onDismiss: () => void
}) {
  return (
    <button
      type="button"
      className="newchip"
      title={`${title}（点一下移除 NEW 标记）`}
      aria-label={`新功能：${title}，点一下移除 NEW 标记`}
      onPointerDown={(e) => e.stopPropagation()}
      onTouchStart={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation()
        onDismiss()
      }}
    >
      NEW
    </button>
  )
}
