"""Daemon process management ops — ``kompany daemon run|install|uninstall|status``.

Logic home for the daemon CLI sub-app (06-12-daemon-tick-loop PR2).
``cli.py`` renders only (interfaces spec: no business logic in an
interface file). These operations are machine-local process management
and deliberately CLI-only (PRD D5) — tick observability instead joins
the existing status/observability operations on all four interfaces.

Key decisions implemented here:

* **D2 — discovery file is the lock.** ``run_daemon`` refuses to start
  when ``<data_dir>/server.json`` points at a healthy live server
  (validated via :func:`kompany.interfaces.mcp_proxy.discover_sidecar`:
  pid alive + ``/health`` probe). Exactly one engine ever ticks.
* **D4 — launchd (macOS).** ``install_launchd`` writes
  ``~/Library/LaunchAgents/com.kompany.daemon.plist`` with
  KeepAlive/RunAtLoad, resolving ProgramArguments at install time:
  bundled PyInstaller server binary if the desktop app is installed
  (no Python dependency for founders), else the current interpreter +
  ``kompany.interfaces.daemon_main``. ``launchctl bootstrap`` is
  best-effort — its failure is recorded, never fatal to the install.
* **D4b — systemd (Linux).** ``install_systemd`` writes
  ``/etc/systemd/system/kompany-daemon.service`` with Restart=always,
  resolving ExecStart at install time the same way as launchd.
  ``systemctl daemon-reload`` + ``enable --now`` is best-effort —
  its failure is recorded, never fatal to the install. Counterpart
  of the launchd path for VPS deployment (07-14-cloud-deploy).

All subprocess calls are list-form (never ``shell=True``).
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


def pwd_get_username() -> str:
    """Current username via pwd (fallback when USER env is unset)."""
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name

from kompany.interfaces import mcp_proxy

LAUNCHD_LABEL = "com.kompany.daemon"
SYSTEMD_UNIT_NAME = "kompany-daemon.service"
SYSTEMD_UNIT_PATH = Path("/etc/systemd/system") / SYSTEMD_UNIT_NAME
# Bundled desktop server binary (preferred ProgramArguments target —
# founders without a Python install still get a 24/7 daemon).
BUNDLED_SERVER_BINARY = Path(
    "/Applications/Kompany.app/Contents/Resources/binaries/"
    "kompany-server-aarch64-apple-darwin/kompany-server-aarch64-apple-darwin"
)
STATUS_PROBE_TIMEOUT_SECONDS = 2.0


def _resolve_data_dir(data_dir: Path | None) -> Path:
    return data_dir if data_dir is not None else mcp_proxy.default_data_dir()


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def _run_launchctl(args: list[str]) -> dict[str, Any]:
    """Run one launchctl command; never raises. Returns {ok, returncode, error}."""
    try:
        proc = subprocess.run(
            ["launchctl", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {"ok": False, "returncode": None, "error": str(exc)}
    error = None
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "").strip() or f"launchctl exited {proc.returncode}"
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "error": error}


def _launchd_loaded() -> bool:
    """True when launchd currently has the agent loaded in the gui domain."""
    if sys.platform != "darwin":
        return False
    return _run_launchctl(["print", f"{_launchctl_domain()}/{LAUNCHD_LABEL}"])["ok"]


def _run_systemctl(args: list[str]) -> dict[str, Any]:
    """Run one systemctl command; never raises. Returns {ok, returncode, error}."""
    try:
        proc = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {"ok": False, "returncode": None, "error": str(exc)}
    error = None
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "").strip() or f"systemctl exited {proc.returncode}"
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "error": error}


def _systemd_active() -> bool:
    """True when the systemd unit is currently active (running)."""
    if sys.platform != "linux":
        return False
    return _run_systemctl(["is-active", "--quiet", SYSTEMD_UNIT_NAME])["ok"]


def _systemd_enabled() -> bool:
    """True when the systemd unit is enabled (starts at boot)."""
    if sys.platform != "linux":
        return False
    return _run_systemctl(["is-enabled", "--quiet", SYSTEMD_UNIT_NAME])["ok"]


def _fetch_ticker(port: int) -> dict[str, Any] | None:
    """Pull the ``ticker`` block from the running server's ``GET /status``."""
    url = f"http://127.0.0.1:{port}/status"
    try:
        with urllib.request.urlopen(url, timeout=STATUS_PROBE_TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                return None
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    ticker = body.get("ticker")
    return ticker if isinstance(ticker, dict) else None


# ---------------------------------------------------------------------------
# Operations (one per CLI sub-command)
# ---------------------------------------------------------------------------


def daemon_status(data_dir: Path | None = None) -> dict[str, Any]:
    """Truthful daemon report: live server, platform supervisor, ticker block."""
    data_dir = _resolve_data_dir(data_dir)
    info = mcp_proxy.discover_sidecar(data_dir)
    if info is not None:
        server = {
            "running": True,
            "port": info["port"],
            "pid": info["pid"],
            # Files written before 06-12 carry no source label; the
            # only writer back then was the Tauri sidecar.
            "source": info.get("source", "sidecar"),
        }
        ticker = _fetch_ticker(info["port"])
    else:
        server = {"running": False, "port": None, "pid": None, "source": "none"}
        ticker = None
    if sys.platform == "darwin":
        supervisor = _launchd_status_block()
    elif sys.platform == "linux":
        supervisor = _systemd_status_block()
    else:
        supervisor = {"installed": False, "loaded": False, "unit_path": None}
    return {"server": server, "supervisor": supervisor, "ticker": ticker}


def _launchd_status_block() -> dict[str, Any]:
    plist_path = _plist_path()
    return {
        "type": "launchd",
        "installed": plist_path.exists(),
        "plist_path": str(plist_path),
        "loaded": _launchd_loaded(),
    }


def _systemd_status_block() -> dict[str, Any]:
    return {
        "type": "systemd",
        "installed": SYSTEMD_UNIT_PATH.exists(),
        "unit_path": str(SYSTEMD_UNIT_PATH),
        "loaded": _systemd_active(),
        "enabled": _systemd_enabled(),
    }


def resolve_program_arguments() -> list[str]:
    """ProgramArguments for the LaunchAgent, resolved at install time (D4)."""
    if BUNDLED_SERVER_BINARY.exists():
        return [str(BUNDLED_SERVER_BINARY), "--host", "127.0.0.1", "--port", "0"]
    return [sys.executable, "-m", "kompany.interfaces.daemon_main"]


def resolve_daemon_path() -> str:
    """PATH for the LaunchAgent's EnvironmentVariables.

    launchd gives a login-shell-less agent a minimal PATH
    (``/usr/bin:/bin:/usr/sbin:/sbin``), so in ``claude_subscription`` /
    CLI-harness modes the engine can't find the ``claude`` / ``codex`` /
    ``opencode`` binaries (they live in ``~/.local/bin`` or Homebrew) →
    ``LLMUnavailable: CLI not found on PATH`` and the daemon ticks but does
    no LLM work. Prepend the user-local + Homebrew bins so the daemon
    resolves the same CLIs as the founder's interactive shell.
    """
    candidates = [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",  # Apple-silicon Homebrew
        "/usr/local/bin",     # Intel Homebrew / common user installs
    ]
    base = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin").split(":")
    seen: set[str] = set()
    ordered: list[str] = []
    for d in candidates + base:
        if d and d not in seen:
            seen.add(d)
            ordered.append(d)
    return ":".join(ordered)


def install_launchd(data_dir: Path | None = None) -> dict[str, Any]:
    """Write + (best-effort) bootstrap the com.kompany.daemon LaunchAgent."""
    if sys.platform != "darwin":
        return {
            "installed": False,
            "plist_path": None,
            "program_arguments": None,
            "bootstrap": None,
            "error": (
                "kompany daemon install is macOS-only for now (launchd). "
                "On other platforms run 'kompany daemon run' under your "
                "own process supervisor."
            ),
        }
    data_dir = _resolve_data_dir(data_dir)
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    program_arguments = resolve_program_arguments()
    plist: dict[str, Any] = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": program_arguments,
        "KeepAlive": True,
        "RunAtLoad": True,
        "StandardOutPath": str(logs_dir / "daemon.out.log"),
        "StandardErrPath": str(logs_dir / "daemon.err.log"),
        # Pin the daemon to the data_dir chosen at install time so a
        # login-shell-less launchd context resolves the same engine
        # state as the founder's CLI. PATH is set so CLI-harness modes
        # (claude_subscription etc.) can find their binaries — launchd's
        # default PATH omits ~/.local/bin and Homebrew.
        "EnvironmentVariables": {
            "KOMPANY_DATA_DIR": str(data_dir),
            "PATH": resolve_daemon_path(),
        },
    }
    plist_path = _plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)
    # Best-effort load: a failure (e.g. already bootstrapped, SIP
    # quirks) is reported, but the install itself stands — the agent
    # still loads on next login via RunAtLoad.
    bootstrap = _run_launchctl(
        ["bootstrap", _launchctl_domain(), str(plist_path)]
    )
    return {
        "installed": True,
        "plist_path": str(plist_path),
        "program_arguments": program_arguments,
        "bootstrap": bootstrap,
        "error": None,
    }


def uninstall_launchd() -> dict[str, Any]:
    """Bootout + remove the LaunchAgent plist. Idempotent."""
    plist_path = _plist_path()
    existed = plist_path.exists()
    if sys.platform == "darwin":
        bootout = _run_launchctl(
            ["bootout", f"{_launchctl_domain()}/{LAUNCHD_LABEL}"]
        )
    else:
        bootout = {"ok": True, "returncode": None, "error": None}
    try:
        plist_path.unlink(missing_ok=True)
    except OSError as exc:
        return {
            "removed": False,
            "plist_path": str(plist_path),
            "bootout": bootout,
            "error": str(exc),
        }
    return {
        "removed": existed,
        "plist_path": str(plist_path),
        "bootout": bootout,
        "error": None,
    }


def _systemd_unit_content(
    data_dir: Path,
    program_arguments: list[str],
    path_env: str,
    user: str | None = None,
) -> str:
    """Render the systemd unit file body."""
    exec_start = " ".join(program_arguments)
    user_line = f"User={user}\n" if user else ""
    return (
        "[Unit]\n"
        "Description=Kompany 24/7 daemon server\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        f"Type=simple\n"
        f"{user_line}"
        f"ExecStart={exec_start}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        f"Environment=KOMPANY_DATA_DIR={data_dir}\n"
        f"Environment=PATH={path_env}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def install_systemd(data_dir: Path | None = None) -> dict[str, Any]:
    """Write + (best-effort) enable the kompany-daemon systemd unit (Linux).

    Counterpart of :func:`install_launchd` for VPS deployment. Writes
    ``/etc/systemd/system/kompany-daemon.service`` with Restart=always,
    then runs ``systemctl daemon-reload`` + ``enable --now`` (best-effort
    — failures are recorded, never fatal to the install). Requires root
    (or sudo) to write to ``/etc/systemd/system``.
    """
    if sys.platform != "linux":
        return {
            "installed": False,
            "unit_path": None,
            "exec_start": None,
            "reload": None,
            "enable": None,
            "error": (
                "kompany daemon install is Linux-only for systemd. "
                "On macOS use launchd (the default)."
            ),
        }
    data_dir = _resolve_data_dir(data_dir)
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    program_arguments = resolve_program_arguments()
    path_env = resolve_daemon_path()
    # Run as the current user so the daemon accesses the right home
    # dir, venv, and CLI harness tools (claude/codex/opencode). Under
    # sudo, USER/root is wrong — prefer SUDO_USER (the invoking user).
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or pwd_get_username()
    unit_body = _systemd_unit_content(data_dir, program_arguments, path_env, user=user)
    try:
        SYSTEMD_UNIT_PATH.write_text(unit_body, encoding="utf-8")
    except OSError as exc:
        return {
            "installed": False,
            "unit_path": str(SYSTEMD_UNIT_PATH),
            "exec_start": " ".join(program_arguments),
            "reload": None,
            "enable": None,
            "error": str(exc),
        }
    reload_result = _run_systemctl(["daemon-reload"])
    enable_result = _run_systemctl(["enable", "--now", SYSTEMD_UNIT_NAME])
    return {
        "installed": True,
        "unit_path": str(SYSTEMD_UNIT_PATH),
        "exec_start": " ".join(program_arguments),
        "reload": reload_result,
        "enable": enable_result,
        "error": None,
    }


def uninstall_systemd() -> dict[str, Any]:
    """Disable + remove the systemd unit. Idempotent."""
    if sys.platform != "linux":
        return {
            "removed": False,
            "unit_path": str(SYSTEMD_UNIT_PATH),
            "disable": {"ok": True, "returncode": None, "error": None},
            "reload": {"ok": True, "returncode": None, "error": None},
            "error": None,
        }
    existed = SYSTEMD_UNIT_PATH.exists()
    disable_result = _run_systemctl(["disable", "--now", SYSTEMD_UNIT_NAME])
    try:
        SYSTEMD_UNIT_PATH.unlink(missing_ok=True)
    except OSError as exc:
        return {
            "removed": False,
            "unit_path": str(SYSTEMD_UNIT_PATH),
            "disable": disable_result,
            "reload": None,
            "error": str(exc),
        }
    reload_result = _run_systemctl(["daemon-reload"])
    return {
        "removed": existed,
        "unit_path": str(SYSTEMD_UNIT_PATH),
        "disable": disable_result,
        "reload": reload_result,
        "error": None,
    }


def run_daemon(
    host: str = "127.0.0.1",
    port: int = 0,
    data_dir: Path | None = None,
    *,
    server_runner: Callable[..., int] | None = None,
) -> dict[str, Any]:
    """Boot the daemon server, unless a healthy server already owns the lock.

    D2: the discovery file IS the single-server lock. A validated live
    server (pid alive + health probe) means we refuse and tell the
    founder where it runs; a stale/dead file is ignored by the
    validation and we start normally. Blocks until server shutdown when
    it does start.

    Known race window (accepted, documented per PRD D2 review): between
    this check and the new server's discovery publish (uvicorn binds →
    watcher thread writes server.json) two near-simultaneous starts can
    both pass the check. The window is sub-second, requires the founder
    to race two manual starts (launchd KeepAlive never double-spawns the
    same label), and the second writer simply overwrites server.json —
    readers follow the healthy winner. A flock-grade lock is deliberately
    out of scope.
    """
    explicit_data_dir = data_dir is not None
    resolved_data_dir = _resolve_data_dir(data_dir)
    # Handoff tombstone: this company was exported to another machine
    # (`kompany export --handoff`). Refuse to boot a second live engine.
    from kompany.state.export_bundle import read_exported_marker

    marker = read_exported_marker(resolved_data_dir)
    if marker is not None:
        return {
            "started": False,
            "port": None,
            "pid": None,
            "source": "exported",
            "message": (
                "This company was handed off to another machine "
                f"(exported_at={marker.get('exported_at')}). The daemon "
                "will not tick a tombstoned data_dir — run the company "
                "where the bundle was imported, or `kompany import` a "
                "bundle here to make this machine live again."
            ),
        }
    # A cached "no sidecar" verdict from earlier in this process must
    # not let two daemons race past the lock check.
    mcp_proxy.reset_discovery_cache()
    existing = mcp_proxy.discover_sidecar(resolved_data_dir)
    if existing is not None:
        source = existing.get("source", "sidecar")
        return {
            "started": False,
            "port": existing["port"],
            "pid": existing["pid"],
            "source": source,
            "message": (
                f"A Kompany server is already running (source={source}, "
                f"pid={existing['pid']}, port={existing['port']}). "
                "One server process owns the tick loop — attach to it "
                "instead of starting a second engine."
            ),
        }
    if server_runner is None:
        from kompany.interfaces.server_boot import run_server as server_runner
    server_runner(
        host=host,
        port=port,
        data_dir_override=str(resolved_data_dir) if explicit_data_dir else None,
        source="daemon",
    )
    return {"started": True, "port": port, "pid": os.getpid(), "source": "daemon"}


# ---------------------------------------------------------------------------
# Platform-routing shims (used by the CLI; pick launchd or systemd)
# ---------------------------------------------------------------------------


def install_daemon(data_dir: Path | None = None) -> dict[str, Any]:
    """Install the daemon supervisor for the current platform.

    macOS → launchd LaunchAgent; Linux → systemd system service. Other
    platforms get a clear error pointing to ``kompany daemon run`` under
    a manual process supervisor.
    """
    if sys.platform == "darwin":
        return install_launchd(data_dir)
    if sys.platform == "linux":
        return install_systemd(data_dir)
    return {
        "installed": False,
        "error": (
            f"kompany daemon install has no built-in supervisor for "
            f"{sys.platform}. Run 'kompany daemon run' under your own "
            "process supervisor (e.g. supervisord, runit)."
        ),
    }


def uninstall_daemon() -> dict[str, Any]:
    """Uninstall the daemon supervisor for the current platform."""
    if sys.platform == "darwin":
        return uninstall_launchd()
    if sys.platform == "linux":
        return uninstall_systemd()
    return {
        "removed": False,
        "error": f"No built-in supervisor to uninstall on {sys.platform}.",
    }


__all__ = [
    "BUNDLED_SERVER_BINARY",
    "LAUNCHD_LABEL",
    "SYSTEMD_UNIT_NAME",
    "SYSTEMD_UNIT_PATH",
    "daemon_status",
    "install_daemon",
    "install_launchd",
    "install_systemd",
    "resolve_program_arguments",
    "run_daemon",
    "uninstall_daemon",
    "uninstall_launchd",
    "uninstall_systemd",
]
