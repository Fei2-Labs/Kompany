"""Daemon process management ops (06-12-daemon-tick-loop PR2).

No real launchctl (subprocess.run is monkeypatched and command shapes
asserted), no real uvicorn (run_daemon takes an injectable
server_runner), HOME redirected to tmp for plist tests. The live-server
detection test reuses the sleeper-pid + loopback-stub pattern from
``test_mcp_sidecar_proxy.py``.
"""

from __future__ import annotations

import http.server
import json
import os
import plistlib
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from kompany.core import daemon_ops
from kompany.interfaces import mcp_proxy


@pytest.fixture(autouse=True)
def _fresh_discovery_cache():
    mcp_proxy.reset_discovery_cache()
    yield
    mcp_proxy.reset_discovery_cache()


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture()
def launchctl_recorder(monkeypatch):
    """Replace subprocess.run with a recorder; returncode configurable."""
    calls: list[list[str]] = []
    state = {"returncode": 0, "stderr": "", "raise_oserror": False}

    def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
        calls.append(list(cmd))
        if state["raise_oserror"]:
            raise FileNotFoundError("launchctl not found")
        return SimpleNamespace(
            returncode=state["returncode"], stdout="", stderr=state["stderr"]
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls, state


@pytest.fixture()
def darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture()
def sleeper_pid():
    """A live pid that is not this process (defeats the self-proxy guard)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc.pid
    proc.kill()
    proc.wait()


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Minimal running-server stand-in: /health + /status."""

    ticker_payload: dict = {
        "running": True,
        "last_tick_at": "2026-06-12T00:00:00+00:00",
        "tick_count": 7,
        "interval_seconds": 300,
    }

    def do_GET(self):
        if self.path == "/health":
            body = b'{"status": "ok"}'
        elif self.path == "/status":
            body = json.dumps({
                "company": "TestCo",
                "ticker": self.ticker_payload,
            }).encode("utf-8")
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture()
def stub_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _write_server_json(tmp_path, port, pid, source=None):
    payload = {"port": port, "pid": pid, "started_at": "2026-06-12T00:00:00+00:00"}
    if source is not None:
        payload["source"] = source
    (tmp_path / mcp_proxy.DISCOVERY_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# daemon_status
# ---------------------------------------------------------------------------


def test_status_no_server(tmp_path, fake_home, darwin, launchctl_recorder):
    calls, state = launchctl_recorder
    state["returncode"] = 113  # launchctl print: service not loaded

    result = daemon_ops.daemon_status(tmp_path)

    assert result["server"] == {
        "running": False, "port": None, "pid": None, "source": "none",
    }
    assert result["ticker"] is None
    assert result["supervisor"]["installed"] is False
    assert result["supervisor"]["loaded"] is False
    assert result["supervisor"]["plist_path"].endswith(
        "Library/LaunchAgents/com.kompany.daemon.plist"
    )


def test_status_detects_daemon_server_and_ticker(
    tmp_path, fake_home, darwin, sleeper_pid, stub_server, monkeypatch
):
    # launchctl untouched on this path except the loaded probe.
    monkeypatch.setattr(daemon_ops, "_launchd_loaded", lambda: False)
    _write_server_json(tmp_path, stub_server, sleeper_pid, source="daemon")

    result = daemon_ops.daemon_status(tmp_path)

    assert result["server"] == {
        "running": True,
        "port": stub_server,
        "pid": sleeper_pid,
        "source": "daemon",
    }
    # Ticker block passed through from the live server's GET /status.
    assert result["ticker"] == _StubHandler.ticker_payload


def test_status_legacy_discovery_file_defaults_to_sidecar_source(
    tmp_path, fake_home, darwin, sleeper_pid, stub_server, monkeypatch
):
    monkeypatch.setattr(daemon_ops, "_launchd_loaded", lambda: False)
    _write_server_json(tmp_path, stub_server, sleeper_pid)  # no source key

    result = daemon_ops.daemon_status(tmp_path)

    assert result["server"]["source"] == "sidecar"


def test_status_stale_discovery_file_reports_not_running(
    tmp_path, fake_home, darwin, launchctl_recorder
):
    _, state = launchctl_recorder
    state["returncode"] = 113
    # Dead pid: nothing validates, the stale lock is ignored.
    _write_server_json(tmp_path, 1, 99999999, source="daemon")

    result = daemon_ops.daemon_status(tmp_path)

    assert result["server"]["running"] is False
    assert result["server"]["source"] == "none"


def test_status_loaded_via_launchctl_print(
    tmp_path, fake_home, darwin, launchctl_recorder
):
    calls, state = launchctl_recorder
    state["returncode"] = 0
    plist_dir = fake_home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    (plist_dir / "com.kompany.daemon.plist").write_bytes(b"")

    result = daemon_ops.daemon_status(tmp_path)

    assert result["supervisor"]["installed"] is True
    assert result["supervisor"]["loaded"] is True
    assert calls == [[
        "launchctl", "print", f"gui/{os.getuid()}/com.kompany.daemon",
    ]]


# ---------------------------------------------------------------------------
# install_launchd
# ---------------------------------------------------------------------------


def test_install_refuses_off_macos(tmp_path, fake_home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    result = daemon_ops.install_launchd(tmp_path)

    assert result["installed"] is False
    assert "macOS-only" in result["error"]
    assert not (fake_home / "Library" / "LaunchAgents").exists()


def test_install_writes_plist_with_interpreter_fallback(
    tmp_path, fake_home, darwin, launchctl_recorder, monkeypatch
):
    calls, _ = launchctl_recorder
    monkeypatch.setattr(
        daemon_ops, "BUNDLED_SERVER_BINARY", tmp_path / "no-such-binary"
    )
    data_dir = tmp_path / "data"

    result = daemon_ops.install_launchd(data_dir)

    assert result["installed"] is True
    plist_path = Path(result["plist_path"])
    assert plist_path == fake_home / "Library" / "LaunchAgents" / "com.kompany.daemon.plist"
    with plist_path.open("rb") as fh:
        plist = plistlib.load(fh)
    assert plist["Label"] == "com.kompany.daemon"
    assert plist["ProgramArguments"] == [
        sys.executable, "-m", "kompany.interfaces.daemon_main",
    ]
    assert plist["KeepAlive"] is True
    assert plist["RunAtLoad"] is True
    assert plist["StandardOutPath"] == str(data_dir / "logs" / "daemon.out.log")
    assert plist["StandardErrPath"] == str(data_dir / "logs" / "daemon.err.log")
    env = plist["EnvironmentVariables"]
    assert env["KOMPANY_DATA_DIR"] == str(data_dir)
    # PATH must be set so CLI-harness modes find their binaries under launchd.
    assert "PATH" in env
    assert str(Path.home() / ".local" / "bin") in env["PATH"].split(":")
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
    assert (data_dir / "logs").is_dir()
    # Best-effort bootstrap, list-form command.
    assert result["bootstrap"]["ok"] is True
    assert calls == [[
        "launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path),
    ]]


def test_install_prefers_bundled_app_binary(
    tmp_path, fake_home, darwin, launchctl_recorder, monkeypatch
):
    binary = tmp_path / "Kompany.app" / "binaries" / "kompany-server"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"#!fake")
    monkeypatch.setattr(daemon_ops, "BUNDLED_SERVER_BINARY", binary)

    result = daemon_ops.install_launchd(tmp_path / "data")

    assert result["program_arguments"] == [
        str(binary), "--host", "127.0.0.1", "--port", "0",
    ]
    with Path(result["plist_path"]).open("rb") as fh:
        assert plistlib.load(fh)["ProgramArguments"] == result["program_arguments"]


def test_install_records_bootstrap_failure_without_failing(
    tmp_path, fake_home, darwin, launchctl_recorder, monkeypatch
):
    monkeypatch.setattr(
        daemon_ops, "BUNDLED_SERVER_BINARY", tmp_path / "no-such-binary"
    )
    _, state = launchctl_recorder
    state["returncode"] = 5
    state["stderr"] = "Bootstrap failed: 5: Input/output error"

    result = daemon_ops.install_launchd(tmp_path / "data")

    assert result["installed"] is True
    assert Path(result["plist_path"]).exists()
    assert result["bootstrap"]["ok"] is False
    assert "Bootstrap failed" in result["bootstrap"]["error"]


def test_install_survives_missing_launchctl(
    tmp_path, fake_home, darwin, launchctl_recorder, monkeypatch
):
    monkeypatch.setattr(
        daemon_ops, "BUNDLED_SERVER_BINARY", tmp_path / "no-such-binary"
    )
    _, state = launchctl_recorder
    state["raise_oserror"] = True

    result = daemon_ops.install_launchd(tmp_path / "data")

    assert result["installed"] is True
    assert result["bootstrap"]["ok"] is False
    assert "launchctl not found" in result["bootstrap"]["error"]


# ---------------------------------------------------------------------------
# uninstall_launchd
# ---------------------------------------------------------------------------


def test_uninstall_idempotent_when_not_installed(
    fake_home, darwin, launchctl_recorder
):
    calls, state = launchctl_recorder
    state["returncode"] = 113
    state["stderr"] = "Boot-out failed: 113: Could not find specified service"

    result = daemon_ops.uninstall_launchd()

    assert result["removed"] is False
    assert result["error"] is None
    assert calls == [[
        "launchctl", "bootout", f"gui/{os.getuid()}/com.kompany.daemon",
    ]]


def test_uninstall_removes_plist(fake_home, darwin, launchctl_recorder):
    plist_dir = fake_home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.kompany.daemon.plist"
    plist_path.write_bytes(b"")

    result = daemon_ops.uninstall_launchd()

    assert result["removed"] is True
    assert not plist_path.exists()
    # Second run: nothing left, still clean.
    again = daemon_ops.uninstall_launchd()
    assert again["removed"] is False
    assert again["error"] is None


# ---------------------------------------------------------------------------
# run_daemon — discovery file is the lock (D2)
# ---------------------------------------------------------------------------


def test_run_daemon_refuses_when_healthy_server_running(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mcp_proxy,
        "discover_sidecar",
        lambda data_dir=None: {"port": 4242, "pid": 999, "source": "sidecar"},
    )

    def runner(**kwargs):
        raise AssertionError("server_runner must not be called")

    result = daemon_ops.run_daemon(data_dir=tmp_path, server_runner=runner)

    assert result["started"] is False
    assert result["port"] == 4242
    assert result["pid"] == 999
    assert "4242" in result["message"]
    assert "999" in result["message"]


def test_run_daemon_starts_when_lock_is_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    calls = []

    result = daemon_ops.run_daemon(
        host="127.0.0.1",
        port=8123,
        data_dir=tmp_path,
        server_runner=lambda **kwargs: calls.append(kwargs) or 0,
    )

    assert result["started"] is True
    assert calls == [{
        "host": "127.0.0.1",
        "port": 8123,
        "data_dir_override": str(tmp_path),
        "source": "daemon",
    }]


def test_run_daemon_without_explicit_data_dir_keeps_env_resolution(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_proxy, "default_data_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    calls = []

    daemon_ops.run_daemon(server_runner=lambda **kwargs: calls.append(kwargs) or 0)

    # No explicit override → the server resolves data_dir via settings/env.
    assert calls[0]["data_dir_override"] is None


def test_run_daemon_ignores_stale_cached_verdict(tmp_path, monkeypatch):
    """run_daemon resets the discovery cache before taking the lock."""
    resets = []
    monkeypatch.setattr(
        mcp_proxy, "reset_discovery_cache", lambda: resets.append(True)
    )
    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)

    daemon_ops.run_daemon(data_dir=tmp_path, server_runner=lambda **kwargs: 0)

    assert resets == [True]


def test_resolve_daemon_path_includes_user_and_homebrew_bins(monkeypatch):
    """launchd's minimal PATH omits ~/.local/bin + Homebrew; resolve_daemon_path
    must prepend them (deduped, order-preserving) so claude/codex/opencode
    resolve under the daemon."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/opt/homebrew/bin")
    path = daemon_ops.resolve_daemon_path()
    parts = path.split(":")
    assert parts[0] == str(Path.home() / ".local" / "bin")
    assert "/opt/homebrew/bin" in parts
    assert "/usr/local/bin" in parts
    assert "/usr/bin" in parts  # base PATH preserved
    assert len(parts) == len(set(parts))  # no duplicates


# ---------------------------------------------------------------------------
# systemd install / uninstall (Linux counterpart of launchd)
# ---------------------------------------------------------------------------


@pytest.fixture()
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


@pytest.fixture()
def systemctl_recorder(monkeypatch):
    """Replace subprocess.run with a recorder; returncode configurable."""
    calls: list[list[str]] = []
    state = {"returncode": 0, "stderr": "", "raise_oserror": False}

    def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
        calls.append(list(cmd))
        if state["raise_oserror"]:
            raise FileNotFoundError("systemctl not found")
        return SimpleNamespace(
            returncode=state["returncode"], stdout="", stderr=state["stderr"]
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls, state


def test_systemd_unit_content_shape():
    body = daemon_ops._systemd_unit_content(
        Path("/home/user/.kompany"),
        ["/usr/bin/python3", "-m", "kompany.interfaces.daemon_main"],
        "/home/user/.local/bin:/usr/bin:/bin",
        user="kosonen",
    )
    assert "[Unit]" in body
    assert "[Service]" in body
    assert "[Install]" in body
    assert "Restart=always" in body
    assert "RestartSec=5" in body
    assert "WantedBy=multi-user.target" in body
    assert "User=kosonen" in body
    assert "ExecStart=/usr/bin/python3 -m kompany.interfaces.daemon_main" in body
    assert "KOMPANY_DATA_DIR=/home/user/.kompany" in body
    assert "PATH=/home/user/.local/bin:/usr/bin:/bin" in body


def test_systemd_unit_is_hardened_by_default():
    body = daemon_ops._systemd_unit_content(
        Path("/home/kompany/.kompany"),
        ["/opt/kompany/current/venv/bin/python", "-m", "kompany.interfaces.daemon_main"],
        "/usr/bin:/bin",
        user="kompany",
        home=Path("/home/kompany"),
    )
    for line in (
        "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=strict",
        "ReadWritePaths=/home/kompany/.kompany /home/kompany",
        "CapabilityBoundingSet=", "AmbientCapabilities=", "UMask=0077",
        "ProtectKernelTunables=yes", "RestrictSUIDSGID=yes", "SystemCallArchitectures=native",
    ):
        assert line in body, line
    # release dir (/opt) is NOT writable: only data dir + home are listed
    assert "/opt" not in body.split("ReadWritePaths=")[1].splitlines()[0]
    # without a home only the data dir is writable
    solo = daemon_ops._systemd_unit_content(Path("/srv/k"), ["x"], "/bin", user=None, home=None)
    assert "ReadWritePaths=/srv/k\n" in solo
    assert daemon_ops._home_for(None) is None


def test_systemd_unit_content_no_user():
    body = daemon_ops._systemd_unit_content(
        Path("/root/.kompany"),
        ["/usr/bin/python3", "-m", "kompany.interfaces.daemon_main"],
        "/usr/bin:/bin",
        user=None,
    )
    assert "User=" not in body


def test_install_systemd_refuses_off_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    result = daemon_ops.install_systemd(tmp_path)
    assert result["installed"] is False
    assert "Linux-only" in result["error"]


def test_install_systemd_writes_unit_and_enables(
    tmp_path, linux, systemctl_recorder, monkeypatch
):
    calls, state = systemctl_recorder
    state["returncode"] = 0
    monkeypatch.setattr(
        daemon_ops, "SYSTEMD_UNIT_PATH", tmp_path / "kompany-daemon.service"
    )
    monkeypatch.setattr(
        daemon_ops, "BUNDLED_SERVER_BINARY", tmp_path / "no-such-binary"
    )
    data_dir = tmp_path / "data"

    result = daemon_ops.install_systemd(data_dir)

    assert result["installed"] is True
    unit_path = tmp_path / "kompany-daemon.service"
    assert result["unit_path"] == str(unit_path)
    body = unit_path.read_text()
    assert "ExecStart=" + sys.executable in body
    assert "KOMPANY_DATA_DIR=" + str(data_dir) in body
    assert result["reload"]["ok"] is True
    assert result["enable"]["ok"] is True
    # daemon-reload then enable --now
    assert calls[0] == ["systemctl", "daemon-reload"]
    assert calls[1] == ["systemctl", "enable", "--now", "kompany-daemon.service"]
    assert (data_dir / "logs").is_dir()


def test_install_systemd_records_enable_failure(
    tmp_path, linux, systemctl_recorder, monkeypatch
):
    calls, state = systemctl_recorder
    # daemon-reload succeeds, enable --now fails
    responses = [
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="Failed to start: unit not found"),
    ]

    def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
        calls.append(list(cmd))
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        daemon_ops, "SYSTEMD_UNIT_PATH", tmp_path / "kompany-daemon.service"
    )
    monkeypatch.setattr(
        daemon_ops, "BUNDLED_SERVER_BINARY", tmp_path / "no-such-binary"
    )

    result = daemon_ops.install_systemd(tmp_path / "data")

    assert result["installed"] is True
    assert result["reload"]["ok"] is True
    assert result["enable"]["ok"] is False
    assert "Failed to start" in result["enable"]["error"]


def test_uninstall_systemd_idempotent(linux, systemctl_recorder, monkeypatch):
    calls, state = systemctl_recorder
    state["returncode"] = 0
    unit_path = tmp_path / "kompany-daemon.service" if False else None
    # Use a temp path that doesn't exist
    monkeypatch.setattr(
        daemon_ops, "SYSTEMD_UNIT_PATH", Path("/tmp/test-kompany-nonexistent.service")
    )

    result = daemon_ops.uninstall_systemd()

    assert result["removed"] is False
    assert result["error"] is None
    assert result["disable"]["ok"] is True
    assert result["reload"]["ok"] is True
    assert calls[0] == ["systemctl", "disable", "--now", "kompany-daemon.service"]
    assert calls[1] == ["systemctl", "daemon-reload"]


def test_uninstall_systemd_removes_existing_unit(
    tmp_path, linux, systemctl_recorder, monkeypatch
):
    calls, state = systemctl_recorder
    state["returncode"] = 0
    unit_path = tmp_path / "kompany-daemon.service"
    unit_path.write_text("[Service]\nExecStart=foo\n")
    monkeypatch.setattr(daemon_ops, "SYSTEMD_UNIT_PATH", unit_path)

    result = daemon_ops.uninstall_systemd()

    assert result["removed"] is True
    assert not unit_path.exists()


def test_install_daemon_routes_to_systemd_on_linux(
    tmp_path, linux, systemctl_recorder, monkeypatch
):
    monkeypatch.setattr(
        daemon_ops, "SYSTEMD_UNIT_PATH", tmp_path / "kompany-daemon.service"
    )
    monkeypatch.setattr(
        daemon_ops, "BUNDLED_SERVER_BINARY", tmp_path / "no-such-binary"
    )

    result = daemon_ops.install_daemon(tmp_path / "data")

    assert result["installed"] is True
    assert "unit_path" in result


def test_install_daemon_routes_to_launchd_on_macos(
    tmp_path, fake_home, darwin, launchctl_recorder, monkeypatch
):
    monkeypatch.setattr(
        daemon_ops, "BUNDLED_SERVER_BINARY", tmp_path / "no-such-binary"
    )

    result = daemon_ops.install_daemon(tmp_path / "data")

    assert result["installed"] is True
    assert "plist_path" in result


def test_daemon_status_reports_systemd_on_linux(
    tmp_path, linux, systemctl_recorder, monkeypatch
):
    calls, state = systemctl_recorder
    state["returncode"] = 1  # is-active / is-enabled → false (not installed)
    monkeypatch.setattr(
        daemon_ops, "SYSTEMD_UNIT_PATH", tmp_path / "kompany-daemon.service"
    )
    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)

    result = daemon_ops.daemon_status(tmp_path)

    assert result["supervisor"]["type"] == "systemd"
    assert result["supervisor"]["installed"] is False
    assert result["supervisor"]["loaded"] is False
    assert result["supervisor"]["enabled"] is False
