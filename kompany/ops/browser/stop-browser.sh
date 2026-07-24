#!/bin/bash
# Stop a Kompany browser integration cleanly (SIGTERM + 30s, then SIGKILL).
# Usage: stop-browser.sh <integration-name>
# For systemd-managed integrations, prefer: systemctl stop kompany-browser@<name>
set -u
NAME="${1:?usage: stop-browser.sh <integration>}"
CFG="$HOME/kompany-browser/config/${NAME}.env"
[[ -f "$CFG" ]] || { echo "missing config: $CFG" >&2; exit 2; }
set -a; source "$CFG"; set +a
PORT="${KOMPANY_BROWSER_PORT:?}"
PID=$(ss -ltnpH "sport = :${PORT}" 2>/dev/null | grep -oE "pid=[0-9]+" | head -1 | cut -d= -f2)
if [[ -z "${PID:-}" ]]; then echo "${NAME}: not running on ${PORT}"; exit 0; fi
echo "${NAME}: SIGTERM PID ${PID}, waiting 30s for clean cookie flush..."
kill -TERM "$PID" 2>/dev/null || true
for i in $(seq 1 30); do
  sleep 1; kill -0 "$PID" 2>/dev/null || { echo "${NAME}: closed cleanly after ${i}s"; exit 0; }
done
echo "${NAME}: SIGKILL"; kill -9 "$PID" 2>/dev/null || true
