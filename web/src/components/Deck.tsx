import type { Data } from '../api'
import { hms, pad2 } from '../lib/format'

/** 状态图标：跟着"正在做的事"换图标。
 *  **文件名必须与 旧版 的 RUNICON 表逐字一致** —— 这些是官方素材、按名字放在
 *  `/qp-icons/official/` 下，名字写错就是 404、界面上显示成破图/问号
 *  （踩过：我编了 book_pencil.png / earth.png，实际叫 study_book.png / globe.png）。
 *  改这张表前先逐个验：`curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/qp-icons/official/<name>.png` */
const ICON: Record<string, string> = {
  上课: '/qp-icons/official/study_book.png',
  学习: '/qp-icons/official/study_book.png',
  打工: '/qp-icons/official/work_coin.png',
  雇佣打工: '/qp-icons/official/work_coin.png',
  雇佣好友: '/qp-icons/official/work_coin.png',
  被雇佣检查: '/qp-icons/official/work_coin.png',
  冒险: '/qp-icons/official/cap_compass.png',
  护理: '/qp-icons/official/soap.png',
  好友护理: '/qp-icons/official/soap.png',
  踩踩: '/qp-icons/official/cap_paw.png',
  PK: '/qp-icons/official/pk_words.png',
  福袋: '/qp-icons/official/cap_coin.png',
}
/** 等待中/已停止：地球（旧版 的 RUNICON_DEFAULT —— 文件名是 globe 不是 earth） */
const ICON_IDLE = '/qp-icons/official/globe.png'

/** 底部抽屉（官方 x=215 y=728）：当前在做什么 + 倒计时 + 今日统计 */
export function Deck({ data }: { data: Data }) {
  const sch = data.scheduler
  const eta = data.work_eta
  const td = data.today_duration
  const q = data.queue
  const running = sch.alive

  // 队列状态是不是**当前这个调度器进程**写的：重启后上一轮留在文件里的
  // current（如"上课"）会一直挂着，照读会让界面继续显示"上课中"（用户实报过）。
  // `mark_starting()` 会写 starting + 本次 PID，对不上就当"启动检查中"；
  // 宽限 10 分钟（legacy 引擎不写队列状态，不能让"启动中"永远挂着）。
  const qStale = !!(q.starting || q.stopped || (q.pid && sch.pid && q.pid !== sch.pid))
  const upSecs = sch.uptime ? sch.uptime.split(':').reduce((a, b) => a * 60 + Number(b), 0) : 0
  const qStarting = qStale && upSecs < 600

  const etaKind = eta?.kind ?? ''
  const remain = eta?.remaining ?? null
  const curTask = !qStale && q.current ? String(q.current) : ''
  // 状态 key：图标与文案共用同一个，避免出现「冒险中」配着福袋图标这种错位。
  //   ① remaining > 0 的 work_eta = 主任务活动真在进行（带倒计时，最准）
  //   ② 否则队列 current = 调度器正在跑的场景（护理 / PK / 踩踩 / 福袋…）——
  //      这些任务**不写**"进行中，预计 N 秒后结束"那种日志，只认 work_eta 的话
  //      它们正在跑时界面会说"空闲"（用户实报）
  //   ③ 再否则 work_eta 残留 = 已到点、正在收尾
  // ① 必须判 > 0：活动结束后 10 分钟内 work_eta 仍会返回（remaining 被 clamp 成 0），
  // 只看"非 null"会让状态一直停在上一项活动上。
  const busyEta = etaKind && remain != null && remain > 0 ? etaKind : ''
  const finishing = etaKind && !busyEta && !curTask && remain != null ? etaKind : ''
  const stateKey = busyEta || curTask || finishing

  const icon = running && !qStarting && stateKey ? (ICON[stateKey] ?? ICON_IDLE) : ICON_IDLE
  const state = !running ? '未托管' : qStarting ? '启动中' : stateKey ? `${stateKey}中` : '等待中'
  // 日志最后一行（去掉时间戳前缀）：说明"此刻到底在做什么"，比一句"等待下一项"有用
  const lastLine = (data.last_line || '').replace(/^\[[\d:]+\]\s*/, '').slice(0, 60)
  // 等待时的**具体原因**（后端 queue.next_reason，如"雇佣好友：想雇的「墨瞳」正打工中
  // （不换人，等到空闲再雇）"）。没有它就只能干说一句"等待下一项任务"——用户实报"太宽泛"。
  const nextReason = q.next_reason || ''
  const nextLabel = q.next ? `下一项 ${q.next}${q.next_at ? ` ${q.next_at}` : ''}` : ''
  const hint = !running || qStarting
    ? ''
    : busyEta
      ? `剩余 ${hms(remain ?? 0)}`
      : finishing && eta
        ? `预计 ${eta.eta_clock} 结束`
        : nextLabel
  const sub = !running
    ? '调度器未运行 · 点右侧「启动」'
    : qStarting
      ? lastLine || '启动检查中…'
      : busyEta
        ? // 有"为什么在做这件事"的说明就先说它（如"打工要雇的「柠檬..」正忙，先去冒险；
          // 到点回去雇他"）——剩余时间在右上角 hint 里已经有了，不必挤在这行
          q.note || `剩余 ${hms(remain ?? 0)} · 结束后自动开启下一项`
        : finishing
          ? '收尾中… · 结束后自动开启下一项'
          : curTask
            ? lastLine || `${curTask}进行中…`
            : nextReason ||
              // 后端没给 next_reason（旧版调度器进程）时不硬猜原因：只说"下一项"，
              // 免得把"最近等待点"（护理每 60 秒一轮，永远最近）说成阻塞原因
              (q.next ? `下一项：${q.next}${q.next_at ? ` ${q.next_at}` : ''}` : '等待下一项任务')
  const stat = td
    ? `今日：学习 ${td.learn_min} 分 · 打工 ${td.work_min} 分 · 合计 ${td.total_min} 分 · 效率 ${td.eff_pct}%`
    : ''

  return (
    <div className="flt deck">
      <div className="flt drawer" id="runnerCard">
        <img
          className="dico"
          id="runnerIcon"
          src={icon}
          alt=""
          /* 已停止时灰度压暗，避免"没在跑却亮着"的误读（同 旧版 的 setRunIcon） */
          style={running ? undefined : { filter: 'grayscale(1) opacity(.55)' }}
        />
        <div className="dcol">
          <div className="dtitle">
            <span className={'dot' + (running ? ' on' : ' off')} id="runnerDot" />
            <span id="runnerState">{state}</span>
            <span className="dhint" id="runnerHint">
              {hint}
            </span>
          </div>
          <div className="dsub" id="workSub">
            {sub}
          </div>
          <div className="dsub" id="runnerSub">
            {stat}
          </div>
          <div className="saveMsg" id="runnerMsg" />
        </div>
        <span className="dmeta" id="runnerMeta">
          {running ? `PID ${sch.pid}${sch.uptime ? ` · 已跑 ${sch.uptime}` : ''}` : '未运行'}
        </span>
      </div>
    </div>
  )
}

/** 顶部时钟（旧版 把当前时间显示在资料卡右侧，这里保留为纯函数备用） */
export const clockNow = (d = new Date()): string => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
