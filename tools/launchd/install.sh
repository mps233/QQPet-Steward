#!/bin/bash
# 安装/卸载「QQ 宠物托管 · 仪表盘」的 launchd 托管（macOS）。
#
#   ./tools/launchd/install.sh              # 安装 + 启动
#   ./tools/launchd/install.sh --uninstall  # 卸载（停服务并移除 plist）
#
# 为什么不让 launchd 也托管调度器：
#   调度器的启停是仪表盘按钮的交互（/api/runner/start | stop），
#   给它套 KeepAlive 会让「停止」被立刻拉回。仪表盘由 launchd 托管后，
#   它拉起的调度器同属该 launchd 作业的进程联盟，已不再随 App 退出被连带杀。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LABEL="ai.qqpet.dashboard"
SRC="$ROOT/tools/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$DEST"
  echo "已卸载 $LABEL（日志保留在 runs/dashboard_console.log）"
  exit 0
fi

[ -x "$ROOT/.venv/bin/python" ] || { echo "找不到 $ROOT/.venv/bin/python" >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/runs"
sed "s|__PROJECT_ROOT__|$ROOT|g" "$SRC" > "$DEST"

# 幂等：旧的先下线（不存在会返回非零，忽略）
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$DEST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

for _ in $(seq 1 20); do
  if curl -s -o /dev/null --max-time 2 http://127.0.0.1:8787/; then
    echo "已托管并启动：$LABEL → http://127.0.0.1:8787/"
    exit 0
  fi
  sleep 0.5
done
echo "已托管，但 8787 暂未就绪，查看 runs/dashboard_console.log" >&2
exit 1
