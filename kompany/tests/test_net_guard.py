"""SSRF guard for LLM-controlled URLs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kompany.core.agent_tools.net_guard import BlockedURL, check_url, fetch_with_guard


def _resolver(mapping):
    return lambda host: mapping.get(host, [])


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/credentials", "http://localhost/", "http://[::1]/", "http://0.0.0.0/",
    "http://10.1.2.3/", "http://192.168.1.1/", "http://172.16.0.1/", "http://169.254.169.254/latest/meta-data",
    "http://100.64.0.1/", "http://metadata.google.internal/", "http://box.local/", "http://intranet.internal/",
    "ftp://example.com/", "file:///etc/passwd", "http://user:pw@example.com/", "http://[::ffff:127.0.0.1]/",
])
def test_blocked_targets(url):
    with pytest.raises(BlockedURL):
        check_url(url, resolver=_resolver({}))


def test_public_literal_and_resolved_host_pass():
    assert check_url("https://1.1.1.1/", resolver=_resolver({})) == "https://1.1.1.1/"
    assert check_url("https://example.com/x", resolver=_resolver({"example.com": ["93.184.216.34"]}))


def test_dns_to_private_is_blocked():
    with pytest.raises(BlockedURL):
        check_url("https://rebind.example", resolver=_resolver({"rebind.example": ["93.184.216.34", "127.0.0.1"]}))
    with pytest.raises(BlockedURL):
        check_url("https://nx.example", resolver=_resolver({}))


def test_fetch_with_guard_rechecks_every_redirect(monkeypatch):
    monkeypatch.setattr("kompany.core.agent_tools.net_guard._resolve", lambda h: ["93.184.216.34"])
    hops = iter([
        SimpleNamespace(status_code=302, headers={"location": "http://127.0.0.1:8000/credentials"}),
    ])

    def fake_get(url, follow_redirects, **kw):
        assert follow_redirects is False
        return next(hops)

    with pytest.raises(BlockedURL):
        fetch_with_guard("https://example.com/start", client_get=fake_get)


def test_fetch_with_guard_follows_public_redirects_and_caps(monkeypatch):
    monkeypatch.setattr("kompany.core.agent_tools.net_guard._resolve", lambda h: ["93.184.216.34"])
    seen = []

    def fake_get(url, follow_redirects, **kw):
        seen.append(url)
        if len(seen) < 3:
            return SimpleNamespace(status_code=301, headers={"location": f"/step{len(seen)}"})
        return SimpleNamespace(status_code=200, headers={}, text="ok")

    resp = fetch_with_guard("https://example.com/a", client_get=fake_get)
    assert resp.status_code == 200 and seen == ["https://example.com/a", "https://example.com/step1", "https://example.com/step2"]

    def loop(url, follow_redirects, **kw):
        return SimpleNamespace(status_code=302, headers={"location": "https://example.com/again"})

    with pytest.raises(BlockedURL):
        fetch_with_guard("https://example.com/a", client_get=loop)


def test_web_fetch_tool_refuses_loopback():
    from kompany.core.agent_tools.web_tools import _web_fetch

    out = _web_fetch({"url": "http://127.0.0.1:8000/credentials"}, ctx=None)
    assert out.startswith("ERROR: web_fetch refused")
