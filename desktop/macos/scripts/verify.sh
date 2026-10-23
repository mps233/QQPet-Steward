#!/usr/bin/env bash
# Verify the complete bundle using an isolated, offline runtime self-test.
set -euo pipefail

APP="${1:-}"

if [ -z "$APP" ]; then
  echo "usage: $0 /absolute/path/to/QQ Pet Dashboard.app" >&2
  exit 1
fi
if [ ! -d "$APP" ]; then
  echo "找不到 .app：$APP" >&2
  exit 1
fi

plutil -lint "$APP/Contents/Info.plist" >/dev/null
IDENTIFIER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")"
ICON="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$APP/Contents/Info.plist")"
if [ "$IDENTIFIER" != "ai.qqpet.dashboard.desktop" ]; then
  echo "bundle identifier 异常：$IDENTIFIER" >&2
  exit 1
fi
if [ "$ICON" != "icon.icns" ]; then
  echo "bundle icon 异常：$ICON" >&2
  exit 1
fi

BIN="$APP/Contents/MacOS/qqpet-dashboard-desktop"
[ -x "$BIN" ] || { echo "找不到可执行文件：$BIN" >&2; exit 1; }
file "$BIN" | rg -q 'Mach-O 64-bit executable arm64|Mach-O 64-bit executable x86_64' \
  || { echo "不是预期的 Mach-O 可执行文件" >&2; exit 1; }

RUNTIME="$APP/Contents/Resources/backend/QQPetRuntime"
if [ ! -x "$RUNTIME" ]; then
  echo "缺少随包本机 runtime：$RUNTIME" >&2
  exit 1
fi
BUILD_FINGERPRINT="$(cat "$APP/Contents/Resources/backend/.build-fingerprint")"
if [[ ! "$BUILD_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]]; then
  echo "随包 runtime 构建指纹无效。" >&2
  exit 1
fi
"$RUNTIME" self-test

echo "bundle verify: ok"
echo "identifier: $IDENTIFIER"
echo "icon: $ICON"
echo "bundled backend, scheduler, Python and resources: verified"
du -sh "$APP"
