import { useEffect, useState } from 'react'
import type { Data } from '../api'
import { countdownSeconds } from './format'

interface CountdownValues {
  nextRemaining: number | null
  workRemaining: number | null
}

function countdownsAt(data: Data, sampledAt: number, now: number): CountdownValues {
  const remaining = data.work_eta?.remaining
  const workTarget = typeof remaining === 'number' && Number.isFinite(remaining)
    ? sampledAt / 1000 + Math.max(0, remaining)
    : null
  return {
    nextRemaining: countdownSeconds(data.queue.next_ts, now),
    workRemaining: countdownSeconds(workTarget, Math.max(sampledAt, now)),
  }
}

export function useCountdowns(data: Data): CountdownValues {
  const [clock, setClock] = useState(() => {
    const now = Date.now()
    return { data, ...countdownsAt(data, now, now) }
  })

  useEffect(() => {
    const sampledAt = Date.now()
    const update = () => {
      const values = countdownsAt(data, sampledAt, Date.now())
      setClock(prev => prev.data === data
        && prev.nextRemaining === values.nextRemaining
        && prev.workRemaining === values.workRemaining
        ? prev
        : { data, ...values })
    }
    update()
    const initial = countdownsAt(data, sampledAt, sampledAt)
    if (!data.scheduler.alive || (initial.nextRemaining === null && initial.workRemaining === null)) return

    const timer = window.setInterval(update, 1000)
    const onVisible = () => {
      if (!document.hidden) update()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // Each API snapshot recalibrates the activity's relative remaining time.
  }, [data])

  if (clock.data === data) return clock
  const now = Date.now()
  return countdownsAt(data, now, now)
}
