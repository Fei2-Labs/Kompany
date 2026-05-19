#!/usr/bin/env bash
# Build the Kompany sidecar binary that the Tauri shell spawns.
#
# Produces `dist/kompany-server-<target-triple>/` containing the
# PyInstaller --onedir bundle. The build_desktop.sh script copies that
# directory into `tauri/src-tauri/binaries/` before invoking the Rust
# bundler.
#
# Requires:
#   * Python 3.11+ with the project installed in the current venv
#   * `pip install '.[api,desktop]'` (pyinstaller + uvicorn + fastapi)
#
# Notes:
#   * We hardcode `--onedir` (not `--onefile`) so cryptography /
#     pydantic-core / anthropic native libs stay on disk and don't have
#     to unpack to a temp dir on every launch.
#   * `--collect-all` for the heavy native packages because their
#     dynamic imports trip PyInstaller's static scanner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# ---------------------------------------------------------------------
# Target triple (matches `rustc -vV` host so Tauri's sidecar discovery
# finds the binary under the name it expects).
# ---------------------------------------------------------------------
detect_triple() {
  if command -v rustc >/dev/null 2>&1; then
    rustc -vV | awk '/^host: /{print $2}'
    return
  fi
  # Fallback for boxes without rustc installed yet.
  local arch
  arch="$(uname -m)"
  case "$(uname -s)" in
    Darwin)
      case "${arch}" in
        arm64|aarch64) echo "aarch64-apple-darwin" ;;
        x86_64)         echo "x86_64-apple-darwin" ;;
        *) echo "unknown-apple-darwin" ;;
      esac
      ;;
    Linux)
      case "${arch}" in
        x86_64) echo "x86_64-unknown-linux-gnu" ;;
        aarch64) echo "aarch64-unknown-linux-gnu" ;;
        *) echo "unknown-unknown-linux-gnu" ;;
      esac
      ;;
    MINGW*|MSYS*|CYGWIN*)
      echo "x86_64-pc-windows-msvc"
      ;;
    *)
      echo "unknown-unknown-unknown"
      ;;
  esac
}

TRIPLE="${KOMPANY_TARGET_TRIPLE:-$(detect_triple)}"
BINARY_NAME="kompany-server-${TRIPLE}"
DIST_ROOT="${SCRIPT_DIR}/dist"
BUILD_ROOT="${SCRIPT_DIR}/build"

echo "==> Building Kompany sidecar for ${TRIPLE}"
echo "    output: ${DIST_ROOT}/${BINARY_NAME}/"

# Sanity-check pyinstaller is reachable.
if ! python -c "import PyInstaller" >/dev/null 2>&1; then
  echo "error: PyInstaller is not installed in the current Python env." >&2
  echo "       Install with: pip install '.[api,desktop]'" >&2
  exit 2
fi

# Clean prior output so a stale build can't poison the bundle.
rm -rf "${DIST_ROOT}/${BINARY_NAME}" "${BUILD_ROOT}/${BINARY_NAME}"

python -m PyInstaller \
  --noconfirm \
  --onedir \
  --name "${BINARY_NAME}" \
  --distpath "${DIST_ROOT}" \
  --workpath "${BUILD_ROOT}" \
  --specpath "${BUILD_ROOT}" \
  --collect-all kompany \
  --collect-all anthropic \
  --collect-all openai \
  --collect-all cryptography \
  --collect-all pydantic \
  --collect-all pydantic_core \
  --collect-all fastapi \
  --collect-all uvicorn \
  --collect-all starlette \
  --collect-all rich \
  --collect-all typer \
  --collect-all keyring \
  --hidden-import sqlite3 \
  --hidden-import anyio \
  --hidden-import h11 \
  sidecar_main.py

echo "==> Sidecar bundle ready: ${DIST_ROOT}/${BINARY_NAME}/"
