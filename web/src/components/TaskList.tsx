import {
  DndContext,
  MouseSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { restrictToParentElement, restrictToVerticalAxis } from '@dnd-kit/modifiers'
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { useEffect, useMemo, useState } from 'react'
import { applyOptim, effectiveValue, reconcile, rollbackOptim } from '../lib/optimistic'
import { taskListSnapshot } from '../lib/task-list'
import {
  TASK_NAME,
  isLoopTask,
  keyOfTaskName,
  type Data,
  type QueueTaskState,
} from '../api'

/** 行右侧状态文字（与 旧版 的 rowOf 一致：只给"有信息量"的几种写字）。
 *  `active` = 这一行就是调度器此刻正在跑的任务 —— 后端 tasks[k].state 只有
 *  disabled/dead/ready/waiting/done，**从不写 run**，所以"执行中"必须靠
 *  queue.current 认，不能靠 state（否则这个分支永远命中不了）。 */
function statusText(
  t: QueueTaskState | undefined,
  nowDate: string,
  active: boolean,
  pending: boolean,
) {
  if (pending) return { cls: 'run', text: '待结算' }
  if (active) return { cls: 'run', text: '执行中' }
  const st = t?.state ?? ''
  if (st === 'disabled') return { cls: 'off-t', text: '已禁用' }
  if (st === 'run' || st === 'running') return { cls: 'run', text: '执行中' }
  if (st === 'waiting') {
    const nx = t?.next
    if (!nx) return { cls: '', text: '等待' }
    const cross = nx.slice(0, 10) === nowDate ? '' : '明 '
    return { cls: '', text: `等待 ${cross}${nx.slice(11, 16)}` }
  }
  if (st === 'done') return { cls: 'done', text: '' }
  if (st === 'dead') return { cls: 'dead-t', text: '今日结束' }
  return { cls: '', text: '' }
}

function RowBody({
  k,
  t,
  pendingKey,
  activeKey,
  nowDate,
  onToggle,
  on,
}: {
  k: string
  t: QueueTaskState | undefined
  pendingKey: string
  /** 正在执行的任务键（含 pending 收尾中的那个） */
  activeKey: string
  nowDate: string
  /** 点开关切换该任务启用状态（写回 `<k>_enabled`） */
  /** 返回 Promise —— 失败时调用方要回滚乐观值（见 handleToggle），不能丢弃 */
  onToggle: (k: string, on: boolean) => Promise<unknown>
  /** 当前是否启用 —— 由 TaskList 算好（含"刚点下去、请求还没回来"的乐观值） */
  on: boolean
}) {
  const isPend = !!pendingKey && k === pendingKey
  const isActive = !!activeKey && k === activeKey
  const det = statusText(t, nowDate, isActive && !isPend, isPend)

  return (
    <>
      <span className={'mname' + (on ? '' : ' off')}>{TASK_NAME[k] ?? k}</span>
      <span className="mright">
        {det.text || det.cls === 'done' ? (
          <span
            className={'mdet' + (det.cls ? ` ${det.cls}` : '')}
            title={det.cls === 'done' ? '今日完成' : undefined}
          >
            {det.cls === 'done' ? (
              <svg className="mcheck" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M4.2 8.2 6.9 10.8 11.9 5.2" />
              </svg>
            ) : det.text}
          </span>
        ) : null}
      </span>
      <span
        className={'mcb' + (on ? ' on' : '')}
        data-k={k}
        // 开关：阻止冒泡，免得 dnd-kit 挂在行上的 listeners 把这次按下当成拖拽起手
        // （旧版 是在 pointerdown 里 `if(e.target.closest('.mcb')) return` 做同样的事）
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation()
          onToggle(k, !on)
        }}
      />
    </>
  )
}

function rowClass(
  k: string,
  t: QueueTaskState | undefined,
  activeKey: string,
) {
  const st = t?.state ?? ''
  const done = st === 'done' || st === 'dead'
  const isActive = !!activeKey && k === activeKey
  return 'mrow' + (done ? ' done' : '') + (isActive ? ' run' : '') + (st === 'disabled' ? ' off' : '')
}

/** 「任务顺序」组里的一行：用 dnd-kit 的 useSortable 包一层。
 *  listeners 绑在整行上，但传感器设了 220ms 长按门槛 —— 短按仍然是普通点击
 *  （开关/行内按钮照常工作），长按才进入拖拽。这替掉了 旧版 那套手写的
 *  pointerdown + 220ms 定时器实现。 */
function SortableRow(props: {
  k: string
  t: QueueTaskState | undefined
  pendingKey: string
  activeKey: string
  nowDate: string
  /** 返回 Promise —— 失败时调用方要回滚乐观值（见 handleToggle），不能丢弃 */
  onToggle: (k: string, on: boolean) => Promise<unknown>
  on: boolean
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: props.k,
  })
  return (
    <div
      ref={setNodeRef}
      // 'sortable' 这个 class 是给 CSS 用的（.mrow.sortable{touch-action:none}）——
      // touch-action 必须在触摸开始前就声明好，不能等 onDragStart 再加。
      className={rowClass(props.k, props.t, props.activeKey) + ' sortable' + (isDragging ? ' dragging' : '')}
      data-k={props.k}
      style={{
        transform: transform ? `translate3d(0, ${Math.round(transform.y)}px, 0)` : undefined,
        transition,
        zIndex: isDragging ? 9 : undefined,
      }}
      {...attributes}
      {...listeners}
    >
      <RowBody {...props} />
    </div>
  )
}

function StaticRow(props: {
  k: string
  t: QueueTaskState | undefined
  pendingKey: string
  activeKey: string
  nowDate: string
  /** 返回 Promise —— 失败时调用方要回滚乐观值（见 handleToggle），不能丢弃 */
  onToggle: (k: string, on: boolean) => Promise<unknown>
  on: boolean
}) {
  return (
    <div className={rowClass(props.k, props.t, props.activeKey)} data-k={props.k}>
      <RowBody {...props} />
    </div>
  )
}

/** 任务列表：分两组 —— 「日常轮巡」= 循环类，顺序固定不可拖；
 *  「任务顺序」= 其余，长按拖动排序，松手写回 tasks.order / tasks.main_order。 */
export function TaskList({
  data,
  onReorder,
  onToggle,
}: {
  data: Data
  onReorder: (order: string[]) => void
  /** 点开关切启用 —— 对应 旧版 里挂在 #taskList 上的那个 click 委托 */
  /** 返回 Promise —— 失败时调用方要回滚乐观值（见 handleToggle），不能丢弃 */
  onToggle: (k: string, on: boolean) => Promise<unknown>
}) {
  const qt = useMemo(() => taskListSnapshot(
    data.queue.tasks,
    data.config?.task_order,
    data.config?.tasks_enabled,
    Object.keys(TASK_NAME),
  ), [data.queue.tasks, data.config])
  const nowDate = (data.now || '').slice(0, 10)
  // hire_friend / friend_care 已分别并入 work / care，但后端 pending 仍按旧键登记；
  // 不映射的话列表里没有对应的行，进行中的那一项永远不会高亮。
  const rawPendingKey = data.queue.pending ? data.queue.pending_key || '' : ''
  const pendingKey = rawPendingKey === 'hire_friend' ? 'work'
    : rawPendingKey === 'friend_care' ? 'care'
      : rawPendingKey
  // 正在执行的任务行：pending 时后端直接给任务键，平时 queue.current 是中文名要反查
  const activeKey = pendingKey || keyOfTaskName(data.queue.current)
  const keys = Object.keys(qt)
  const loop = keys.filter(isLoopTask)
  const rest = keys.filter((k) => !isLoopTask(k))

  // **触摸用 TouchSensor、鼠标用 MouseSensor**（dnd-kit 官方推荐的组合）。
  // 原来只用 PointerSensor —— iOS 上它在滚动容器（.page-slot / .home）里会被浏览器的
  // 手势判定截断，按住不动也收不到 move，拖拽完全没反应（用户实报"拖动逼用没有"）。
  // TouchSensor 走 touchstart/touchmove，不经过那套 pointer 判定；长按 220ms 激活，
  // 短按仍是普通点击（开关照常）。MouseSensor 给桌面用，移动 6px 激活。
  const sensors = useSensors(
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
  )

  // **乐观更新**：点开关立即翻转，不等 6 秒轮询。
  // 之前点一下要等很久才看到开关变化（用户实报"隔几十秒才出现勾"）——因为界面完全
  // 依赖 /api/data 轮询回来才知道新状态；后端本身只要 10ms（实测），慢在刷新链路。
  // 这里本地先翻转，等 data 里该任务的状态与之一致后自动清掉覆盖。
  const [optim, setOptim] = useState<Record<string, boolean>>({})
  const serverOn = (k: string) => (qt[k]?.state ?? '') !== 'disabled'
  useEffect(() => {
    // 规则抽到 lib/optimistic.ts（可独立验证）；这里只接 React 状态
    setOptim((prev) => reconcile(prev, serverOn))
    // serverOn 依赖 qt，qt 每次 data 更新都会换引用；这里只在"已一致"时清理
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qt])
  const effectiveOn = (k: string) => effectiveValue(optim, k, serverOn)
  /**
   * 点开关：本地先翻转（乐观），成功由轮询对齐后自动清除；**失败必须回滚**。
   *
   * 历史坑：原实现只在"服务端状态与本地一致"时清除覆盖 —— 保存失败时服务端永远不变，
   * 于是 optim[k] **永不消解**，启用状态与真实配置长期不符，直到整页刷新才暴露。
   * 见 docs/review/05-前端设计系统.md H 节。
   */
  const handleToggle = (k: string, on: boolean): Promise<unknown> => {
    setOptim((p) => applyOptim(p, k, on))
    const rollback = () => setOptim((p) => rollbackOptim(p, k))
    // 超时兜底：请求悬挂（网络半死）时不能让它一直挂在错误的启用态上
    const timer = window.setTimeout(rollback, 8000)
    return Promise.resolve(onToggle(k, on))
      .then(() => window.clearTimeout(timer))   // 成功：交给轮询在"已一致"时清除
      .catch((e) => {
        window.clearTimeout(timer)
        rollback()
        throw e
      })
  }

  const handleDragEnd = (e: DragEndEvent) => {
    const { active, over } = e
    if (!over || active.id === over.id) return
    const from = rest.indexOf(String(active.id))
    const to = rest.indexOf(String(over.id))
    if (from < 0 || to < 0) return
    onReorder(arrayMove(rest, from, to))
  }

  return (
    <div className="qpanel">
      <div className="qhead">
        <span className="qtitle">任务列表</span>
        <span className="qpend" id="qPend">
          {data.queue.pending ? <span className="run">{data.queue.pending} 待结算</span> : ''}
        </span>
      </div>
      <div className="tasklist" id="taskList">
        {loop.length ? (
          <section className="tgroup">
            <div className="tghd">
              <span>日常轮巡</span>
              <span className="tghint">固定顺序</span>
            </div>
            <div className="trows">
              {loop.map((k) => (
                <StaticRow
                  key={k}
                  k={k}
                  t={qt[k]}
                  pendingKey={pendingKey}
                  activeKey={activeKey}
                  nowDate={nowDate}
                  onToggle={handleToggle}
                  on={effectiveOn(k)}
                />
              ))}
            </div>
          </section>
        ) : null}

        {rest.length ? (
          <section className="tgroup">
            <div className="tghd">
              <span>任务顺序</span>
              <span className="tghint">长按拖动</span>
            </div>
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              modifiers={[restrictToVerticalAxis, restrictToParentElement]}
              onDragEnd={handleDragEnd}
            >
              <SortableContext items={rest} strategy={verticalListSortingStrategy}>
                <div className="trows">
                  {rest.map((k) => (
                    <SortableRow
                      key={k}
                      k={k}
                      t={qt[k]}
                      pendingKey={pendingKey}
                      activeKey={activeKey}
                      nowDate={nowDate}
                      onToggle={handleToggle}
                      on={effectiveOn(k)}
                    />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
          </section>
        ) : null}
      </div>
    </div>
  )
}
