import { useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchLogs,
  fetchRewards,
  type RewardBucket,
  type RewardRow,
  type RewardsData,
  type Shot,
} from '../api'
import { usePoll } from '../lib/usePoll'
import { Switch } from './form'

const RW_ICON: Record<string, string> = {
  school: '/qp-icons/official/study_book.png',
  work: '/qp-icons/official/work_coin.png',
}

/** "力量+5 智力+2" → ["力量 +5", "智力 +2"]；解析不出的原样保留 */
const attrList = (a: string | undefined): string[] => (a || '').trim().split(/\s+/)
  .filter(Boolean)
  .map((s) => s.replace(/^([^\d+-]+)([+-]\d+)$/, '$1 $2'))

const attrSummary = (a: Record<string, number> | undefined): string[] =>
  ['力量', '智力', '魅力']
    .filter((k) => a && a[k])
    .map((k) => `${k} +${a?.[k] ?? 0}`)

/** 解析不出数值时**明说**，不拿 +0 冒充"这次没收益" */
const miss = (a: number | undefined, b: number | undefined): string =>
  (a || 0) < (b || 0) ? `（${(b || 0) - (a || 0)} 次未解析）` : ''

const dateLabel = (dt: string, today: string, yesterday: string): string =>
  dt === today ? '今天' : dt === yesterday ? '昨天' : dt.slice(5).replace('-', '/')

function StatRow({
  icon,
  label,
  value,
  unit,
  detail,
}: {
  icon: string
  label: string
  value: number
  unit: string
  detail: string[]
}) {
  return (
    <div className="lg-stat">
      <img className="lg-stat-ico" src={icon} alt="" />
      <div className="lg-stat-main">
        <div className="lg-stat-top">
          <span className="lg-stat-k">{label}</span>
          <span className="lg-stat-v">
            {value}
            <small>{unit}</small>
          </span>
        </div>
        <p className={'lg-stat-d' + (detail.length ? '' : ' muted')}>
          {detail.length ? detail.join(' · ') : '—'}
        </p>
      </div>
    </div>
  )
}

function RewardSummary({ d }: { d: RewardsData }) {
  const s: RewardBucket = d.school
  const w: RewardBucket = d.work
  const gotSchool = (s.credits_n || 0) > 0
  const gotWork = (w.coins_n || 0) > 0
  const schoolDetail: string[] = []
  const workDetail: string[] = []
  if (s.sessions) {
    schoolDetail.push(gotSchool
      ? `学分 +${s.credits || 0}${miss(s.credits_n, s.sessions)}`
      : `学分 —${miss(0, s.sessions)}`)
    schoolDetail.push(...attrSummary(s.attrs))
  }
  if (w.sessions) {
    workDetail.push(gotWork
      ? `金币 +${w.coins || 0}${miss(w.coins_n, w.sessions)}`
      : `金币 —${miss(0, w.sessions)}`)
    if ((w.workpoints_n || 0) > 0) workDetail.push(`工分 +${w.workpoints || 0}`)
    // 广告加成（结算页「看视频获得 N 金币」）：单独一笔，不计进上面的金币
    if ((w.ad_coins_n || 0) > 0) workDetail.push(`看视频 +${w.ad_coins || 0}`)
  }
  const tired = (s.tired || 0) + (w.tired || 0)
  return (
    <div className="lg-sum" id="rwSum">
      <StatRow icon={RW_ICON.school ?? ''} label="学习" value={s.sessions || 0} unit=" 节" detail={schoolDetail} />
      <StatRow icon={RW_ICON.work ?? ''} label="打工" value={w.sessions || 0} unit=" 次" detail={workDetail} />
      {tired ? (
        <div className="lg-tired">
          <i className="lg-dot" aria-hidden="true" />
          疲惫 {tired} 次 · 收益减少
        </div>
      ) : null}
    </div>
  )
}

function RewardRowItem({ r, tsLab }: { r: RewardRow; tsLab: boolean }) {
  const [ts, kind, title, credits, attrs, coins, tired, wp, pay] = r
  const isS = kind === 'school'
  const value = isS
    ? (credits != null ? `学分 +${credits}` : '—')
    : (coins != null ? `+${coins}` : '—')
  const tags = attrList(attrs)
  if (!isS && wp != null) tags.push(`工分 +${wp}`)
  return (
    <div className="lg-rrow">
      <span className="lg-rtime">
        <img src={RW_ICON[kind] ?? ''} alt="" />
        <span>{tsLab ? ts : (ts || '').slice(6)}</span>
      </span>
      <div className="lg-rmain">
        <span className="lg-rtitle">{title || (isS ? '学习' : '打工')}</span>
        {tags.length || tired ? (
          <span className="lg-rtags">
            {tags.map((t, i) => <i className="lg-tag" key={`${t}-${i}`}>{t}</i>)}
            {tired ? <i className="lg-tag warn">疲惫</i> : null}
          </span>
        ) : null}
      </div>
      <span
        className={'lg-rval' + (!isS && coins != null ? ' pos' : '') + (value === '—' ? ' zero' : '')}
        title={pay || undefined}
      >
        {value}
      </span>
    </div>
  )
}

/** 异常截图：默认只露最近 3 张，展开后才是整墙缩略图 */
function ShotsCard({ shots }: { shots: Shot[] }) {
  const [open, setOpen] = useState(false)
  if (!shots.length) return null
  const shown = open ? shots : shots.slice(0, 3)
  return (
    <section className="lg-sec" id="shotCard">
      <div className="lg-sec-head">
        <h3>异常截图</h3>
        <span className="lg-chip">{shots.length}</span>
      </div>
      <div className="lg-card">
        <div className="lg-shots" id="shots">
          {shown.map((s) => (
            <a className="lg-shot" key={s.name} href={`/files/${encodeURIComponent(s.name)}`} target="_blank" rel="noreferrer">
              <img loading="lazy" src={`/files/${encodeURIComponent(s.name)}`} alt={`异常截图 ${s.mtime}`} />
              <span className="lg-shot-cap">{s.mtime}</span>
            </a>
          ))}
        </div>
        {shots.length > 3 ? (
          <button className={'lg-more' + (open ? ' open' : '')} type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? '收起' : `展开全部 ${shots.length} 张`}
          </button>
        ) : null}
        <p className="lg-footnote">异常发生时自动保存，点缩略图查看原图。</p>
      </div>
    </section>
  )
}

function ScopeCard({ d, setDate }: { d: RewardsData; setDate: (v: string) => void }) {
  const who = d.pet ? `${d.pet}${d.owner ? ` · ${d.owner}` : ''}` : ''
  const tn = `今日 学习 ${d.today_school?.sessions || 0} 节 / 打工 ${d.today_work?.sessions || 0} 次`
  return (
    <section className="lg-sec">
      <h3>统计范围</h3>
      <div className="lg-card">
        <div className="lg-field">
          <div className="lg-field-head">
            <label className="lg-label" htmlFor="rwDate">日期</label>
          </div>
          <select
            id="rwDate"
            className="lg-select"
            aria-label="统计范围"
            value={d.date}
            onChange={(e) => setDate(e.target.value)}
          >
            {(d.dates || []).map((dt) => (
              <option key={dt} value={dt}>
                {dateLabel(dt, d.today, d.yesterday)}
              </option>
            ))}
            <option value="all">全部历史</option>
          </select>
          <div className="lg-meta" id="rwMeta">
            {who ? <span>{who}</span> : null}
            <span>共 {d.n || 0} 次</span>
            {d.updated ? <span>更新 {d.updated}</span> : null}
          </div>
          {d.date !== d.today ? <p className="lg-hint">{tn}</p> : null}
        </div>
      </div>
    </section>
  )
}

/** 收益记录子页：统计范围下拉 + 汇总 + 按次记录 */
function RewardTab({ shots }: { shots: Shot[] }) {
  const [date, setDate] = useState<string>('')

  // 15 秒轮询；切换统计范围（date）时立刻重拉
  const { data: d, error: err } = usePoll(() => fetchRewards(date || undefined), 15000, [date])

  if (err) {
    return (
      <section className="lg-sec">
        <h3>收益记录</h3>
        <div className="lg-card"><p className="lg-empty">读取收益记录失败：{err}</p></div>
      </section>
    )
  }
  if (!d) {
    return (
      <section className="lg-sec">
        <h3>收益记录</h3>
        <div className="lg-card"><p className="lg-empty">加载中…</p></div>
      </section>
    )
  }

  if (!d.ok) {
    return (
      <>
        <section className="lg-sec">
          <h3>收益记录</h3>
          <div className="lg-card">
            <p className="lg-empty">
              还没有收益记录。调度器跑完一节学习 / 一次打工、检测到结算页后就会自动出现在这里。
            </p>
          </div>
        </section>
        <ShotsCard shots={shots} />
      </>
    )
  }

  const tsLab = d.date === 'all'
  const rows = [...(d.recent || [])].reverse()
  return (
    <>
      <ScopeCard d={d} setDate={setDate} />
      <section className="lg-sec">
        <h3>收益汇总</h3>
        <div className="lg-card"><RewardSummary d={d} /></div>
      </section>
      <section className="lg-sec">
        <h3>按次记录</h3>
        <div className="lg-card">
          <div className="lg-rlist" id="rwList">
            {rows.length ? (
              rows.map((r, i) => <RewardRowItem key={`${r[0]}-${i}`} r={r} tsLab={tsLab} />)
            ) : (
              <p className="lg-empty">{d.date === 'all' ? '还没有收益记录。' : '这一天还没有收益记录。'}</p>
            )}
          </div>
          {/* 后端 recent 只留最近 300 条；不说明时「共 501 次」与列表行数对不上 */}
          {d.n > rows.length ? (
            <p className="lg-footnote">共 {d.n} 条，此处只列最近 {rows.length} 条。</p>
          ) : null}
        </div>
      </section>
      <ShotsCard shots={shots} />
    </>
  )
}

const LOG_TIME_RE = /^\[(\d{2}:\d{2}:\d{2})\]\s?(.*)$/
const LOG_ERR_RE = /失败|错误|异常|Traceback|Exception|Error/i
const LOG_WARN_RE = /警告|超时|重试|WARN/i

function LogLines({ lines }: { lines: string[] }) {
  return (
    <>
      {lines.map((line, i) => {
        const m = LOG_TIME_RE.exec(line)
        const body = m ? (m[2] ?? '') : line
        const tone = LOG_ERR_RE.test(body) ? ' err' : LOG_WARN_RE.test(body) ? ' warn' : ''
        return (
          <div className={'lg-line' + tone} key={i}>
            {m ? <span className="lg-time">{m[1]}</span> : null}
            <span className="lg-msg">{body || '\u00a0'}</span>
          </div>
        )
      })}
    </>
  )
}

/** 实时日志子页：过滤 + 自动滚动 + 输出 + 异常截图 */
function LiveTab({ shots }: { shots: Shot[] }) {
  const [auto, setAuto] = useState(true)
  const [filter, setFilter] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  // 3 秒轮询。**失败静默**（不渲染 error）：调度器没在跑时日志读不到是常态，
  // 保持上一份即可，不该显示成一次"读取失败"
  const { data: logs } = usePoll(fetchLogs, 3000)

  const lines = useMemo(() => logs?.lines ?? [], [logs])
  const shown = useMemo(
    () => (filter ? lines.filter((l) => l.includes(filter)) : lines),
    [lines, filter],
  )

  useEffect(() => {
    if (auto && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [shown, auto])

  const empty = !lines.length
    ? (logs ? '当前还没有日志输出。' : '正在读取日志…')
    : filter && !shown.length
      ? `没有匹配「${filter}」的日志行。`
      : ''

  return (
    <>
      <section className="lg-sec">
        <h3>调度器输出</h3>
        <div className="lg-card">
          <div className="lg-field">
            <div className="lg-field-head">
              <label className="lg-label" htmlFor="logFilter">过滤关键字</label>
              {filter ? (
                <button className="lg-clear" type="button" onClick={() => setFilter('')}>清除</button>
              ) : null}
            </div>
            <input
              id="logFilter"
              className="lg-input"
              type="text"
              value={filter}
              placeholder="只看包含某个词的行…"
              autoComplete="off"
              spellCheck={false}
              enterKeyHint="search"
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          <div className="lg-row">
            <div className="lg-row-main">
              <span className="lg-row-label">自动滚动</span>
              <span className="lg-row-desc">新日志出现时滚到底部；向上翻记录时可以关掉</span>
            </div>
            <Switch on={auto} onToggle={() => setAuto((v) => !v)} title="自动滚动到底部" />
          </div>
          <div className={'lg-console' + (empty ? ' is-empty' : '')} id="logbox" ref={boxRef} tabIndex={0} aria-label="调度器日志">
            {empty ? <p className="lg-empty">{empty}</p> : <LogLines lines={shown} />}
          </div>
          <div className="lg-foot" id="logMeta">
            <span>{logs?.name || ''}</span>
            <span>
              {logs ? (filter ? `${shown.length} / ${logs.total} 行` : `共 ${logs.total} 行`) : ''}
            </span>
          </div>
        </div>
      </section>
      <ShotsCard shots={shots} />
    </>
  )
}

/** 日志页：两个子页（实时日志 / 收益记录），切换条常驻上方。
 *  层级同设置页二级：子页是历史栈里的**真一层**（URL `?tab=log&sub=reward`），
 *  所以侧滑返回退回"实时日志"、再滑才回总览；`sub` 由 App 从 URL 派生后传进来
 *  （早先这里自持 state 不压栈，右滑会直接退出日志页）。 */
export function LogPage({
  shots,
  sub,
  onSub,
}: {
  shots: Shot[]
  /** 当前子页键（URL 的 sub 参数；null = 默认实时日志） */
  sub: string | null
  onSub: (k: 'index' | 'reward') => void
}) {
  const cur = sub === 'reward' ? 'reward' : 'index'
  return (
    <div className="log-page">
      <div className="lg-tabs" role="tablist" aria-label="日志子页">
        <button
          data-lsub="index"
          role="tab"
          aria-selected={cur === 'index'}
          className={cur === 'index' ? 'on' : ''}
          title="调度器全部输出"
          onClick={() => onSub('index')}
        >
          实时日志
        </button>
        <button
          data-lsub="reward"
          role="tab"
          aria-selected={cur === 'reward'}
          className={cur === 'reward' ? 'on' : ''}
          title="每次学习/打工结算拿到的学分·属性·金币"
          onClick={() => onSub('reward')}
        >
          收益记录
        </button>
      </div>
      {cur === 'index' ? <LiveTab shots={shots} /> : <RewardTab shots={shots} />}
    </div>
  )
}
