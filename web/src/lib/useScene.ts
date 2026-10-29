import { useCallback, useEffect, useState } from 'react'
import {
  applyScene,
  computeScene,
  readManualScene,
  SCENE_ORDER,
  sceneName,
  writeManualScene,
} from './scene'
import { showToast } from './toast'

/** 房间背景状态。
 *
 *  **手动选择必须暂停"按当前任务自动换背景"** —— 否则每 6 秒一次的数据刷新会立刻把
 *  背景改回任务场景，用户看到的就是"点了没用、自己弹回去"（旧版 踩过）。
 *  选择存 `localStorage('qpet_scene')`，选回「自动」才恢复跟随。
 *
 *  **`dark` 由调用方传入**（App 里 `useTheme()` 的解析结果），本 hook 不再自己监听
 *  `matchMedia`：主题现在有「跟随系统 / 浅色 / 深色」三态，背景换图必须跟主题按钮
 *  用的是同一个解析结果，否则手动切深色时房间背景不会跟着换。 */
export function useScene(curKey: string, etaKind: string, dark: boolean) {
  const [manual, setManual] = useState<string>(() => readManualScene())

  const scene = manual === 'auto' ? computeScene(curKey, etaKind) : manual

  useEffect(() => {
    applyScene(scene, dark)
  }, [scene, dark])

  const select = useCallback((key: string, quiet = false) => {
    setManual(key)
    writeManualScene(key)
    if (!quiet) showToast(sceneName(key), { icon: 'paw' })
  }, [])

  /** 长按圆钮：直接切下一张（`auto` 时从第一张开始） */
  const cycle = useCallback(() => {
    const list = ['auto', ...SCENE_ORDER]
    const i = list.indexOf(manual)
    const next = list[(i + 1) % list.length] ?? 'auto'
    setManual(next)
    writeManualScene(next)
    showToast(sceneName(next), { icon: 'paw' })
  }, [manual])

  return { manual, scene, select, cycle, dark }
}
