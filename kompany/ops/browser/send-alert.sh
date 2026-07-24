#!/bin/bash
# send-alert.sh <source>  — file a system alert into the Kompany inbox.
# Used by kompany-alert@.service (systemd OnFailure hook). Best-effort:
# exits 0 even if the engine is unreachable so systemd does not recurse.
set -u
SOURCE="${1:?usage: send-alert.sh <source>}"
ENGINE="${KOMPANY_ENGINE_URL:-http://127.0.0.1:55352}"
TITLE="${ALERT_TITLE:-Service ${SOURCE} stopped}"
MESSAGE="${ALERT_MESSAGE:-systemd unit exited non-zero; check journalctl for details}"
# Severity defaults to high; override via ALERT_SEVERITY for less urgent sources.
SEV="${ALERT_SEVERITY:-high}"
curl -s --max-time 10 -X POST "${ENGINE}/alerts" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json,sys; print(json.dumps({\"source\":sys.argv[1],\"severity\":sys.argv[2],\"title\":sys.argv[3],\"message\":sys.argv[4]}))" "$SOURCE" "$SEV" "$TITLE" "$MESSAGE")" \
  2>&1 || echo "alert: engine unreachable, alert not filed (best-effort)"
echo "alert filed: source=${SOURCE}"
