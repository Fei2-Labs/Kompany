#!/usr/bin/env bash
# Install the freshly-built Kompany DMG or .app into /Applications and
# launch it — no manual drag-to-Applications.
#
# Tauri deletes the .app after packaging the DMG. If DMG bundling fails,
# install the .app bundle that Tauri already built. Run after build_desktop.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE_ROOT="$REPO_ROOT/tauri/src-tauri/target/release/bundle"
DMG=""
APP=""

for candidate in "$BUNDLE_ROOT"/dmg/Kompany_*.dmg; do
  if [ -f "$candidate" ]; then
    DMG="$candidate"
    break
  fi
done

if [ -z "$DMG" ]; then
  for candidate in "$BUNDLE_ROOT"/macos/Kompany.app "$BUNDLE_ROOT"/macos/Kompany_*.app; do
    if [ -d "$candidate" ]; then
      APP="$candidate"
      break
    fi
  done
fi

if [ -z "$DMG" ] && [ -z "$APP" ]; then
  echo "No Kompany DMG or .app found under $BUNDLE_ROOT — run build_desktop.sh first." >&2
  exit 1
fi

# Quit a running instance + stray sidecar so we can replace the bundle.
osascript -e 'tell application "Kompany" to quit' 2>/dev/null || true
pkill -f "kompany-server-aarch64" 2>/dev/null || true
sleep 2

# Detach any stray DMG mounts (e.g. a manually opened installer). If one is
# left mounted, LaunchServices may resolve "Kompany" to the /Volumes copy and
# silently launch a stale build instead of /Applications.
for vol in /Volumes/Kompany*; do
  [ -d "$vol" ] && hdiutil detach "$vol" -quiet 2>/dev/null || true
done

if [ -n "$DMG" ]; then
  MNT="$(mktemp -d /tmp/kompany-mnt.XXXX)"
  hdiutil attach "$DMG" -nobrowse -quiet -mountpoint "$MNT"
  APP="$MNT/Kompany.app"
  trap 'hdiutil detach "$MNT" -quiet 2>/dev/null || true' EXIT
  if [ ! -d "$APP" ]; then
    echo "Kompany.app not found in $DMG." >&2
    exit 1
  fi
fi

rm -rf "/Applications/Kompany.app"
ditto "$APP" "/Applications/Kompany.app"
if [ -n "$DMG" ]; then
  hdiutil detach "$MNT" -quiet
  trap - EXIT
fi
echo "installed /Applications/Kompany.app"
# Explicit path — `open -a Kompany` resolves by name and can pick a
# DMG-volume copy if one is mounted.
open "/Applications/Kompany.app"
echo "launched"
