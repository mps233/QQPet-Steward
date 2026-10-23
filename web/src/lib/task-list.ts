/** Use configured tasks before the scheduler has written its first snapshot. */
export function taskListSnapshot<T extends { state: string }>(
  queueTasks: Record<string, T> | undefined,
  configuredOrder: unknown,
  configuredEnabled: unknown,
  knownKeys: readonly string[],
): Record<string, T | { state: string }> {
  if (queueTasks && Object.keys(queueTasks).length) return queueTasks

  const known = new Set(knownKeys)
  const order = Array.isArray(configuredOrder)
    ? [...new Set(configuredOrder.filter(
      (key): key is string => typeof key === 'string' && known.has(key),
    ))]
    : []
  const keys = order.length ? order : knownKeys
  const enabled = configuredEnabled && typeof configuredEnabled === 'object'
    ? configuredEnabled as Record<string, unknown>
    : {}
  return Object.fromEntries(keys.map((key) => [
    key,
    { state: enabled[key] === false ? 'disabled' : 'ready' },
  ]))
}
