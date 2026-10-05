import { useCallback, useEffect, useRef, useState } from 'react'

export interface PollResult<T> {
  data: T | null
  error: string
  /** 手动立刻重拉一次（保存设置 / 识别完成后要马上看到新值） */
  reload: () => void
}

/**
 * 统一轮询：立刻拉一次 → 每 intervalMs 再拉。
 *
 * 原先四处各写一遍 `useEffect + alive 旗标 + setInterval`
 * （总览页 / 冒险记录 / 职业计划 / 日志的两个子页），这里收敛成一处，
 * 并补上原来都没有的三件事：
 *
 *  - **失败保留上一份数据**，只置 error —— 网络抖一下界面不会空掉
 *  - **在途去重**：上一轮没回来就跳过这一轮（3 秒的日志刷新撞上慢 `adb`/OCR 时，
 *    不会把请求堆成雪崩）
 *  - **后台暂停 / 回前台补拉**：手机 PWA 切后台不再每 6 秒白打一次接口
 *
 * 失败怎么显示由调用方决定（日志子页静默、其余页渲染 error），本 hook 只如实上报。
 * **流媒体心跳不走这里**（LivePage）：它每轮还要 POST 保活、量延迟追帧、检测接管失效，
 * 是状态机而不是取数。
 */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  /** 请求参数依赖：变化时立刻重拉并重建定时器（如日期下拉切换） */
  deps: unknown[] = [],
): PollResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [tick, setTick] = useState(0)

  // fetcher 每次渲染都是新函数：用 ref 持最新一份，effect 才不会每渲染重建
  const fRef = useRef(fetcher)
  fRef.current = fetcher

  const reload = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    let alive = true
    let inflight = false
    const run = async () => {
      if (inflight || document.hidden) return
      inflight = true
      try {
        const r = await fRef.current()
        if (alive) {
          setData(r)
          setError('')
        }
      } catch (e) {
        if (alive) setError(String(e))
      } finally {
        inflight = false
      }
    }
    void run()
    const t = window.setInterval(run, intervalMs)
    const onVis = () => {
      if (!document.hidden) void run()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      alive = false
      window.clearInterval(t)
      document.removeEventListener('visibilitychange', onVis)
    }
    // deps 由调用方给（= 请求参数）。展开写法 React 支持；exhaustive-deps
    // 规则会抱怨非静态依赖数组，故对下一行豁免。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps])

  return { data, error, reload }
}
