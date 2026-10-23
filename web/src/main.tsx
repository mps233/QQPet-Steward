import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { installStageUnit } from './lib/stage'
// 旧界面那 1499 行 CSS 原样搬过来（class 名保持不变 → 样式直接生效，一行没改）
import './styles/app.css'
// Android remote-control gate + pairing QR
import './styles/connection.css'
// 总览页 V2（正常流布局，独立命名空间 v2-*，不覆盖也不依赖 app.css 的规则）
import './styles/overview-v2.css'
// 职业树（官方逐像素复刻，独立命名空间 .ct-*）
import './styles/career-tree.css'
// 实时画面（定时截图）页
import './styles/live.css'
// 通知页（渠道卡片 / 事件开关 / 可折叠历史）
import './styles/notify.css'
// 设置页（与通知页同一套白卡分节；合并成 8 个二级页）
import './styles/settings.css'
// 日志页（实时日志 / 收益记录，同套白卡）
import './styles/logs.css'
// 冒险记录（游戏风格「冒险手账」，独立命名空间 .adv-*）
import './styles/adv.css'
import './styles/onboarding.css'

installStageUnit()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* 渲染期异常的兜底：原先任何异常都是整页白屏（用户不在电脑前时等于失联）。
        放在 App 外层，保证连外壳出错时也能给出"托管仍在运行 + 重新加载"。 */}
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
