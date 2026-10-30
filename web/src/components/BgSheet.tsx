import {
  BG_VER,
  RANDOM_KEY,
  RANDOM_ROTATE_MINUTES,
  SCENE_INFO,
  SCENE_ORDER,
  sceneBgFile,
  sceneVideoFile,
} from '../lib/scene'
import { assetUrl } from '../lib/connection'

/** 「随机」封面里扇形展开的三张预览图（纯装饰，颜色拉开对比；深色模式自动取夜版）。
 *  只在 SCENE_INFO 里存在的键才用，背景库调整时不会出现裂图。 */
const RANDOM_PREVIEW = ['home-cabin', 'home-huatian', 'home-ocean']
  .filter((k) => k in SCENE_INFO)

/** 房间背景选择面板（点总览页左上角圆钮打开；点一张即生效）。
 *  缩略图跟着深浅色取对应文件（房间场景有 -dark 版），与实际应用的那张保持一致。 */
export function BgSheet({
  open,
  manual,
  dark,
  onSelect,
  onClose,
}: {
  open: boolean
  manual: string
  dark: boolean
  onSelect: (key: string) => void
  onClose: () => void
}) {
  if (!open) return null
  return (
    <div id="bgSheet" className="on" aria-hidden="false">
      <div className="bgmask" data-bgclose onClick={onClose} />
      <div className="bgpanel">
        <div className="bghead">
          <span>房间背景</span>
          <button type="button" data-bgclose onClick={onClose}>
            关闭
          </button>
        </div>
        <div className="bggrid" id="bgSheetGrid">
          <button
            type="button"
            className={'bgtile' + (manual === RANDOM_KEY ? ' on' : '')}
            data-bg={RANDOM_KEY}
            onClick={() => onSelect(RANDOM_KEY)}
          >
            {/* 封面只放装饰图形、不放字（名字在下面的 .bgname）：
                三张背景小卡扇形摊开 + 中间的「换一换」图标 = 随机的感觉。
                小卡是 <img> 不是文字，旋转不会踩 iOS 文字被合成层糊掉那条。 */}
            <span className="bgthumb randomthumb" aria-hidden="true">
              {RANDOM_PREVIEW.map((k, i) => (
                <img
                  key={k}
                  className={`rt-card rt-card-${i + 1}`}
                  loading="lazy"
                  src={assetUrl(`/qp-icons/bg/${sceneBgFile(k, dark)}.jpg?v=${BG_VER}`)}
                  alt=""
                />
              ))}
              <span className="rt-badge">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M3 7h3.2L17 17h3.8" />
                  <path d="M3 17h3.2L17 7h3.8" />
                  <path d="m17.4 4.2 3.4 2.8-3.4 2.8" />
                  <path d="m17.4 14.2 3.4 2.8-3.4 2.8" />
                </svg>
              </span>
              <i className="rt-spark rt-spark-1" />
              <i className="rt-spark rt-spark-2" />
              <i className="rt-spark rt-spark-3" />
            </span>
            <span className="bgname">随机</span>
          </button>
          {SCENE_ORDER.map((k) => (
            <button
              type="button"
              key={k}
              className={'bgtile' + (manual === k ? ' on' : '')}
              data-bg={k}
              onClick={() => onSelect(k)}
            >
              <img
                className="bgthumb"
                loading="lazy"
                src={assetUrl(`/qp-icons/bg/${sceneBgFile(k, dark)}.jpg?v=${BG_VER}`)}
                alt=""
              />
              {/* 带视频层的背景（官方叫「灵动背景」）打一个角标：缩略图是静帧，不打标
                  用户看不出这张会动，而且它在手机上比别的背景多下一份 mp4。 */}
              {sceneVideoFile(k, dark) ? <span className="bgvideo">动态</span> : null}
              <span className="bgname">{SCENE_INFO[k]?.name}</span>
            </button>
          ))}
        </div>
        <div className="bgtip">
          「随机」= 从全部背景里每 {RANDOM_ROTATE_MINUTES} 分钟换一张（深色自动用夜版）。
          选中具体背景就固定，不再轮换。长按左上角圆钮可直接切下一张。
        </div>
      </div>
    </div>
  )
}
