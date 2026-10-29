import { useSyncExternalStore } from 'react'
import {
  getToastSnapshot,
  subscribeToast,
  type ToastIcon,
  type ToastItem,
} from '../lib/toast'

/** 图标是内联 SVG 而不是复用 ctrl/theme-*.svg：喂到 20px 圆片里只有 13px，
 *  官方那几张带渐变的插画在这个尺寸会糊成一团；这里用同形的简笔版，
 *  颜色由 CSS 的 currentColor 控制（默认暖金、错误态暖红）。 */
function ToastGlyph({ name }: { name: ToastIcon }) {
  const common = {
    viewBox: '0 0 24 24',
    width: 13,
    height: 13,
    'aria-hidden': true,
    focusable: 'false',
  } as const
  switch (name) {
    case 'paw':
      return (
        <svg {...common} fill="currentColor">
          <ellipse cx="7.3" cy="9.2" rx="2" ry="2.6" transform="rotate(-24 7.3 9.2)" />
          <ellipse cx="11.3" cy="6.6" rx="2" ry="2.7" transform="rotate(-8 11.3 6.6)" />
          <ellipse cx="15.6" cy="7" rx="2" ry="2.7" transform="rotate(10 15.6 7)" />
          <ellipse cx="19.2" cy="10.1" rx="1.9" ry="2.5" transform="rotate(26 19.2 10.1)" />
          <path d="M13.2 11.1c-2.6 0-4.4 3.4-6.2 4.7-1.8 1.3-1.6 3.9.8 4.6 2 .6 3.1-1 4.6-1s2.6 1.6 4.6 1c2.4-.7 2.6-3.3.8-4.6-1.8-1.3-3.6-4.7-6.2-4.7Z" />
        </svg>
      )
    case 'success':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5.4 12.4 9.7 16.7 18.8 7.2" />
        </svg>
      )
    case 'error':
      return (
        <svg {...common} fill="none">
          <path d="M12 4.6v10" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
          <circle cx="12" cy="19.2" r="1.7" fill="currentColor" />
        </svg>
      )
    case 'theme-system':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8.6" fill="none" stroke="currentColor" strokeWidth="1.7" />
          <path d="M12 3.4 A8.6 8.6 0 0 0 12 20.6 Z" fill="currentColor" />
          <path d="M12 4.2V19.8" stroke="currentColor" strokeWidth="1" opacity=".55" />
        </svg>
      )
    case 'theme-light':
      return (
        <svg {...common}>
          <g stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
            <path d="M12 2.4v3M12 18.6v3M2.4 12h3M18.6 12h3M5.2 5.2l2.1 2.1M16.7 16.7l2.1 2.1M5.2 18.8l2.1-2.1M16.7 7.3l2.1-2.1" />
          </g>
          <circle cx="12" cy="12" r="4.6" fill="currentColor" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      )
    case 'theme-dark':
      return (
        <svg {...common}>
          <path
            d="M16.8 5.4A8.6 8.6 0 1 0 16.8 18.6 6.9 6.9 0 0 1 16.8 5.4Z"
            fill="currentColor"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinejoin="round"
          />
        </svg>
      )
  }
}

function ToastView({ item }: { item: ToastItem }) {
  const error = item.tone === 'error'
  return (
    <div
      className={`qp-toast qp-toast--${item.placement}${error ? ' qp-toast--error' : ''}`}
      role={error ? 'alert' : 'status'}
      aria-live={error ? 'assertive' : 'polite'}
    >
      {item.icon ? (
        <span className="qp-toast__ico" aria-hidden="true">
          <ToastGlyph name={item.icon} />
        </span>
      ) : null}
      <span className="qp-toast__txt">{item.text}</span>
    </div>
  )
}

/** 全局唯一的气泡宿主：在 main.tsx 挂一次，页面只负责 showToast()。
 *  key 用 item.id：同位置换一条时重新挂载，入场动画才会重播。 */
export function ToastHost() {
  const snap = useSyncExternalStore(subscribeToast, getToastSnapshot, getToastSnapshot)
  return (
    <>
      {snap.center ? <ToastView key={`center-${snap.center.id}`} item={snap.center} /> : null}
      {snap.career ? <ToastView key={`career-${snap.career.id}`} item={snap.career} /> : null}
      {snap.bottom ? <ToastView key={`bottom-${snap.bottom.id}`} item={snap.bottom} /> : null}
    </>
  )
}
