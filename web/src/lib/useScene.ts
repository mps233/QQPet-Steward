import { useCallback, useEffect, useLayoutEffect, useState } from 'react'
import {
  applyScene,
  pickRandomScene,
  RANDOM_KEY,
  RANDOM_ROTATE_MINUTES,
  readManualScene,
  SCENE_ORDER,
  sceneName,
  writeManualScene,
} from './scene'
import { showToast } from './toast'

/** 随机模式多久换一张。 */
const ROTATE_MS = RANDOM_ROTATE_MINUTES * 60 * 1000

/** 房间背景状态。
 *
 *  **「随机」= 每 10 分钟从全部背景里抽一张**（`RANDOM_ROTATE_MINUTES`），页面开着
 *  就会自己换；抽签会排除当前那张，不会连续两次一样。手动选定某张背景时**轮换必须
 *  停掉**，否则定时器会把用户刚选的图换走（旧版「跟随任务」踩过：选了没用、自己弹回去）。
 *
 *  选择存 `localStorage('qpet_scene')`，选回「随机」才恢复轮换。
 *
 *  **`dark` 由调用方传入**（App 里 `useTheme()` 的解析结果），本 hook 不再自己监听
 *  `matchMedia`：主题现在有「跟随系统 / 浅色 / 深色」三态，背景换图必须跟主题按钮
 *  用的是同一个解析结果，否则手动切深色时房间背景不会跟着换。 */
export function useScene(dark: boolean) {
  const [manual, setManual] = useState<string>(() => readManualScene())
  // 随机模式当前这张。先抽好一张，即便 manual 是固定背景也不会白算（开销可忽略）。
  const [randomKey, setRandomKey] = useState<string>(() => pickRandomScene())

  const scene = manual === RANDOM_KEY ? randomKey : manual

  // **只在「随机」模式下轮换**；手动选定的背景不能被定时器改回去。
  // randomKey 也进依赖：每抽到一张就重新计时，保证每张都完整停留 N 分钟
  // （点「随机」重抽时也从头算，不会刚换完又被定时器马上换走）。
  useEffect(() => {
    if (manual !== RANDOM_KEY) return
    const t = window.setInterval(() => setRandomKey((prev) => pickRandomScene(prev)), ROTATE_MS)
    return () => window.clearInterval(t)
  }, [manual, randomKey])

  // **必须是 layout effect**：App 里同步 theme-color 的那个 useLayoutEffect 要读
  // `--qp-statusbar`，而它在本 hook 之后声明、同一提交内按声明顺序执行。
  // 换成普通 useEffect 会晚一拍：换背景后面板里的状态栏带还停在上一张的颜色
  // （面板打开时那条带要跟着压暗，颜色取自这个变量）。
  useLayoutEffect(() => {
    applyScene(scene, dark)
  }, [scene, dark])

  const select = useCallback((key: string, quiet = false) => {
    setManual(key)
    writeManualScene(key)
    // 点「随机」= 立刻重抽一张；已经在随机模式时点它等于手动换一张
    if (key === RANDOM_KEY) setRandomKey((prev) => pickRandomScene(prev))
    if (!quiet) showToast(sceneName(key), { icon: 'paw' })
  }, [])

  /** 长按圆钮：直接切下一张（`random` 时从第一张背景开始，落到 random 时重抽一张） */
  const cycle = useCallback(() => {
    const list = [RANDOM_KEY, ...SCENE_ORDER]
    const i = list.indexOf(manual)
    const next = list[(i + 1) % list.length] ?? RANDOM_KEY
    setManual(next)
    writeManualScene(next)
    if (next === RANDOM_KEY) setRandomKey((prev) => pickRandomScene(prev))
    showToast(sceneName(next), { icon: 'paw' })
  }, [manual])

  return { manual, scene, select, cycle, dark }
}
