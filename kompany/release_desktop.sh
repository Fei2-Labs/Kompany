#!/usr/bin/env bash
# Kompany Desktop release: build signed artifacts, then publish them as a
# GitHub release plus the auto-update feed that installed apps poll.
#
#   kompany/release_desktop.sh build
#       On a Mac. Runs build_desktop.sh with the updater signing key and
#       stages the artifacts under tauri/src-tauri/target/desktop-release/.
#       Requires TAURI_SIGNING_PRIVATE_KEY (key file path or contents) and
#       TAURI_SIGNING_PRIVATE_KEY_PASSWORD. Without them Tauri refuses to
#       build, because tauri.conf.json enables createUpdaterArtifacts.
#
#   kompany/release_desktop.sh publish <staged-dir> [notes-file]
#       Anywhere with an authenticated `gh` and the release commit pushed.
#       1. Creates release `desktop-v<version>` with the dmg and the signed
#          update bundle. It is never marked "latest": the engine updater
#          reads releases/latest and must keep seeing engine wheels.
#       2. Replaces latest.json on the rolling `desktop-updater` pre-release.
#          That URL is the endpoint in tauri.conf.json plugins.updater, so
#          every installed app picks the new version up on its next check.
#       Publishing another platform for the same version merges into
#       latest.json instead of replacing it.
#
# Bump the version in both tauri/src-tauri/tauri.conf.json and
# tauri/src-tauri/Cargo.toml first: the updater only offers versions that
# are semver-greater than the running app.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TAURI_DIR="${REPO_ROOT}/tauri/src-tauri"
FEED_TAG="desktop-updater"

die() { echo "error: $*" >&2; exit 2; }

conf_version() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' \
    "${TAURI_DIR}/tauri.conf.json"
}

# Tauri updater platform key for a Rust target triple.
platform_key() {
  case "$1" in
    aarch64-apple-darwin) echo "darwin-aarch64" ;;
    x86_64-apple-darwin) echo "darwin-x86_64" ;;
    *) die "no updater platform mapping for target ${1}" ;;
  esac
}

cmd_build() {
  [[ "$(uname -s)" == "Darwin" ]] || die "build runs on macOS"
  [[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]] || die "TAURI_SIGNING_PRIVATE_KEY is not set"
  [[ -n "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}" ]] || die "TAURI_SIGNING_PRIVATE_KEY_PASSWORD is not set"
  command -v rustc >/dev/null || die "rustc not found"

  local version triple platform arch out bundle
  version="$(conf_version)"
  triple="$(rustc -vV | awk '/^host: /{print $2}')"
  platform="$(platform_key "${triple}")"
  arch="${triple%%-*}"

  # CI=true makes Tauri pass --skip-jenkins to bundle_dmg.sh, skipping the
  # AppleScript that arranges the dmg window in Finder. That step needs a
  # logged-in GUI session and fails over SSH; the dmg works the same.
  CI="${CI:-true}" "${SCRIPT_DIR}/build_desktop.sh"

  bundle="${TAURI_DIR}/target/release/bundle"
  out="${TAURI_DIR}/target/desktop-release/${version}-${platform}"
  rm -rf "${out}" && mkdir -p "${out}"

  local tarball="${bundle}/macos/Kompany.app.tar.gz"
  [[ -f "${tarball}" && -f "${tarball}.sig" ]] || die "missing signed update bundle at ${tarball}{,.sig}"
  local dmg
  dmg="$(ls "${bundle}"/dmg/*.dmg 2>/dev/null | head -1 || true)"
  [[ -n "${dmg}" ]] || die "missing dmg under ${bundle}/dmg"

  cp "${tarball}" "${out}/Kompany_${version}_${arch}.app.tar.gz"
  cp "${tarball}.sig" "${out}/Kompany_${version}_${arch}.app.tar.gz.sig"
  cp "${dmg}" "${out}/Kompany_${version}_${arch}.dmg"
  printf '{"version": "%s", "platform": "%s", "arch": "%s"}\n' "${version}" "${platform}" "${arch}" \
    > "${out}/meta.json"

  echo
  echo "==> Staged ${version} (${platform}) in ${out}"
  ls -l "${out}"
}

cmd_publish() {
  local staged="${1:-}" notes_file="${2:-}"
  [[ -d "${staged}" && -f "${staged}/meta.json" ]] || die "usage: $0 publish <staged-dir> [notes-file]"
  command -v gh >/dev/null || die "gh not found"

  local version platform arch tag repo sha
  version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "${staged}/meta.json")"
  platform="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["platform"])' "${staged}/meta.json")"
  arch="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["arch"])' "${staged}/meta.json")"
  tag="desktop-v${version}"
  repo="$(cd "${REPO_ROOT}" && gh repo view --json nameWithOwner --jq .nameWithOwner)"
  sha="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
  [[ -n "$(git -C "${REPO_ROOT}" branch -r --contains "${sha}")" ]] \
    || die "HEAD ${sha} is not on any remote branch; push it first"

  local tarball="Kompany_${version}_${arch}.app.tar.gz"
  local dmg="Kompany_${version}_${arch}.dmg"
  local work
  work="$(mktemp -d)"
  trap 'rm -rf "${work}"' RETURN

  local notes="${work}/notes.md"
  if [[ -n "${notes_file}" ]]; then
    cp "${notes_file}" "${notes}"
  else
    cat > "${notes}" <<EOF
Kompany Desktop ${version}.

Installed apps update themselves: the new version downloads in the background and a **Restart to update** button appears in the top bar.

**First install** — download \`${dmg}\` and drag Kompany to Applications. The app is not notarized yet, so macOS blocks the first launch; run this once, then open it normally:

\`\`\`
xattr -dr com.apple.quarantine /Applications/Kompany.app
\`\`\`
EOF
  fi

  if gh release view "${tag}" --repo "${repo}" >/dev/null 2>&1; then
    echo "==> ${tag} exists; adding ${platform} assets"
    gh release upload "${tag}" --repo "${repo}" --clobber \
      "${staged}/${tarball}" "${staged}/${tarball}.sig" "${staged}/${dmg}"
  else
    echo "==> Creating ${tag} at ${sha}"
    gh release create "${tag}" --repo "${repo}" --target "${sha}" --latest=false \
      --title "Kompany Desktop ${version}" --notes-file "${notes}" \
      "${staged}/${tarball}" "${staged}/${tarball}.sig" "${staged}/${dmg}"
  fi

  # latest.json: merge into the current feed when it already announces this
  # version (another platform), otherwise start fresh for the new version.
  local feed="${work}/latest.json" previous="${work}/previous.json"
  if gh release view "${FEED_TAG}" --repo "${repo}" >/dev/null 2>&1; then
    gh release download "${FEED_TAG}" --repo "${repo}" --pattern latest.json \
      --output "${previous}" 2>/dev/null || true
  fi
  python3 - "${previous}" "${feed}" "${version}" "${platform}" \
    "https://github.com/${repo}/releases/download/${tag}/${tarball}" \
    "${staged}/${tarball}.sig" "${notes}" <<'PY'
import datetime, json, pathlib, sys
previous, feed, version, platform, url, sig_path, notes_path = sys.argv[1:]
prev = {}
if pathlib.Path(previous).is_file():
    try:
        prev = json.loads(pathlib.Path(previous).read_text())
    except ValueError:
        prev = {}
platforms = prev.get("platforms", {}) if prev.get("version") == version else {}
platforms[platform] = {"signature": pathlib.Path(sig_path).read_text().strip(), "url": url}
doc = {
    "version": version,
    "notes": pathlib.Path(notes_path).read_text().split("\n\n")[0].strip(),
    "pub_date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "platforms": platforms,
}
pathlib.Path(feed).write_text(json.dumps(doc, indent=2) + "\n")
PY

  if ! gh release view "${FEED_TAG}" --repo "${repo}" >/dev/null 2>&1; then
    echo "==> Creating update feed release ${FEED_TAG}"
    gh release create "${FEED_TAG}" --repo "${repo}" --target "${sha}" --prerelease --latest=false \
      --title "Kompany Desktop update feed" \
      --notes "Machine-readable update feed polled by installed Kompany Desktop apps. Download releases from the desktop-v* tags instead."
  fi
  gh release upload "${FEED_TAG}" --repo "${repo}" --clobber "${feed}"

  local endpoint="https://github.com/${repo}/releases/download/${FEED_TAG}/latest.json"
  local served
  served="$(curl -fsSL "${endpoint}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["version"], ",".join(sorted(d["platforms"])))')"
  echo "==> Feed now serves: ${served}"
  echo "    ${endpoint}"
}

case "${1:-}" in
  build) shift; cmd_build "$@" ;;
  publish) shift; cmd_publish "$@" ;;
  *) die "usage: $0 build | publish <staged-dir> [notes-file]" ;;
esac
