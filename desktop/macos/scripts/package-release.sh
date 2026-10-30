#!/usr/bin/env bash
# Package a verified app without starting the app or its background services.
set -euo pipefail

DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="$(cd "$DESKTOP_DIR/../.." && pwd -P)"
PYTHON="${QQPET_BUILD_PYTHON:-$ROOT/.venv/bin/python}"
APP_NAME="QQPet-Steward.app"
SKIP_UPDATER=0
if [ "${1:-}" = "--no-updater" ]; then
  SKIP_UPDATER=1
  shift
fi
APP="${1:-$DESKTOP_DIR/target/release/bundle/macos/$APP_NAME}"

if [ "$#" -gt 1 ] || [ "${1:-}" = "--help" ]; then
  echo "usage: $0 [--no-updater] [/absolute/path/to/QQPet-Steward.app]" >&2
  if [ "${1:-}" = "--help" ]; then exit 0; fi
  exit 1
fi
if [ ! -d "$APP" ] || [ ! -f "$APP/Contents/Info.plist" ]; then
  echo "找不到完整的 .app：$APP" >&2
  exit 1
fi
if [ ! -x "$PYTHON" ]; then
  echo "找不到构建 Python：$PYTHON；可设置 QQPET_BUILD_PYTHON。" >&2
  exit 1
fi
for tool in ditto hdiutil lipo codesign shasum; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "缺少 macOS 打包工具：$tool" >&2
    exit 1
  fi
done
APP="$(cd "$(dirname "$APP")" && pwd -P)/$(basename "$APP")"

VERSION="$("$PYTHON" - "$APP/Contents/Info.plist" "$DESKTOP_DIR/tauri.conf.json" <<'PY'
import json
import plistlib
import re
import sys

with open(sys.argv[1], "rb") as handle:
    bundle = plistlib.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    configuration = json.load(handle)
version = bundle.get("CFBundleShortVersionString")
if not isinstance(version, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version):
    raise SystemExit("App version is missing or unsuitable for an attachment filename")
if version != configuration.get("version"):
    raise SystemExit("App version does not match desktop/macos/tauri.conf.json")
print(version)
PY
)"
EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
case "$EXECUTABLE" in
  ""|*/*|*$'\n'*)
    echo "App 可执行文件名称无效。" >&2
    exit 1
    ;;
esac
BIN="$APP/Contents/MacOS/$EXECUTABLE"
if [ ! -x "$BIN" ]; then
  echo "找不到 App 可执行文件：$BIN" >&2
  exit 1
fi
ARCHITECTURES="$(lipo -archs "$BIN")"
case "$ARCHITECTURES" in
  arm64) ARCH="arm64" ;;
  x86_64) ARCH="x64" ;;
  *)
    echo "当前附件命名只支持单架构 arm64 或 x86_64；实际为：$ARCHITECTURES" >&2
    exit 1
    ;;
esac

OUTPUT_DIR="$DESKTOP_DIR/target/releases/v$VERSION"
BASE_NAME="QQPet-Steward_${VERSION}_macos_${ARCH}"
ARTIFACTS=("$BASE_NAME.dmg" "$BASE_NAME.zip")
if [ "$SKIP_UPDATER" -eq 0 ]; then
  ARTIFACTS+=("$BASE_NAME.app.tar.gz" "$BASE_NAME.app.tar.gz.sig")
fi
ATTACHMENTS=("${ARTIFACTS[@]}" "SHA256SUMS.txt")
for name in "${ATTACHMENTS[@]}"; do
  if [ -e "$OUTPUT_DIR/$name" ] || [ -L "$OUTPUT_DIR/$name" ]; then
    echo "附件已存在，拒绝覆盖：$OUTPUT_DIR/$name" >&2
    exit 1
  fi
done

QQPET_BUILD_PYTHON="$PYTHON" "$DESKTOP_DIR/scripts/verify.sh" "$APP"

mkdir -p "$OUTPUT_DIR"
WORK_DIR="$(mktemp -d "$OUTPUT_DIR/.package-release.XXXXXX")"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

DMG_STAGE="$WORK_DIR/dmg"
mkdir "$DMG_STAGE"
ditto "$APP" "$DMG_STAGE/$APP_NAME"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create \
  -volname "QQPet-Steward $VERSION" \
  -srcfolder "$DMG_STAGE" \
  -fs HFS+ \
  -format UDZO \
  "$WORK_DIR/$BASE_NAME.dmg"
hdiutil verify "$WORK_DIR/$BASE_NAME.dmg"

ditto -c -k --sequesterRsrc --keepParent \
  "$DMG_STAGE/$APP_NAME" "$WORK_DIR/$BASE_NAME.zip"
UNZIP_STAGE="$WORK_DIR/unzipped"
mkdir "$UNZIP_STAGE"
ditto -x -k "$WORK_DIR/$BASE_NAME.zip" "$UNZIP_STAGE"
codesign --verify --deep --strict "$UNZIP_STAGE/$APP_NAME"
"$PYTHON" "$DESKTOP_DIR/restore_runtime_symlinks.py" \
  --source "$DESKTOP_DIR/target/runtime/QQPetRuntime" \
  --destination "$UNZIP_STAGE/$APP_NAME/Contents/Resources/backend" \
  --check

if [ "$SKIP_UPDATER" -eq 0 ]; then
  TAURI_CLI="$DESKTOP_DIR/node_modules/.bin/tauri"
  if [ ! -x "$TAURI_CLI" ]; then
    echo "找不到 Tauri CLI：$TAURI_CLI；无法生成 updater 产物。" >&2
    exit 1
  fi
  SIGN_KEY_PATH="${TAURI_SIGNING_PRIVATE_KEY_PATH:-$HOME/.tauri/qqpet-updater.key}"
  if [ ! -f "$SIGN_KEY_PATH" ]; then
    echo "找不到签名私钥：$SIGN_KEY_PATH。请设置 TAURI_SIGNING_PRIVATE_KEY_PATH，或先用 tauri signer generate 生成。" >&2
    exit 1
  fi
  if [ -z "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}" ] \
      && [ -f "$HOME/.tauri/qqpet-updater.key.password" ]; then
    TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat "$HOME/.tauri/qqpet-updater.key.password")"
    export TAURI_SIGNING_PRIVATE_KEY_PASSWORD
  fi

  APP_TAR="$BASE_NAME.app.tar.gz"
  COPYFILE_DISABLE=1 tar -czf "$WORK_DIR/$APP_TAR" -C "$(dirname "$APP")" "$APP_NAME"
  ROOT_ENTRIES="$(tar -tzf "$WORK_DIR/$APP_TAR" | awk -F/ 'NF {print $1}' | sort -u)"
  if [ "$ROOT_ENTRIES" != "$APP_NAME" ]; then
    echo "updater 归档的根条目异常：$ROOT_ENTRIES" >&2
    exit 1
  fi
  TAR_STAGE="$WORK_DIR/tarcheck"
  mkdir "$TAR_STAGE"
  tar -xzf "$WORK_DIR/$APP_TAR" -C "$TAR_STAGE"
  codesign --verify --deep --strict "$TAR_STAGE/$APP_NAME"
  "$PYTHON" "$DESKTOP_DIR/restore_runtime_symlinks.py" \
    --source "$DESKTOP_DIR/target/runtime/QQPetRuntime" \
    --destination "$TAR_STAGE/$APP_NAME/Contents/Resources/backend" \
    --check
  "$TAURI_CLI" signer sign --app-version "$VERSION" \
    --private-key-path "$SIGN_KEY_PATH" "$WORK_DIR/$APP_TAR"
  if [ ! -f "$WORK_DIR/$APP_TAR.sig" ]; then
    echo "updater 签名没有生成 .sig 文件。" >&2
    exit 1
  fi
fi

(
  cd "$WORK_DIR"
  shasum -a 256 "${ARTIFACTS[@]}" > SHA256SUMS.txt
)

# Hard links publish atomically without replacing another run's attachments.
for name in "${ATTACHMENTS[@]}"; do
  if ! ln "$WORK_DIR/$name" "$OUTPUT_DIR/$name"; then
    echo "无法交付附件，已有文件不会被覆盖：$OUTPUT_DIR/$name" >&2
    exit 1
  fi
done
for name in "${ARTIFACTS[@]}"; do
  printf '%s\n' "$OUTPUT_DIR/$name"
done
printf '%s\n' "$OUTPUT_DIR/SHA256SUMS.txt"
