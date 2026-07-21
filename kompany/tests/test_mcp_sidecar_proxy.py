"""MCP sidecar proxy — discovery, bridge endpoint, proxy client, call_tool routing.

Covers task 06-10-mcp-sidecar-proxy:

- discovery-file validation (missing / malformed / stale pid / recycled
  port serving a foreign app / self-proxy guard / alive)
- ``POST /mcp/tool`` bridge contract (200 ok, 404 unknown tool, 500 on
  engine failure) via TestClient
- ``proxy_tool_call`` round-trip over a real loopback socket
- ``call_tool`` mode selection: proxy when a sidecar is alive (no
  in-process engine constructed), error surfacing without fallback,
  in-process fallback when headless
- parity smoke: same tool + args through dispatch_tool and the bridge
  serialize to identical text
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from kompany.interfaces import mcp_proxy
from kompany.interfaces.api import app
from kompany.interfaces.mcp_dispatch import UnknownToolError, dispatch_tool
from kompany.interfaces.mcp_server import call_tool


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_discovery_cache():
    mcp_proxy.reset_discovery_cache()
    yield
    mcp_proxy.reset_discovery_cache()


@pytest.fixture()
def sleeper_pid():
    """A live pid that is not this process (defeats the self-proxy guard)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc.pid
    proc.kill()
    proc.wait()


def _write_discovery(tmp_path, port, pid):
    (tmp_path / mcp_proxy.DISCOVERY_FILENAME).write_text(
        json.dumps({
            "port": port,
            "pid": pid,
            "started_at": "2026-06-10T00:00:00+00:00",
        }),
        encoding="utf-8",
    )


def _closed_port() -> int:
    """An ephemeral port with nothing listening on it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _FakeLedger:
    def get_recent(self, limit=10):
        return [
            {"description": "seed", "amount": 100.0, "at": datetime(2026, 6, 10, 12, 0)},
            {"description": "ai_cost", "amount": -0.42, "at": datetime(2026, 6, 10, 13, 0)},
        ][:limit]


class _FakeEngine:
    ledger = _FakeLedger()

    def run_retrospective(self, project_id):
        raise RuntimeError(f"engine exploded on {project_id}")


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Minimal sidecar stand-in: /health + /mcp/tool over a real socket."""

    health_payload: bytes = b'{"status": "ok"}'

    def do_GET(self):
        if self.path == "/health":
            self._send(200, self.health_payload)
        else:
            self._send(404, b"{}")

    def do_POST(self):
        if self.path != "/mcp/tool":
            self._send(404, b"{}")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        try:
            result = dispatch_tool(_FakeEngine(), body["name"], body["arguments"])
        except UnknownToolError:
            out, code = {"ok": False, "error": f"Unknown tool: {body['name']}"}, 404
        else:
            out = {"ok": True, "result": json.loads(json.dumps(result, default=str))}
            code = 200
        self._send(code, json.dumps(out).encode("utf-8"))

    def _send(self, code, payload):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture()
def stub_sidecar():
    """Spin loopback HTTP servers; yields a factory(health_payload) -> port."""
    servers = []

    def _make(health_payload: bytes = b'{"status": "ok"}') -> int:
        handler = type("Handler", (_StubHandler,), {"health_payload": health_payload})
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]

    yield _make
    for srv in servers:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------
# Discovery-file write / remove (sidecar side)
# ---------------------------------------------------------------------------


def test_write_and_remove_discovery_file(tmp_path):
    path = mcp_proxy.write_discovery_file(8123, tmp_path)
    assert path == tmp_path / "server.json"
    data = json.loads(path.read_text())
    assert data["port"] == 8123
    assert data["pid"] == os.getpid()
    assert "started_at" in data
    mcp_proxy.remove_discovery_file(tmp_path)
    assert not path.exists()
    # Idempotent: removing again must not raise.
    mcp_proxy.remove_discovery_file(tmp_path)


# ---------------------------------------------------------------------------
# Discovery validation (reader side)
# ---------------------------------------------------------------------------


def test_discover_missing_file_returns_none(tmp_path):
    assert mcp_proxy.discover_sidecar(tmp_path) is None


def test_discover_malformed_json_returns_none(tmp_path):
    (tmp_path / "server.json").write_text("{not json", encoding="utf-8")
    assert mcp_proxy.discover_sidecar(tmp_path) is None


def test_discover_bad_shape_returns_none(tmp_path):
    (tmp_path / "server.json").write_text(
        json.dumps({"port": "8000", "pid": os.getpid()}), encoding="utf-8"
    )
    assert mcp_proxy.discover_sidecar(tmp_path) is None


def test_discover_self_pid_guard(tmp_path, stub_sidecar):
    """A discovery file naming our own pid means we ARE the sidecar."""
    port = stub_sidecar()
    _write_discovery(tmp_path, port, os.getpid())
    assert mcp_proxy.discover_sidecar(tmp_path) is None


def test_discover_dead_pid_returns_none(tmp_path, stub_sidecar):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    port = stub_sidecar()
    _write_discovery(tmp_path, port, proc.pid)
    assert mcp_proxy.discover_sidecar(tmp_path) is None


def test_discover_health_refused_returns_none(tmp_path, sleeper_pid):
    """App quit, stale file: probe hits a closed port → fallback verdict."""
    _write_discovery(tmp_path, _closed_port(), sleeper_pid)
    assert mcp_proxy.discover_sidecar(tmp_path) is None


def test_discover_foreign_health_shape_returns_none(tmp_path, sleeper_pid, stub_sidecar):
    """Recycled port serving some other app → shape check rejects it."""
    port = stub_sidecar(health_payload=b'{"hello": "world"}')
    _write_discovery(tmp_path, port, sleeper_pid)
    assert mcp_proxy.discover_sidecar(tmp_path) is None


def test_discover_alive_sidecar(tmp_path, sleeper_pid, stub_sidecar):
    port = stub_sidecar()
    _write_discovery(tmp_path, port, sleeper_pid)
    info = mcp_proxy.discover_sidecar(tmp_path)
    assert info is not None
    assert info["port"] == port
    assert info["pid"] == sleeper_pid


def test_discover_verdict_is_cached(tmp_path, sleeper_pid, stub_sidecar):
    port = stub_sidecar()
    _write_discovery(tmp_path, port, sleeper_pid)
    assert mcp_proxy.discover_sidecar(tmp_path) is not None
    # File vanishes, but the ~5s verdict cache still answers positively…
    (tmp_path / "server.json").unlink()
    assert mcp_proxy.discover_sidecar(tmp_path) is not None
    # …until the cache is dropped.
    mcp_proxy.reset_discovery_cache()
    assert mcp_proxy.discover_sidecar(tmp_path) is None


# ---------------------------------------------------------------------------
# Bridge endpoint (POST /mcp/tool) via TestClient
# ---------------------------------------------------------------------------


def test_bridge_known_tool(monkeypatch):
    monkeypatch.setattr("kompany.interfaces.api._engine", _FakeEngine())
    with TestClient(app) as client:
        resp = client.post(
            "/mcp/tool", json={"name": "kompany_ledger", "arguments": {"limit": 1}}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == [
        {"description": "seed", "amount": 100.0, "at": "2026-06-10 12:00:00"},
    ]


def test_bridge_unknown_tool_404(monkeypatch):
    monkeypatch.setattr("kompany.interfaces.api._engine", _FakeEngine())
    with TestClient(app) as client:
        resp = client.post("/mcp/tool", json={"name": "kompany_nope", "arguments": {}})
    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False
    assert "Unknown tool: kompany_nope" in body["error"]


def test_bridge_engine_failure_500(monkeypatch):
    monkeypatch.setattr("kompany.interfaces.api._engine", _FakeEngine())
    with TestClient(app) as client:
        resp = client.post(
            "/mcp/tool",
            json={"name": "kompany_retrospective", "arguments": {"project_id": "p1"}},
        )
    assert resp.status_code == 500
    body = resp.json()
    assert body["ok"] is False
    assert "engine exploded on p1" in body["error"]


def test_bridge_parity_with_dispatch_tool(monkeypatch):
    """Acceptance 6: same tool + args → byte-identical serialized payload."""
    monkeypatch.setattr("kompany.interfaces.api._engine", _FakeEngine())
    direct = dispatch_tool(_FakeEngine(), "kompany_ledger", {"limit": 2})
    with TestClient(app) as client:
        resp = client.post(
            "/mcp/tool", json={"name": "kompany_ledger", "arguments": {"limit": 2}}
        )
    proxied = resp.json()["result"]
    direct_text = json.dumps(direct, indent=2, default=str)
    proxied_text = json.dumps(proxied, indent=2, default=str)
    assert direct_text == proxied_text


# ---------------------------------------------------------------------------
# Proxy client round-trip over a real socket
# ---------------------------------------------------------------------------


def test_proxy_tool_call_round_trip(stub_sidecar):
    port = stub_sidecar()
    result = mcp_proxy.proxy_tool_call(port, "kompany_ledger", {"limit": 1})
    assert result == [
        {"description": "seed", "amount": 100.0, "at": "2026-06-10 12:00:00"},
    ]


def test_proxy_tool_call_unknown_tool_raises(stub_sidecar):
    port = stub_sidecar()
    with pytest.raises(mcp_proxy.SidecarProxyError) as exc_info:
        mcp_proxy.proxy_tool_call(port, "kompany_nope", {})
    assert "Unknown tool: kompany_nope" in str(exc_info.value)


def test_proxy_tool_call_connection_refused_raises():
    with pytest.raises(mcp_proxy.SidecarProxyError):
        mcp_proxy.proxy_tool_call(_closed_port(), "kompany_status", {})


# ---------------------------------------------------------------------------
# call_tool mode selection (stdio MCP server)
# ---------------------------------------------------------------------------


async def test_call_tool_proxies_when_sidecar_alive(monkeypatch):
    """Acceptance 1: proxy mode never constructs an in-process engine."""
    calls = []
    monkeypatch.setattr(
        mcp_proxy, "discover_sidecar", lambda data_dir=None: {"port": 4242, "pid": 1}
    )

    def fake_proxy(port, name, arguments, timeout=None, base_url=None):
        calls.append((port, name, arguments))
        return {"balance": 7.0}

    monkeypatch.setattr(mcp_proxy, "proxy_tool_call", fake_proxy)
    monkeypatch.setattr(
        "kompany.interfaces.mcp_server.get_engine",
        lambda: pytest.fail("get_engine must never run in proxy mode"),
    )
    out = await call_tool("kompany_status", {})
    assert calls == [(4242, "kompany_status", {})]
    assert out[0].text == json.dumps({"balance": 7.0}, indent=2, default=str)


async def test_call_tool_surfaces_proxy_error_without_fallback(monkeypatch):
    monkeypatch.setattr(
        mcp_proxy, "discover_sidecar", lambda data_dir=None: {"port": 4242, "pid": 1}
    )

    def boom(port, name, arguments, timeout=None, base_url=None):
        raise mcp_proxy.SidecarProxyError("connection died mid-call")

    monkeypatch.setattr(mcp_proxy, "proxy_tool_call", boom)
    monkeypatch.setattr(
        "kompany.interfaces.mcp_server.get_engine",
        lambda: pytest.fail("must not fall back to in-process on proxy error"),
    )
    out = await call_tool("kompany_execute", {"project_id": "p1"})
    assert out[0].text == "Sidecar proxy error: connection died mid-call"


async def test_call_tool_falls_back_in_process_when_headless(monkeypatch):
    """Acceptance 2: no sidecar → in-process engine, behavior as today."""
    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(
        "kompany.interfaces.mcp_server.get_engine", lambda: _FakeEngine()
    )
    out = await call_tool("kompany_ledger", {"limit": 1})
    expected = json.dumps(
        [{"description": "seed", "amount": 100.0, "at": datetime(2026, 6, 10, 12, 0)}],
        indent=2,
        default=str,
    )
    assert out[0].text == expected


async def test_call_tool_unknown_tool_in_process(monkeypatch):
    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(
        "kompany.interfaces.mcp_server.get_engine", lambda: _FakeEngine()
    )
    out = await call_tool("kompany_nope", {})
    assert out[0].text == "Unknown tool: kompany_nope"


# ---------------------------------------------------------------------------
# Full chain: real uvicorn socket + discovery file + proxy + bridge
# ---------------------------------------------------------------------------


async def test_full_chain_call_tool_through_real_sidecar(
    tmp_path, sleeper_pid, monkeypatch
):
    """call_tool → discovery → POST /mcp/tool → dispatch in the sidecar app.

    Also exercises the same bound-port accessor ``sidecar_main`` uses
    (``server.servers[0].sockets[0]``) for the ``--port 0`` case.
    """
    import time

    import uvicorn

    monkeypatch.setattr("kompany.interfaces.api._engine", _FakeEngine())
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]

        _write_discovery(tmp_path, port, sleeper_pid)
        monkeypatch.setattr(mcp_proxy, "default_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "kompany.interfaces.mcp_server.get_engine",
            lambda: pytest.fail("get_engine must never run in proxy mode"),
        )

        # Blocking urllib inside this test's loop is fine: the sidecar
        # serves from its own thread/loop, so there is no deadlock.
        out = await call_tool("kompany_ledger", {"limit": 1})
        expected = json.dumps(
            dispatch_tool(_FakeEngine(), "kompany_ledger", {"limit": 1}),
            indent=2,
            default=str,
        )
        assert out[0].text == expected
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Remote URL mode (07-14 cloud-deploy-backup-restore step 6)
# ---------------------------------------------------------------------------

class _FakeRemoteHandler(http.server.BaseHTTPRequestHandler):
    """Minimal handler that fakes a remote Kompany engine."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/mcp/tool":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "result": {"echo": body["name"], "args": body["arguments"]},
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def _start_fake_remote():
    """Start a fake remote engine on a random loopback port. Returns (port, server)."""
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _FakeRemoteHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return port, httpd


def test_discover_sidecar_remote_url(monkeypatch):
    """KOMPANY_REMOTE_URL makes discover_sidecar return a remote dict."""
    port, httpd = _start_fake_remote()
    try:
        monkeypatch.setenv(mcp_proxy.REMOTE_URL_ENV, f"http://127.0.0.1:{port}")
        result = mcp_proxy.discover_sidecar()
        assert result is not None
        assert result["source"] == "remote"
        assert result["url"] == f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


def test_discover_sidecar_remote_url_takes_precedence(monkeypatch, tmp_path, sleeper_pid):
    """Remote URL wins over a local sidecar discovery file."""
    port, httpd = _start_fake_remote()
    try:
        # Also write a local discovery file — remote should still win.
        _write_discovery(tmp_path, 99999, sleeper_pid)
        monkeypatch.setattr(mcp_proxy, "default_data_dir", lambda: tmp_path)
        monkeypatch.setenv(mcp_proxy.REMOTE_URL_ENV, f"http://127.0.0.1:{port}")
        result = mcp_proxy.discover_sidecar()
        assert result is not None
        assert result["source"] == "remote"
    finally:
        httpd.shutdown()


def test_discover_sidecar_remote_url_unreachable(monkeypatch):
    """An unreachable remote URL returns None (falls back to local)."""
    monkeypatch.setenv(mcp_proxy.REMOTE_URL_ENV, "http://127.0.0.1:1")
    assert mcp_proxy.discover_sidecar() is None


def test_discover_sidecar_remote_url_bad_response(monkeypatch):
    """A remote that doesn't return kompany health shape is rejected."""
    class _BadHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "not_ok"}).encode())
        def log_message(self, *a): pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _BadHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv(mcp_proxy.REMOTE_URL_ENV, f"http://127.0.0.1:{port}")
        assert mcp_proxy.discover_sidecar() is None
    finally:
        httpd.shutdown()


def test_proxy_tool_call_with_base_url():
    """proxy_tool_call forwards to base_url/mcp/tool when base_url is set."""
    port, httpd = _start_fake_remote()
    try:
        result = mcp_proxy.proxy_tool_call(
            port=0,
            name="kompany_status",
            arguments={"detail": True},
            base_url=f"http://127.0.0.1:{port}",
        )
        assert result == {"echo": "kompany_status", "args": {"detail": True}}
    finally:
        httpd.shutdown()


def test_discover_sidecar_remote_url_cached(monkeypatch):
    """Remote URL discovery is cached for ~5s."""
    port, httpd = _start_fake_remote()
    try:
        monkeypatch.setenv(mcp_proxy.REMOTE_URL_ENV, f"http://127.0.0.1:{port}")
        # First call probes
        r1 = mcp_proxy.discover_sidecar()
        assert r1 is not None
        # Shutdown the remote — cached verdict should still return
        httpd.shutdown()
        r2 = mcp_proxy.discover_sidecar()
        assert r2 is not None  # cached
    finally:
        pass


def test_discover_sidecar_no_remote_url(monkeypatch, tmp_path):
    """Without KOMPANY_REMOTE_URL, local discovery is used (no remote dict)."""
    monkeypatch.delenv(mcp_proxy.REMOTE_URL_ENV, raising=False)
    monkeypatch.setattr(mcp_proxy, "default_data_dir", lambda: tmp_path)
    # No discovery file, no remote URL → None
    assert mcp_proxy.discover_sidecar() is None
