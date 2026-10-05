import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { err: Error | null }

/**
 * 渲染期异常的兜底。
 *
 * 背景：全仓原先**零** ErrorBoundary（grep `ErrorBoundary|componentDidCatch` = 0），
 * 于是任何渲染期异常都是**整页白屏** —— 对一个"挂机托管"工具来说，用户不在电脑前时
 * 白屏等于失联，而他其实只需要知道"托管还在跑"。见 docs/review/05-前端设计系统.md H 节。
 *
 * 注意：ErrorBoundary 只能捕获**渲染期**异常，捕获不到事件处理器与异步里的 ——
 * 那些仍需各处 try/catch（启停按钮的 catch 见 App.tsx 的 runRunnerAction）。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null }

  static getDerivedStateFromError(err: Error): State {
    return { err }
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    // 保留现场，便于从控制台复制反馈
    console.error('[ui] 渲染异常', err, info.componentStack)
  }

  render() {
    if (!this.state.err) return this.props.children

    return (
      <div
        style={{
          padding: 'calc(env(safe-area-inset-top, 0px) + 32px) 24px 24px',
          fontFamily:
            '-apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI",Roboto,sans-serif',
          lineHeight: 1.6,
          color: '#1C1C1E',
          background: '#F2F2F7',
          minHeight: '100%',
          boxSizing: 'border-box',
        }}
      >
        <h2 style={{ margin: '0 0 8px', fontSize: 20 }}>界面出错了</h2>
        {/* 这句是关键：让用户知道"托管没停"，不必惊慌去重启 */}
        <p style={{ color: '#6B6B70', margin: '0 0 16px', fontSize: 15 }}>
          自动化托管不受影响，仍在继续运行。刷新页面即可恢复。
        </p>
        <pre
          style={{
            background: '#fff',
            border: '1px solid #E5E5EA',
            borderRadius: 10,
            padding: 12,
            fontSize: 12,
            lineHeight: 1.5,
            overflow: 'auto',
            maxHeight: 220,
            margin: 0,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {String(this.state.err?.message || this.state.err)}
        </pre>
        <button
          onClick={() => window.location.reload()}
          style={{
            marginTop: 16,
            width: '100%',
            padding: '12px 20px',
            fontSize: 16,
            fontWeight: 600,
            borderRadius: 10,
            border: 'none',
            background: '#FF990F',
            color: '#5A2D00', // 对比度 5.42:1（橙底白字只有 2.14:1，见 02 文档）
          }}
        >
          重新加载
        </button>
      </div>
    )
  }
}
