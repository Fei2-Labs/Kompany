"""Login returns the founder to what they asked for (or the start page), never to the old /dashboard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kompany.core.engine import KompanyEngine
from kompany.interfaces import api
from kompany.interfaces.api_parts.dashboard import _safe_next, after_login_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    monkeypatch.setenv("WEB_DASHBOARD_TOKEN", "t0ken-for-tests")
    e = KompanyEngine(); monkeypatch.setattr(api, "_engine", e)
    monkeypatch.setattr(api, "BOARD_AVAILABLE", True)
    return TestClient(api.app, follow_redirects=False), e


def test_safe_next_rejects_offsite():
    assert _safe_next("/#/talk") == "/#/talk" and _safe_next("/projects?x=1") == "/projects?x=1"
    for bad in ("https://evil", "//evil/x", "javascript:alert(1)", "", None, "  "):
        assert _safe_next(bad) is None


def test_landing_requests_go_to_start_page_deep_links_are_kept(client):
    c, e = client
    assert after_login_path(e, None) == "/"
    assert after_login_path(e, "/dashboard") == "/" and after_login_path(e, "/ui/") == "/"
    assert after_login_path(e, "/#/projects") == "/#/projects"
    e.set_ui_preferences(start_page="terminal")
    assert after_login_path(e, "/") == "/ui/"
    assert after_login_path(e, "https://evil") == "/ui/"


def test_guard_carries_next_and_login_honours_it(client):
    c, e = client
    r = c.get("/#/talk", headers={"accept": "text/html"})
    assert r.status_code == 303 and r.headers["location"].startswith("/dashboard/login?next=")
    r = c.get("/ui/settings.html", headers={"accept": "text/html"})
    assert r.headers["location"] == "/dashboard/login?next=/ui/settings.html"
    page = c.get("/dashboard/login?next=/ui/settings.html")
    assert 'name="next" value="/ui/settings.html"' in page.text
    # deep link kept
    r = c.post("/dashboard/login", data={"dashboard_token": "t0ken-for-tests", "next": "/ui/settings.html"})
    assert r.status_code == 303 and r.headers["location"] == "/ui/settings.html" and "kompany_dashboard_session" in r.headers.get("set-cookie", "")
    # landing → start page (board by default), never the legacy /dashboard
    r = c.post("/dashboard/login", data={"dashboard_token": "t0ken-for-tests", "next": "/"})
    assert r.headers["location"] == "/"
    r = c.post("/dashboard/login", data={"dashboard_token": "t0ken-for-tests"})
    assert r.headers["location"] == "/"
    e.set_ui_preferences(start_page="needs-you")
    r = c.post("/dashboard/login", data={"dashboard_token": "t0ken-for-tests", "next": "/ui/"})
    assert r.headers["location"] == "/#/needs-you"
    # offsite next is ignored; wrong token still 401
    r = c.post("/dashboard/login", data={"dashboard_token": "t0ken-for-tests", "next": "https://evil"})
    assert r.headers["location"] == "/#/needs-you"
    assert c.post("/dashboard/login", data={"dashboard_token": "nope"}).status_code == 401
    # the login page escapes what it echoes
    page = c.get('/dashboard/login?next=/x"><script>')
    assert "<script>" not in page.text.split('name="next"')[1][:80]


def test_session_exchange_sets_cookie_without_the_form(client):
    """The desktop shell in remote mode logs in with one GET; the token never survives the redirect."""
    c, e = client
    r = c.get("/dashboard/session?token=t0ken-for-tests&next=/%23/talk")
    assert r.status_code == 303 and r.headers["location"] == "/#/talk"
    cookie = r.headers["set-cookie"]
    assert "kompany_dashboard_session=" in cookie and "t0ken-for-tests" not in cookie
    assert f"Max-Age={30 * 24 * 60 * 60}" in cookie
    c.cookies.set("kompany_dashboard_session", r.cookies["kompany_dashboard_session"])
    assert c.get("/preferences").status_code == 200
    # wrong or missing token: back to the form, no cookie
    r = c.get("/dashboard/session?token=nope&next=/x")
    assert r.status_code == 303 and r.headers["location"] == "/dashboard/login?next=/x" and "set-cookie" not in r.headers
