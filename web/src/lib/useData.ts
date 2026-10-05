import { fetchData, type Data } from '../api'
import { usePoll } from './usePoll'

/** 总览页的数据源：轮询 `/api/data`（默认 6 秒一次，与旧界面一致）。
 *  失败保留上一份数据、只把错误抛出 —— 实现见 usePoll。 */
export function useData(intervalMs = 6000) {
  return usePoll<Data>(fetchData, intervalMs)
}
