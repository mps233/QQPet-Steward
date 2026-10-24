#!/usr/bin/env bash
# Build the complete macOS app without starting background services.
set -euo pipefail

DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
WEB_DIR="$ROOT/web"
PYTHON="${QQPET_BUILD_PYTHON:-$ROOT/.venv/bin/python}"
SOURCE_RUNTIME="$DESKTOP_DIR/target/runtime/QQPetRuntime"
APP_IDENTIFIER="ai.qqpet.dashboard.desktop"
APP_NAME="QQPet-Steward.app"
STABLE_BUNDLE_DIR="$DESKTOP_DIR/target/release/bundle/macos"
STABLE_APP_PATH="$STABLE_BUNDLE_DIR/$APP_NAME"
BUILD_TARGET_DIR="${CARGO_TARGET_DIR:-$DESKTOP_DIR/target}"
case "$BUILD_TARGET_DIR" in
  /*) ;;
  *) BUILD_TARGET_DIR="$PWD/$BUILD_TARGET_DIR" ;;
esac

# Optimization changes the final resource seal, so notarization must target the
# optimized app rather than Tauri's intermediate bundle.
if [ -n "${APPLE_ID:-}${APPLE_API_KEY:-}${APPLE_API_ISSUER:-}" ]; then
  echo "当前构建会在 Tauri 打包后优化资源；请在最终产物验证后单独公证，勿向中间构建提供公证凭据。" >&2
  exit 1
fi

for directory in "$HOME/.local/bin" "$HOME/.local/node/bin" /opt/homebrew/bin /usr/local/bin; do
  if [ -x "$directory/node" ]; then
    export PATH="$directory:$PATH"
    break
  fi
done

if [ ! -f "$WEB_DIR/dist/index.html" ]; then
  echo "web/dist 不存在，先构建 React 仪表盘…"
  if [ ! -d "$WEB_DIR/node_modules" ]; then
    echo "web/node_modules 不存在；请先在 web/ 执行 pnpm install（或 npm install）。" >&2
    exit 1
  fi
  "$WEB_DIR/build.sh"
fi

"$DESKTOP_DIR/scripts/build-runtime.sh"

cd "$DESKTOP_DIR"
if [ ! -x "node_modules/.bin/tauri" ]; then
  echo "安装 desktop/macos 的本地 Tauri CLI…"
  if command -v pnpm >/dev/null 2>&1; then
    pnpm install --frozen-lockfile
  elif command -v npm >/dev/null 2>&1; then
    npm install
  else
    echo "找不到 pnpm/npm；无法安装本地 Tauri CLI。" >&2
    exit 1
  fi
fi

# Retry a failed Cargo cache in an isolated target, then publish the bundle at
# the same project-local path used by a normal build.
mkdir -p "$BUILD_TARGET_DIR"
export CARGO_TARGET_DIR="$(cd "$BUILD_TARGET_DIR" && pwd -P)"
build_target="$CARGO_TARGET_DIR"

if ! node_modules/.bin/tauri build --bundles app; then
  fallback_target="$(mktemp -d "${TMPDIR:-/tmp}/qqpet-macos-build.XXXXXX")"
  echo "默认 Cargo 缓存构建失败，使用隔离 target 重试：$fallback_target" >&2
  export CARGO_TARGET_DIR="$fallback_target"
  build_target="$fallback_target"
  node_modules/.bin/tauri build --bundles app
fi

APP_PATH="$build_target/release/bundle/macos/$APP_NAME"
if [ ! -d "$APP_PATH" ] || [ ! -f "$APP_PATH/Contents/Info.plist" ]; then
  echo "构建结束，但没有找到预期产物：$APP_PATH" >&2
  exit 1
fi

ACTUAL_IDENTIFIER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP_PATH/Contents/Info.plist" 2>/dev/null || true)"
if [ "$ACTUAL_IDENTIFIER" != "$APP_IDENTIFIER" ]; then
  echo "生成的 .app bundle identifier 异常：$ACTUAL_IDENTIFIER" >&2
  exit 1
fi

"$PYTHON" "$DESKTOP_DIR/restore_runtime_symlinks.py" \
  --source "$SOURCE_RUNTIME" \
  --destination "$APP_PATH/Contents/Resources/backend"

SIGNING_IDENTITY="$("$PYTHON" - "$DESKTOP_DIR/tauri.conf.json" <<'PY'
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    configuration = json.load(handle)
configured = configuration.get("bundle", {}).get("macOS", {}).get("signingIdentity")
print(os.environ.get("APPLE_SIGNING_IDENTITY") or configured or "-")
PY
)"
if [ "$SIGNING_IDENTITY" = "-" ] \
    && codesign -d --verbose=2 "$APP_PATH" 2>&1 | rg -q '^Authority='; then
  echo "Tauri 产物已使用证书签名；请设置 APPLE_SIGNING_IDENTITY 或 signingIdentity，避免优化后降为 ad hoc 签名。" >&2
  exit 1
fi
SIGNING_OPTIONS=(--force --sign "$SIGNING_IDENTITY"
  --preserve-metadata=identifier,entitlements,requirements,flags,runtime)
if [ "$SIGNING_IDENTITY" != "-" ]; then
  SIGNING_OPTIONS+=(--timestamp)
fi
# PyInstaller signs framework binaries, but their restored bundle directories
# also need resource seals before the outer app can pass strict verification.
while IFS= read -r -d '' framework; do
  codesign "${SIGNING_OPTIONS[@]}" "$framework"
done < <("$PYTHON" - "$APP_PATH/Contents/Resources/backend" <<'PY'
import os
from pathlib import Path
import sys

frameworks = []
for folder, directories, _files in os.walk(sys.argv[1], followlinks=False):
    for name in directories:
        path = Path(folder) / name
        if name.endswith(".framework") and not path.is_symlink():
            frameworks.append(path)
for path in sorted(frameworks, key=lambda value: len(value.parts), reverse=True):
    sys.stdout.buffer.write(os.fsencode(path) + b"\0")
PY
)
codesign "${SIGNING_OPTIONS[@]}" "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"

if [ "$APP_PATH" != "$STABLE_APP_PATH" ]; then
  mkdir -p "$STABLE_BUNDLE_DIR"
  staged_bundle="$(mktemp -d "$STABLE_BUNDLE_DIR/.qqpet-bundle.XXXXXX")"
  trap 'rm -rf "$staged_bundle"' EXIT
  ditto "$APP_PATH" "$staged_bundle/$APP_NAME"
  rm -rf "$STABLE_APP_PATH"
  mv "$staged_bundle/$APP_NAME" "$STABLE_APP_PATH"
  rmdir "$staged_bundle"
  trap - EXIT
  APP_PATH="$STABLE_APP_PATH"
fi

"$DESKTOP_DIR/scripts/verify.sh" "$APP_PATH"
echo "$APP_PATH"
