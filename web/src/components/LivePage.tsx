import { useEffect } from 'react'
import { usePoll } from '../lib/usePoll'

/** 截图间隔：与旧版一致（服务端 capture_phone 另有 5 秒节流缓存） */
const SHOT_INTERVAL_MS = 15000

interface ShotFrame {
  url: string
  at: string
}

/** 走 fetch 而不是 <img src>：要读 X-Shot-At 拍摄时间，也方便把失败原因显示出来。 */
async function fetchShot(): Promise<ShotFrame> {
  const r = await fetch(`/api/screenshot?t=${Date.now()}`, { cache: 'no-store' })
  if (!r.ok) {
    const detail = (await r.text().catch(() => '')).trim().slice(0, 120)
    throw new Error(detail || `HTTP ${r.status}`)
  }
  const blob = await r.blob()
  return {
    url: URL.createObjectURL(blob),
    at: r.headers.get('X-Shot-At') || '',
  }
}

/** 实时画面页：每 15 秒取一张 adb 截图（切后台暂停，回前台立即补一张）。 */
export function LivePage() {
  const { data, error, reload } = usePoll(fetchShot, SHOT_INTERVAL_MS)
  const url = data?.url

  // object URL 必须逐个释放：轮询每 15 秒换一张，不释放会一直涨内存
  useEffect(() => {
    if (!url) return
    return () => URL.revokeObjectURL(url)
  }, [url])

  const loading = !data && !error
  const failed = !data && !!error

  return (
    <>
      <div className="scenecard duoshot" id="shotCardWrap">
        {data ? <span className="shotmeta">拍摄 {data.at}</span> : null}
        <a
          id="shotLink"
          href="/api/screenshot"
          target="_blank"
          rel="noopener"
          title="点开原图"
        >
          {url ? <img id="phoneShot" src={url} alt="手机当前画面" /> : null}
          {loading || failed ? (
            <span className={'shotloading' + (failed ? ' failed' : '')}>
              {failed ? '获取失败，稍后自动重试…' : '画面加载中…'}
            </span>
          ) : null}
        </a>
        <div className="shoterr">{error ? `获取失败：${error}` : ''}</div>
      </div>
      <div className="shotpage-ctl">
        <button className="savebtn" onClick={reload}>
          刷新画面
        </button>
        <a className="savebtn" href="/api/screenshot" target="_blank" rel="noopener">
          在新标签打开原图
        </a>
      </div>
      <p className="shotnote">每 15 秒自动刷新；切到后台会暂停，回来时立即补一张。</p>
    </>
  )
}
