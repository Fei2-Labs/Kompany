"""API access guard: origin/CSRF refusal, token gate, exemptions, bind refusal."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kompany.interfaces import api
from kompany.interfaces.api_guard import assert_bind_allowed, is_loopback_host, origin_allowed


class _FakeEngine:
    def __init__(self, token: str = ""):
        self.settings = SimpleNamespace(web_dashboard_token=token, dashboard_session_ttl_seconds=3600)

    def health_check(self):
        return {"status": "ok"}

    def get_runtime_state(self):
        return {"status": "running"}

    def observability_snapshot(self):
        return {"ok": True}

    def workflows_list(self):
        return []

    def process_directive(self, text, **kw):
        return {"ok": True}

    def handle_remote_command(self, req):
        return {"status": "denied", "message": "engine-level auth"}


@pytest.fixture()
def client(monkeypatch):
    def _make(token=""):
        monkeypatch.setattr(api, "_engine", _FakeEngine(token))
        return TestClient(api.app)

    return _make


def test_cross_site_origin_is_refused_even_without_token(client):
    c = client()
    r = c.get("/observability", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post("/workflows/x/run", json={"inputs": {}}, headers={"Origin": "http://attacker.local"})
    assert r.status_code == 403
    r = c.get("/observability", headers={"Origin": "null"})
    assert r.status_code == 403


def test_same_origin_and_loopback_origins_pass(client):
    c = client()
    assert c.get("/observability", headers={"Origin": "http://testserver"}).status_code == 200
    c2 = TestClient(api.app, base_url="http://127.0.0.1:8000")
    assert c2.get("/observability", headers={"Origin": "http://localhost:5173"}).status_code == 200
    assert c2.get("/observability", headers={"Origin": "tauri://localhost"}).status_code == 200


def test_cors_configured_origin_passes(client, monkeypatch):
    monkeypatch.setenv("KOMPANY_CORS_ORIGINS", "https://world.example")
    c = client()
    assert c.get("/observability", headers={"Origin": "https://world.example"}).status_code == 200
    assert c.get("/observability", headers={"Origin": "https://other.example"}).status_code == 403


def test_no_token_configured_keeps_local_api_open(client):
    c = client()
    assert c.get("/observability").status_code == 200


def test_token_gate_blocks_api_and_redirects_browsers(client):
    c = client("secret-1")
    r = c.get("/observability")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    r = c.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard/login"
    assert c.get("/observability", headers={"Authorization": "Bearer secret-1"}).status_code == 200
    assert c.get("/observability?token=secret-1").status_code == 200
    assert c.get("/observability", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_login_cookie_unlocks_every_route(client):
    c = client("secret-2")
    r = c.post("/dashboard/login", data={"dashboard_token": "secret-2"}, follow_redirects=False)
    assert r.status_code == 303 and "kompany_dashboard_session" in r.cookies
    assert c.get("/observability").status_code == 200  # cookie jar carries the session


def test_exempt_routes_stay_reachable_with_token_set(client):
    c = client("secret-3")
    assert c.get("/health").status_code in (200, 500)  # reachable (fake engine may lack fields)
    assert c.get("/dashboard/login").status_code == 200
    r = c.post("/remote/command", json={"source": "mobile", "text": "status", "bearer_token": "x"})
    assert r.status_code != 401  # engine-level auth decides, not the guard


def test_host_allowlist_when_configured(client, monkeypatch):
    monkeypatch.setenv("KOMPANY_ALLOWED_HOSTS", "kompany.example.com")
    c = client()
    assert c.get("/observability", headers={"Host": "attacker.example"}).status_code == 421
    assert c.get("/observability", headers={"Host": "kompany.example.com"}).status_code == 200
    assert c.get("/observability", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_origin_allowed_helper_and_loopback():
    assert origin_allowed("http://127.0.0.1:8000", "127.0.0.1:8000")
    assert origin_allowed("http://localhost:3000", "127.0.0.1:8000")
    assert not origin_allowed("http://localhost:3000", "kompany.example.com")
    assert not origin_allowed("file://", "127.0.0.1:8000")
    assert is_loopback_host("[::1]:8000") and is_loopback_host("localhost") and not is_loopback_host("10.0.0.5")


def test_public_bind_refused_without_token(monkeypatch):
    monkeypatch.delenv("KOMPANY_ALLOW_OPEN_BIND", raising=False)
    monkeypatch.delenv("WEB_DASHBOARD_TOKEN", raising=False)
    assert_bind_allowed("127.0.0.1", SimpleNamespace(web_dashboard_token=""))
    with pytest.raises(SystemExit):
        assert_bind_allowed("0.0.0.0", SimpleNamespace(web_dashboard_token=""))
    assert_bind_allowed("0.0.0.0", SimpleNamespace(web_dashboard_token="t"))
    monkeypatch.setenv("KOMPANY_ALLOW_OPEN_BIND", "1")
    assert_bind_allowed("0.0.0.0", SimpleNamespace(web_dashboard_token=""))


def test_mcp_proxy_sends_bearer_when_token_configured(monkeypatch):
    from kompany.interfaces import mcp_proxy

    monkeypatch.setenv("WEB_DASHBOARD_TOKEN", "proxy-secret")
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "result": {"fine": 1}}'

    def fake_urlopen(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        return _Resp()

    monkeypatch.setattr(mcp_proxy.urllib.request, "urlopen", fake_urlopen)
    out = mcp_proxy.proxy_tool_call(1234, "kompany_status", {})
    assert out == {"fine": 1} and seen["auth"] == "Bearer proxy-secret"
