#!/usr/bin/env bash
# Build bundled local services without starting a service or connecting a device.
set -euo pipefail

DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="$(cd "$DESKTOP_DIR/../.." && pwd -P)"
PYTHON="${QQPET_BUILD_PYTHON:-$ROOT/.venv/bin/python}"
OUTPUT_DIR="$DESKTOP_DIR/target/runtime"
RUNTIME="$OUTPUT_DIR/QQPetRuntime/QQPetRuntime"
FINGERPRINT_FILE="$OUTPUT_DIR/.runtime-fingerprint"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS runtime 必须在 macOS 上构建。" >&2
  exit 1
fi
if [ ! -x "$PYTHON" ]; then
  echo "找不到项目 Python 环境：$PYTHON" >&2
  exit 1
fi
if [ ! -f "$ROOT/web/dist/index.html" ]; then
  echo "缺少 web/dist/index.html；请先构建前端。" >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
if ! "$PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
  "$PYTHON" -m pip install -r "$DESKTOP_DIR/requirements-build.txt"
fi
if ! "$PYTHON" -c 'import frida, frida_tools' >/dev/null 2>&1; then
  "$PYTHON" -m pip install 'frida==17.17.0' 'frida-tools==14.10.4'
fi
if [ -z "${QQPET_BUILD_ADB:-}" ]; then
  for candidate in "$(command -v adb || true)" /opt/homebrew/bin/adb /usr/local/bin/adb "$HOME/Library/Android/sdk/platform-tools/adb"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      export QQPET_BUILD_ADB="$candidate"
      break
    fi
  done
fi
if [ -z "${QQPET_BUILD_ADB:-}" ] || [ ! -x "$QQPET_BUILD_ADB" ]; then
  echo "找不到可打包的 macOS adb；请设置 QQPET_BUILD_ADB。" >&2
  exit 1
fi

fingerprint() {
  "$PYTHON" - "$ROOT" "$DESKTOP_DIR" "$QQPET_BUILD_ADB" <<'PY'
import hashlib
import importlib.metadata
import platform
import sys
from pathlib import Path

root, desktop, adb = (Path(value) for value in sys.argv[1:])
digest = hashlib.sha256()
digest.update((sys.version + platform.machine()).encode())
files = {
    root / "dashboard.py", root / "config.example.yaml", root / "requirements.txt",
    root / "tools/career_sync.py", desktop / "runtime_launcher.py",
    desktop / "QQPetRuntime.spec", desktop / "requirements-build.txt",
    desktop / "scripts/build-runtime.sh", adb.resolve(),
}
for relative in ("src", "scenarios", "web/dist", "static", "resources/minitouch", "resources/frida-server"):
    folder = root / relative
    files.update(path for path in folder.rglob("*") if path.is_file()
                 and "__pycache__" not in path.parts
                 and (relative not in ("src", "scenarios") or path.suffix == ".py"))
for filename in ("PP-OCRv6_det_tiny.onnx", "PP-OCRv6_rec_tiny.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"):
    files.update(path for path in (root / "models/rapidocr" / filename,
                                  root / "runs/models/rapidocr" / filename) if path.is_file())
files.update((root / "scenarios/assets").glob("*.png"))
for path in sorted(files):
    digest.update(str(path).encode())
    digest.update(path.read_bytes())
for distribution in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
    digest.update(f'{distribution.metadata["Name"]}=={distribution.version}'.encode())
print(digest.hexdigest())
PY
}

mkdir -p "$OUTPUT_DIR"
build_fingerprint="$(fingerprint)"
if [ -x "$RUNTIME" ] && [ -f "$FINGERPRINT_FILE" ] \
    && [ "$(cat "$FINGERPRINT_FILE")" = "$build_fingerprint" ]; then
  echo "本机 runtime 源码、依赖与资源未变，验证现有构建…"
  if "$RUNTIME" self-test; then
    printf '%s\n' "$build_fingerprint" > "$OUTPUT_DIR/QQPetRuntime/.build-fingerprint"
    echo "$OUTPUT_DIR/QQPetRuntime"
    exit 0
  fi
  echo "runtime 自检未通过，重新构建。" >&2
fi

"$PYTHON" -m PyInstaller --noconfirm \
  --distpath "$OUTPUT_DIR" \
  --workpath "$DESKTOP_DIR/target/pyinstaller-runtime" \
  "$DESKTOP_DIR/QQPetRuntime.spec"
"$RUNTIME" self-test
printf '%s\n' "$build_fingerprint" > "$OUTPUT_DIR/QQPetRuntime/.build-fingerprint"
if [ "$(fingerprint)" = "$build_fingerprint" ]; then
  printf '%s\n' "$build_fingerprint" > "$FINGERPRINT_FILE"
else
  rm -f "$FINGERPRINT_FILE"
  echo "构建期间源码或资源发生变化，下次将重新构建 runtime。" >&2
fi
echo "$OUTPUT_DIR/QQPetRuntime"
