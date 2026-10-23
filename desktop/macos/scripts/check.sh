#!/usr/bin/env bash
# Fast, non-bundling verification for the shell.
set -euo pipefail

DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DESKTOP_DIR"

cargo fmt --check
CARGO_INCREMENTAL=0 cargo check
node tests/launcher.test.mjs
bash -n scripts/build-macos.sh
bash -n scripts/verify.sh
