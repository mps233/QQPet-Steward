import { useEffect, useState } from 'react'
import type { SchedulerInfo } from '../api'
import { advanceUptime, formatUptime, parseUptimeSeconds } from './format'

export function useUptime({ alive, pid, uptime: rawUptime }: SchedulerInfo): string {
  const uptime = rawUptime || ''
  const [display, setDisplay] = useState(() => ({
    alive, pid, uptime, text: formatUptime(uptime),
  }))

  useEffect(() => {
    const sampledAt = Date.now()
    const update = () => setDisplay({
      alive, pid, uptime, text: advanceUptime(uptime, Date.now() - sampledAt),
    })
    update()
    if (!alive || parseUptimeSeconds(uptime) === null) return

    // Elapsed wall time catches up after background timer throttling.
    const timer = window.setInterval(update, 1000)
    const onVisible = () => {
      if (!document.hidden) update()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [alive, pid, uptime])

  // Do not show the previous process's clock while a new sample takes effect.
  return display.alive === alive && display.pid === pid && display.uptime === uptime
    ? display.text
    : formatUptime(uptime)
}
