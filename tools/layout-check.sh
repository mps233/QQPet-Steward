#!/usr/bin/env bash
# 布局体检：用同源 iframe + headless Chrome 量舞台骨架，交给 assert-layout.py 断言。
#
# 为什么用 iframe：headless Chrome 的最小视口宽是 500px。直接 --window-size=390
# 截出的图是 390 宽，但页面仍按 500 排版后裁掉右侧 —— 会误判成"窄屏正常"
# （web/FRONTEND_PITFALLS.md:35-52 记过两次）。
#
# 用法：
#   ./tools/layout-check.sh                          # 13 档尺寸 × 首页/展开/设置/测试视频
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
SIZES="320x568 360x640 390x844 430x932 520x844 521x844 639x844 640x844 768x1024 844x390 1024x768 1440x900 390x420"
PAGES="main main-open set shot"

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
api_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE/api/data" || true)
if [ "$api_code" = "401" ]; then
  echo "⚠ dashboard 已启用访问验证；此 CLI 探针没有浏览器会话，跳过而不是把登录页误判成布局损坏。"
  echo "请使用已验证的浏览器检查，或在隔离测试服务上运行探针。"
  exit 2
fi

TMP="$(mktemp -d)"
cleanup() { [ "$KEEP" = 1 ] || rm -rf "$TMP"; [ "$KEEP" = 1 ] && echo "（中间产物保留在 ${TMP}）"; }
trap cleanup EXIT

REPORT="$TMP/report.jsonl"
: > "$REPORT"
mkdir -p "$DIST/assets"

for size in $SIZES; do
  W="${size%x*}"; H="${size#*x}"
  for page in $PAGES; do
    TAB="${page%-open}"
    # 探针页必须放在 /assets/ 下（路由只服务 index.html 与 /assets/*）。
    # ⚠ 下面的 JS 里不能出现反引号模板串 —— heredoc 会展开 ${...}。
    cat > "$PROBE" <<HTML
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>
<body style="margin:0;background:#888">
<iframe id="f" data-page="${page}" src="/?tab=${TAB}" width="${W}" height="${H}" style="border:0;display:block"></iframe>
<script>
HTML
    cat "$ROOT/tools/layout-probe.js" >> "$PROBE"
    printf '\n</script></body></html>\n' >> "$PROBE"

    dom="$TMP/dom-${size}-${page}.html"
    "$CHROME" --headless=new --disable-gpu --no-first-run --hide-scrollbars \
      --autoplay-policy=no-user-gesture-required \
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
