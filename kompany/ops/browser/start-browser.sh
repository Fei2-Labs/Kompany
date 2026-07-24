#!/bin/bash
# Kompany browser launcher — one Brave instance per integration.
# Usage: start-browser.sh <integration-name>
# Reads ~/kompany-browser/config/<name>.env for KOMPANY_BROWSER_PORT, USER_DATA_DIR,
# PROFILE_DIR, LANG, ACCEPT_LANG, WINDOW_SIZE, EXTRA_FLAGS, BROWSER_BIN.
#
# Designed to run as systemd ExecStart (Type=simple): the script does setup then
# `exec`s Brave in the FOREGROUND so systemd tracks the Brave PID directly and
# Restart=always only fires when Brave actually crashes. Safe to run manually too
# (Ctrl+C kills Brave).
#
# === HARD RULE: HEADED ONLY — NEVER headless ===
# Target sites (LinkedIn, X, Weibo, Xiaohongshu, Douyu, …) have aggressive bot
# risk control that flags headless Chromium on sight (navigator.webdriver,
# missing WebGL/GPU, no real layout, no foreground tab focus, CDP detection).
# This launcher REFUSES to start a headless browser. Every instance runs headed
# on the shared Xvfb :99 with a real 1920x1200 framebuffer, real rendering, and
# foreground-tab focus. If you set EXTRA_FLAGS=--headless the launcher errors out.
# If DISPLAY is unset or Xvfb is not reachable, the launcher errors out.
set -u
NAME="${1:?usage: start-browser.sh <integration>}"
CFG="$HOME/kompany-browser/config/${NAME}.env"
if [[ ! -f "$CFG" ]]; then echo "missing config: $CFG" >&2; exit 2; fi
set -a; source "$CFG"; set +a

PORT="${KOMPANY_BROWSER_PORT:?KOMPANY_BROWSER_PORT required in $CFG}"
USER_DATA_DIR="${USER_DATA_DIR:?USER_DATA_DIR required in $CFG}"
PROFILE_DIR="${PROFILE_DIR:-Default}"
LANG_VAL="${LANG:-en-US}"
ACCEPT_LANG="${ACCEPT_LANG:-${LANG_VAL}}"
WINDOW_SIZE="${WINDOW_SIZE:-1920x1200}"
EXTRA_FLAGS="${EXTRA_FLAGS:-}"
BROWSER_BIN="${BROWSER_BIN:-/opt/brave.com/brave/brave}"

# --- HEADED guard: refuse any headless flag, anywhere in EXTRA_FLAGS ---
case " ${EXTRA_FLAGS} " in
  *" --headless "*|*" --headless=new "*|*" --headless=old "*|*" --headless=chrome "*)
    echo "${NAME}: REFUSING --headless. Bot risk control on target sites flags headless Chromium. Use Xvfb :99 (headed)." >&2
    exit 3
    ;;
esac

# --- DISPLAY guard: a real X server must be reachable, or we are de-facto headless ---
if [[ -z "${DISPLAY:-}" ]]; then
  echo "${NAME}: REFUSING to start — DISPLAY is unset (would be headless). Xvfb :99 is the shared display." >&2
  exit 3
fi
if ! pgrep -x Xvfb >/dev/null 2>&1; then
  echo "${NAME}: Xvfb not running on ${DISPLAY}, starting it..." >&2
  setsid Xvfb :99 -screen 0 1920x1200x24 </dev/null >/tmp/xvfb.log 2>&1 &
  sleep 2
  if ! pgrep -x Xvfb >/dev/null 2>&1; then
    echo "${NAME}: Xvfb failed to start — REFUSING to launch browser (would be headless)." >&2
    exit 3
  fi
fi

# Take ownership of the port: ask any stale Brave on $PORT to close CLEANLY first
# (SIGTERM + 30s wait so it flushes cookies/session to disk), SIGKILL only as last resort.
# Use ss to find the PID (avoids pkill -f matching this script own command line).
STALE_PID=$(ss -ltnpH "sport = :${PORT}" 2>/dev/null | grep -oE "pid=[0-9]+" | head -1 | cut -d= -f2)
if [[ -n "${STALE_PID:-}" && "$STALE_PID" != "$$" ]]; then
  echo "${NAME}: asking stale browser PID ${STALE_PID} to close cleanly (30s)..." >&2
  kill -TERM "$STALE_PID" 2>/dev/null || true
  for i in $(seq 1 30); do
    sleep 1
    if ! kill -0 "$STALE_PID" 2>/dev/null; then
      echo "${NAME}: stale browser closed cleanly after ${i}s" >&2
      break
    fi
  done
  if kill -0 "$STALE_PID" 2>/dev/null; then
    echo "${NAME}: stale browser did not close in 30s, SIGKILL" >&2
    kill -9 "$STALE_PID" 2>/dev/null || true
    sleep 2
  fi
  sleep 1
fi

# Clear stale singleton lock (Brave refuses to start if a previous instance left it)
rm -f "${USER_DATA_DIR}/Singleton"* 2>/dev/null

export DISPLAY HOME USER
export LANG="${LANG_VAL}"
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

echo "${NAME}: launching ${BROWSER_BIN} HEADED on ${DISPLAY}, CDP ${PORT}, profile ${USER_DATA_DIR}/${PROFILE_DIR}"
# exec replaces this shell with Brave — systemd now tracks Brave directly.
# No --headless flag is ever passed. --disable-gpu is fine (server has no GPU,
# but the Xvfb framebuffer still gives a real composited layout that passes
# navigator.webdriver / WebGL / getBoundingClientRect bot checks).
exec "${BROWSER_BIN}" \
  --user-data-dir="${USER_DATA_DIR}" \
  --profile-directory="${PROFILE_DIR}" \
  --remote-debugging-port="${PORT}" \
  --no-first-run --no-default-browser-check \
  --lang="${LANG_VAL}" --accept-lang="${ACCEPT_LANG}" \
  --window-position=0,0 --window-size="${WINDOW_SIZE}" \
  --disable-backgrounding-occluded-windows \
  --disable-gpu --disable-dev-shm-usage --no-sandbox \
  --remote-allow-origins=* \
  ${EXTRA_FLAGS}
