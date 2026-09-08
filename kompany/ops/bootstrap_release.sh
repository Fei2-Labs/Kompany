#!/usr/bin/env bash
# One-time migration of a Kompany server to the release layout, after which
# updates are one click (Settings → Update / `kompany update apply`).
#
#   <data_dir>/releases/<version>/venv   ← Core + Pro wheels from GitHub Releases
#   <data_dir>/releases/current -> <version>
#   systemd unit ExecStart -> releases/current/venv/bin/python (hardened unit,
#   KOMPANY_SUPERVISED=systemd so the updater can exit for a restart)
#
# Usage (on the server, as the daemon user; sudo is used only for the unit):
#   bash bootstrap_release.sh --core-version 0.1.6 [--pro-wheel /tmp/kompany_pro-0.1.5-py3-none-any.whl]
#                             [--data-dir ~/.kompany] [--role maintainer|customer] [--python python3]
#
# The Pro wheel is private: download it on your own machine
# (`gh release download v0.1.5 -R Fei2-Labs/kompany-pro -p '*.whl'`) and scp it
# here. No GitHub credential ever lands on the server.
set -euo pipefail

CORE_VERSION=""; PRO_WHEEL=""; DATA_DIR="${KOMPANY_DATA_DIR:-$HOME/.kompany}"; ROLE="customer"; PYTHON="python3"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --core-version) CORE_VERSION="$2"; shift 2 ;;
    --pro-wheel) PRO_WHEEL="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$CORE_VERSION" ]] || { echo "--core-version is required (e.g. 0.1.6)" >&2; exit 2; }
DATA_DIR="$(cd "$(dirname "$DATA_DIR")" && pwd)/$(basename "$DATA_DIR")"
REL="$DATA_DIR/releases/$CORE_VERSION"
BASE="https://github.com/Fei2-Labs/Kompany/releases/download/v$CORE_VERSION"
WHEEL="kompany-$CORE_VERSION-py3-none-any.whl"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "==> [1/6] fetching release manifest + wheel"
curl -fsSL -o "$TMP/release-manifest.json" "$BASE/release-manifest.json"
curl -fsSL -o "$TMP/$WHEEL" "$BASE/$WHEEL"
EXPECTED="$($PYTHON -c "import json,sys; print(json.load(open(sys.argv[1]))['artifacts'][sys.argv[2]]['sha256'])" "$TMP/release-manifest.json" "$WHEEL")"
ACTUAL="$(sha256sum "$TMP/$WHEEL" | cut -d' ' -f1)"
[[ "$EXPECTED" == "$ACTUAL" ]] || { echo "sha256 mismatch for $WHEEL" >&2; exit 1; }
echo "    sha256 ok ($ACTUAL)"
if command -v gh >/dev/null 2>&1; then
  gh attestation verify "$TMP/$WHEEL" --repo Fei2-Labs/Kompany && echo "    provenance verified" || { echo "attestation failed" >&2; exit 1; }
else
  echo "    (gh not installed — provenance not checked; sha256 verified)"
fi

echo "==> [2/6] backup before anything changes"
if command -v kompany >/dev/null 2>&1; then
  KOMPANY_DATA_DIR="$DATA_DIR" kompany backup --label "pre-release-$CORE_VERSION" || echo "    (backup via old install failed — continuing; sqlite file is untouched by this script)"
fi

echo "==> [3/6] venv at $REL/venv"
mkdir -p "$REL"
"$PYTHON" -m venv --clear "$REL/venv"
"$REL/venv/bin/pip" install --quiet --upgrade pip
"$REL/venv/bin/pip" install --quiet "$TMP/$WHEEL[api,mcp]"
if [[ -n "$PRO_WHEEL" ]]; then
  "$REL/venv/bin/pip" install --quiet "$PRO_WHEEL"
fi
GOT="$("$REL/venv/bin/python" -c 'import kompany; print(kompany.__version__)')"
[[ "$GOT" == "$CORE_VERSION" ]] || { echo "venv reports kompany $GOT, expected $CORE_VERSION" >&2; exit 1; }
"$REL/venv/bin/python" -c 'import json; from importlib.resources import files; print("    release identity:", json.loads(files("kompany").joinpath("release.json").read_text())["source"])' || echo "    (no release.json in wheel — was this built by the Release workflow?)"

echo "==> [4/6] releases/current -> $CORE_VERSION"
ln -sfn "$CORE_VERSION" "$DATA_DIR/releases/current"

echo "==> [5/6] supervisor unit + installation role"
if [[ "$(uname -s)" == "Linux" ]]; then
  UNIT=/etc/systemd/system/kompany-daemon.service
  sudo mkdir -p /etc/kompany && echo "$ROLE" | sudo tee /etc/kompany/installation_role >/dev/null && sudo chmod 644 /etc/kompany/installation_role
  if [[ -f "$UNIT" ]]; then
    # Existing unit: keep its ExecStart arguments (--host/--port), env and
    # user; only repoint the interpreter at releases/current and add what the
    # updater needs. A full hardened rewrite is `kompany daemon install`.
    sudo cp "$UNIT" "$UNIT.bak-$(date +%Y%m%d%H%M%S)"
    sudo "$PYTHON" - "$UNIT" "$DATA_DIR" <<'PY'
import re, sys, pathlib
unit, data_dir = pathlib.Path(sys.argv[1]), sys.argv[2]
s = unit.read_text()
s = re.sub(r"^ExecStart=\S+\s+-m\s+kompany\.interfaces\.daemon_main(.*)$",
           lambda m: f"ExecStart={data_dir}/releases/current/venv/bin/python3 -m kompany.interfaces.daemon_main{m.group(1)}",
           s, flags=re.M)
if "KOMPANY_SUPERVISED" not in s:
    s = s.replace("[Service]\n", "[Service]\nEnvironment=KOMPANY_SUPERVISED=systemd\n", 1)
unit.write_text(s)
PY
    if [[ -f "$DATA_DIR/daemon.env" ]] && ! grep -q "daemon.env" "$UNIT"; then
      sudo sed -i "s#^\[Service\]#[Service]\nEnvironmentFile=$DATA_DIR/daemon.env#" "$UNIT"
    fi
    sudo systemctl daemon-reload
    sudo systemctl restart kompany-daemon.service
  else
    sudo -E KOMPANY_DATA_DIR="$DATA_DIR" SUDO_USER="${SUDO_USER:-$USER}" \
      "$DATA_DIR/releases/current/venv/bin/kompany" daemon install --role "$ROLE" --data-dir "$DATA_DIR"
    sudo systemctl daemon-reload && sudo systemctl restart kompany-daemon.service
  fi
  echo "    NOTE: a non-loopback bind needs WEB_DASHBOARD_TOKEN — put it in $DATA_DIR/daemon.env (chmod 600) before the first start."
else
  KOMPANY_DATA_DIR="$DATA_DIR" "$DATA_DIR/releases/current/venv/bin/kompany" daemon install --data-dir "$DATA_DIR"
fi

echo "==> [6/6] verify"
sleep 5
KOMPANY_DATA_DIR="$DATA_DIR" "$DATA_DIR/releases/current/venv/bin/kompany" doctor || true
KOMPANY_DATA_DIR="$DATA_DIR" "$DATA_DIR/releases/current/venv/bin/kompany" update status || true
echo "Done. From now on: Settings → Update, or \`kompany update apply\`."
