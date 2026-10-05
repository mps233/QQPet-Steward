#!/usr/bin/env bash
# 布局体检：用同源 iframe + headless Chrome 量舞台骨架，交给 assert-layout.py 断言。
#
# 为什么用 iframe：headless Chrome 的最小视口宽是 500px。直接 --window-size=390
# 截出的图是 390 宽，但页面仍按 500 排版后裁掉右侧 —— 会误判成"窄屏正常"
# （web/FRONTEND_PITFALLS.md:35-52 记过两次）。
#
# 用法：
#   ./tools/layout-check.sh                          # 默认 6 档尺寸 × 2 页
#   ./tools/layout-check.sh --sizes "390x844"        # 只跑一档
#   ./tools/layout-check.sh --port 8899
#   ./tools/layout-check.sh --keep                   # 保留中间产物供排查
#
# 退出码：0 全通过 / 1 断言失败 / 2 环境不满足（未阻塞 —— 可让 CI 忽略）
#
# 前置：dashboard 在跑、web/dist 已构建、装了 Google Chrome。
set -uo pipefail

PORT=8787
KEEP=0
SIZES="320x568 360x640 390x844 430x932 768x1024 844x390"
PAGES="main set"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/web/dist"
PROBE="$DIST/assets/_layout-probe.html"
ASSERT="$ROOT/tools/assert-layout.py"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

while [ $# -gt 0 ]; do
  case "$1" in
    --port)  PORT="$2"; shift 2 ;;
    --sizes) SIZES="$2"; shift 2 ;;
    --keep)  KEEP=1; shift ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

BASE="http://127.0.0.1:${PORT}"

# ---- 前置检查（失败给退出码 2，不阻塞 CI）----
if [ ! -x "$CHROME" ]; then
  echo "❌ 找不到 Chrome：$CHROME（可用 CHROME=... 指定）" >&2; exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 需要 python3" >&2; exit 2
fi
if [ ! -f "$DIST/index.html" ]; then
  echo "❌ web/dist 未构建：cd web && ./build.sh" >&2; exit 2
fi
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE/" || true)
if [ "$code" != "200" ]; then
  echo "❌ dashboard 未响应（$BASE → ${code:-无}）。先起：python3 dashboard.py" >&2
  exit 2
fi

TMP="$(mktemp -d)"
cleanup() { [ "$KEEP" = 1 ] || rm -rf "$TMP"; [ "$KEEP" = 1 ] && echo "（中间产物保留在 $TMP）"; }
trap cleanup EXIT

REPORT="$TMP/report.jsonl"
: > "$REPORT"
mkdir -p "$DIST/assets"

for size in $SIZES; do
  W="${size%x*}"; H="${size#*x}"
  for page in $PAGES; do
    # 探针页必须放在 /assets/ 下（路由只服务 index.html 与 /assets/*）。
    # ⚠ 下面的 JS 里不能出现反引号模板串 —— heredoc 会展开 ${...}。
    cat > "$PROBE" <<HTML
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>
<body style="margin:0;background:#888">
<iframe id="f" src="/?tab=${page}" width="${W}" height="${H}" style="border:0;display:block"></iframe>
<script>
var f = document.getElementById('f')
var SIZE = "${W}x${H}", PAGE = "${page}", done = false
function send() {
  if (done) return
  done = true
  var d = f.contentDocument, w = f.contentWindow
  if (!d) {
    document.body.setAttribute('data-probe', encodeURIComponent(JSON.stringify({ size: SIZE, page: PAGE, probeMissing: true })))
    return
  }
  var q = function (s) { return d.querySelector(s) }
  var home = q('.home'), slot = q('.page-slot.page-enter') || q('.page-slot'), main = q('main')
  // 内页没有 .home，容器是 section[data-page]（.card / .shotpage，见 App.tsx:394-434）
  var sec = q('section[data-page]')
  var rules = []
  var walk = function (list) {
    for (var i = 0; i < list.length; i++) {
      var r = list[i]
      if (r.cssRules) { walk(r.cssRules); continue }        // 递归 @media
      var sel = r.selectorText
      if (!sel || !/\bmain\b/.test(sel)) continue            // 只体检 main 相关的骨架契约
      var n = -1
      try { n = d.querySelectorAll(sel).length } catch (e) { n = -3 }
      rules.push([sel.slice(0, 140), n])
    }
  }
  for (var s = 0; s < d.styleSheets.length; s++) {
    try { walk(d.styleSheets[s].cssRules) } catch (e) { rules.push(['<CORS>', -2]) }
  }
  var out = {
    size: SIZE, page: PAGE,
    vp: [w.innerWidth, w.innerHeight],
    homeH: home ? +home.getBoundingClientRect().height.toFixed(2) : null,
    secH: sec ? +sec.getBoundingClientRect().height.toFixed(2) : null,
    // 光看 scrollHeight-clientHeight 不够：overflow:hidden 时元素依然"内容比盒子高"，
    // 但用户滚不动。真正的判据是 computed overflow-y 是否为 visible/hidden。
    homeOverflowY: home ? getComputedStyle(home).overflowY : null,
    homeOverflow: home ? home.scrollHeight - home.clientHeight : null,
    slotOverflow: slot ? slot.scrollHeight - slot.clientHeight : null,
    mainKids: main ? [].map.call(main.children, function (e) {
      return e.tagName.toLowerCase() + (e.className ? '.' + String(e.className).trim().split(/\s+/).join('.') : '')
    }) : [],
    btnW: q('.rbtn') ? +q('.rbtn').getBoundingClientRect().width.toFixed(1) : null,
    fsTask: q('.mname') ? getComputedStyle(q('.mname')).fontSize : null,
    uVar: getComputedStyle(d.documentElement).getPropertyValue('--u').trim(),
    ready: !!(q('.home') && d.querySelectorAll('.cap').length >= 3),
    rules: rules
  }
  // 单行无缩进（FRONTEND_PITFALLS.md:63-65：带缩进会让 grep 抓不到）；
  // URL 编码避免属性值里的引号打架。
  document.body.setAttribute('data-probe', encodeURIComponent(JSON.stringify(out)))
}
f.addEventListener('load', function () {
  var n = 0
  var tick = function () { if (done || ++n > 40) send(); else setTimeout(tick, 250) }
  tick()
})
</script></body></html>
HTML

    dom="$TMP/dom-${size}-${page}.html"
    "$CHROME" --headless=new --disable-gpu --no-first-run --hide-scrollbars \
      --window-size=$(( W + 40 )),$(( H + 40 )) \
      --virtual-time-budget=30000 \
      --dump-dom "$BASE/assets/_layout-probe.html" > "$dom" 2>/dev/null

    raw=$(grep -oE 'data-probe="[^"]*"' "$dom" | head -1 | sed 's/^data-probe="//; s/"$//')
    if [ -z "$raw" ]; then
      printf '{"size":"%s","page":"%s","probeMissing":true}\n' "$size" "$page" >> "$REPORT"
      continue
    fi
    python3 -c 'import sys,urllib.parse; print(urllib.parse.unquote(sys.argv[1]))' "$raw" >> "$REPORT"
  done
done

rm -f "$PROBE"
python3 "$ASSERT" "$REPORT"
