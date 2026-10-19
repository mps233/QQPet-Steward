import { fetchPlan } from '../api'
import { usePoll } from '../lib/usePoll'
import { CareerTree } from './CareerTree'
import type { Trio } from '../lib/career-tree'

const numOf = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const boolOf = (v: unknown): boolean => !!v

/**
 * 职业树页（原「职业解锁计划」）。
 *
 * **页面只有职业树本体** —— 照官方 360dp 画布逐像素复刻，规格见
 * `docs/CAREER-TREE-OFFICIAL.md`。
 *
 * 原先这里还挂着「职业数据」折叠区（进度条 / 隐蔽哨兵 / 进度录入 / 阶梯路线 / 8 线状态），
 * 2026-10-19 用户要求整块删掉、准备重做。备份在
 * `backup/career-data-section-<日期>/`（含 PlanPage.tsx / CareerTree.tsx / career-tree.* /
 * dashboard.py / src/career_watch.py，以及 runs 数据快照）。
 *
 * **数据链没动**：后端 `/api/plan` 照旧返回 `lines` / `steps` / `watch`，
 * 职业树的三态（彩色 / 带锁彩色 / 纯灰）靠哨兵逐格实测的 `tree_tiers`。
 * 只是前端不再渲染那五块。
 */
export function PlanPage() {
  // 20 秒轮询：哨兵每次检查完会更新 tiers，前端跟着刷新
  const { data: d, error: err } = usePoll(fetchPlan, 20000)

  if (err) return <div className="empty">读取职业树失败：{err}</div>
  if (!d) return <div className="empty">加载中…</div>

  const attrs: Trio = [numOf(d.values['力']), numOf(d.values['智']), numOf(d.values['魅'])]
  const gates = {
    points: numOf(d.values['工分']),
    coins: numOf(d.values['金币']),
    jrGrad: boolOf(d.values['初级毕业']),
    midGrad: boolOf(d.values['中级毕业']),
  }

  return (
    <CareerTree
      attrs={attrs}
      treeLines={d.tree_lines ?? {}}
      treeTiers={d.tree_tiers ?? {}}
      equipped={d.equipped ?? null}
      gates={gates}
    />
  )
}
