#!/usr/bin/env bash
# End-to-end Kompany desktop build.
#
# Pipeline:
#   1. Run build_sidecar.sh → produces dist/kompany-server-<triple>/
#   2. Copy that into tauri/src-tauri/binaries/ so Tauri picks it up as
#      an external binary at bundle time.
#   3. Run `cargo tauri build` from the tauri/ directory; the resulting
#      installers land in tauri/src-tauri/target/release/bundle/.
#
# Prereqs:
#   * Rust toolchain + tauri-cli: `cargo install tauri-cli --version "^2.0"`
#   * Python venv with `pip install '.[api,desktop]'` (PyInstaller, etc.)
#   * On macOS: Xcode command-line tools
#   * On Linux: see docs/context/distribution.md for the apt-get list
#   * On Windows: WebView2 runtime ships with Win11; install on Win10
#
# This script does NOT do code-signing or notarization — that's a v2
# release-engineering task. Outputs are unsigned and require the user
# to bypass Gatekeeper / SmartScreen on first launch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------
# Stage 1 — sidecar bundle.
# ---------------------------------------------------------------------
echo "==> [1/3] Building Python sidecar..."
"${SCRIPT_DIR}/build_sidecar.sh"

# ---------------------------------------------------------------------
# Stage 2 — copy sidecar into Tauri's external-binary slot.
# ---------------------------------------------------------------------
if command -v rustc >/dev/null 2>&1; then
  TRIPLE="$(rustc -vV | awk '/^host: /{print $2}')"
else
  TRIPLE="${KOMPANY_TARGET_TRIPLE:-}"
fi
if [[ -z "${TRIPLE}" ]]; then
  echo "error: cannot determine target triple (install rustc or set KOMPANY_TARGET_TRIPLE)" >&2
  exit 2
fi

SIDECAR_DIR="${SCRIPT_DIR}/dist/kompany-server-${TRIPLE}"
TARGET_BIN_DIR="${REPO_ROOT}/tauri/src-tauri/binaries"

if [[ ! -d "${SIDECAR_DIR}" ]]; then
  echo "error: expected sidecar bundle at ${SIDECAR_DIR}" >&2
  exit 2
fi

echo "==> [2/3] Staging sidecar at ${TARGET_BIN_DIR}/..."
mkdir -p "${TARGET_BIN_DIR}"
# Wipe stale copies so an old build can't poison the bundle.
rm -rf "${TARGET_BIN_DIR}/kompany-server-${TRIPLE}"
cp -R "${SIDECAR_DIR}" "${TARGET_BIN_DIR}/kompany-server-${TRIPLE}"

# Tauri's externalBin discovery looks for `<name>-<triple>` next to
# `<name>`. PyInstaller --onedir gives us a directory; we symlink the
# inner executable up under the triple-suffixed name so Tauri's bundle
# step can find it.
SIDECAR_EXE="${TARGET_BIN_DIR}/kompany-server-${TRIPLE}/kompany-server-${TRIPLE}"
if [[ "${TRIPLE}" == *"windows"* ]]; then
  SIDECAR_EXE="${SIDECAR_EXE}.exe"
fi
if [[ -f "${SIDECAR_EXE}" ]]; then
  ln -sf "${SIDECAR_EXE}" "${TARGET_BIN_DIR}/kompany-server-${TRIPLE}.bin"
fi

# ---------------------------------------------------------------------
# Stage 3 — cargo tauri build.
# ---------------------------------------------------------------------
echo "==> [3/3] Running cargo tauri build..."
cd "${REPO_ROOT}/tauri/src-tauri"
cargo tauri build

echo
echo "==> Done. Installers under:"
echo "    ${REPO_ROOT}/tauri/src-tauri/target/release/bundle/"
