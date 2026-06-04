#!/usr/bin/env bash
# Install the freshly-built Kompany DMG straight into /Applications and
# launch it — no manual drag-to-Applications.
#
# Tauri deletes the .app after packaging the DMG, so we mount the DMG,
# copy the .app out with ditto, eject, and launch. Run this right after
# build_desktop.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DMG="$REPO_ROOT/tauri/src-tauri/target/release/bundle/dmg/Kompany_0.1.0_aarch64.dmg"

if [ ! -f "$DMG" ]; then
  echo "DMG not found at $DMG — run build_desktop.sh first." >&2
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

MNT="$(mktemp -d /tmp/kompany-mnt.XXXX)"
hdiutil attach "$DMG" -nobrowse -quiet -mountpoint "$MNT"
rm -rf "/Applications/Kompany.app"
ditto "$MNT/Kompany.app" "/Applications/Kompany.app"
hdiutil detach "$MNT" -quiet
echo "installed → /Applications/Kompany.app"
# Explicit path — `open -a Kompany` resolves by name and can pick a
# DMG-volume copy if one is mounted.
open "/Applications/Kompany.app"
echo "launched"
