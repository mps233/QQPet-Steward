import { useEffect } from 'react'
import { apiUrl, readConnection } from '../api'
import { usePoll } from '../lib/usePoll'

/** 截图间隔：与旧版一致（服务端 capture_phone 另有 5 秒节流缓存） */
const SHOT_INTERVAL_MS = 15000

interface ShotFrame {
  url: string
  at: string
}

/** 走 fetch 而不是 <img src>：要读 X-Shot-At 拍摄时间，也方便把失败原因显示出来。 */
async function fetchShot(): Promise<ShotFrame> {
  const c = readConnection()
  const r = await fetch(apiUrl(`/api/screenshot?t=${Date.now()}`, c.server), {
    cache: 'no-store',
    credentials: c.server ? 'omit' : 'same-origin',
    headers: c.token ? { Authorization: `Bearer ${c.token}` } : {},
  })
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
  const shotHref = apiUrl('/api/screenshot', readConnection().server)

  // object URL 必须逐个释放：轮询每 15 秒换一张，不释放会一直涨内存
  useEffect(() => {
    if (!url) return
    return () => URL.revokeObjectURL(url)
  }, [url])

  const loading = !data && !error
  const failed = !data && !!error
  const stamp = data?.at ? `拍摄 ${data.at}` : '等待画面'

  return (
    <div className="shotbody">
      <div className="shotstage" id="shotCardWrap">
        <a
          id="shotLink"
          className="shotscreen"
          href={shotHref}
          target="_blank"
          rel="noopener"
          title="点开原图"
          onClickCapture={(e) => {
            // The plain href cannot carry a device Bearer token; let the
            // authenticated blob URL open instead when paired remotely.
            if (url && readConnection().server) {
              e.preventDefault()
              window.open(url, '_blank', 'noopener')
            }
          }}
          onClick={(e) => {
            if (!url) e.preventDefault()
          }}
        >
          {url ? (
            <>
              <span
                className="shotambient"
                style={{ backgroundImage: `url(${url})` }}
                aria-hidden="true"
              />
              <img id="phoneShot" src={url} alt="手机当前画面" />
            </>
          ) : null}
          {loading || failed ? (
            <span className={'shotempty' + (failed ? ' failed' : '')}>
              {failed ? null : <span className="shotspin" aria-hidden="true" />}
              <span className="shotempty-t">{failed ? '暂时连不上手机' : '正在获取画面'}</span>
              <span className="shotempty-s">
                {failed ? '15 秒后自动重试' : '通常 1 秒内完成'}
              </span>
            </span>
          ) : null}
          {url ? <span className="shotprogress" key={url} aria-hidden="true" /> : null}
        </a>
      </div>

      <div className="shotmeta">
        <span className="shotstamp">
          <i className={'shotdot' + (error ? ' stale' : '')} aria-hidden="true" />
          {stamp}
        </span>
        <span className={'shotcycle' + (error ? ' stale' : '')}>
          {error ? '暂时连不上，正在重试' : '每 15 秒自动刷新'}
        </span>
      </div>

      <div className="shotactions">
        <button className="shotbtn primary" onClick={reload}>
          刷新画面
        </button>
        <a
          className="shotbtn"
          href={shotHref}
          target="_blank"
          rel="noopener"
          onClick={(e) => {
            if (url && readConnection().server) {
              e.preventDefault()
              window.open(url, '_blank', 'noopener')
            }
          }}
        >
          查看原图
        </a>
      </div>

      {error ? <div className="shoterr">获取失败：{error}</div> : null}
    </div>
  )
}
