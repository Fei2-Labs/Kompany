"""Desktop start page preference (dev-inbox #63): store, REST, /start resolution."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kompany.core.engine import KompanyEngine
from kompany.interfaces import api
from kompany.state.ui_preferences import START_PAGE_PATHS, UIPreferences, start_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    e = KompanyEngine(); monkeypatch.setattr(api, "_engine", e)
    return TestClient(api.app), e


def test_default_is_board_and_paths_are_same_origin():
    p = UIPreferences()
    assert p.start_page == "board" and START_PAGE_PATHS["board"] == "/"
    for path in START_PAGE_PATHS.values():
        assert path.startswith("/") and not path.startswith("//") and "://" not in path
    assert start_path(p, board_available=True) == "/"
    assert start_path(UIPreferences(start_page="talk"), board_available=True) == "/#/talk"
    # no board bundle → every board pane degrades to the terminal; terminal stays
    assert start_path(UIPreferences(start_page="talk"), board_available=False) == "/ui/"
    assert start_path(UIPreferences(start_page="terminal"), board_available=False) == "/ui/"


def test_rest_roundtrip_and_start_endpoint(client, monkeypatch):
    c, e = client
    assert c.get("/preferences").json()["start_page"] == "board"
    st = c.get("/start").json()
    assert st["start_page"] == "board" and {o["id"] for o in st["options"]} == set(START_PAGE_PATHS)
    assert st["path"] == ("/" if st["board_available"] else "/ui/")
    assert c.patch("/preferences", json={"start_page": "terminal"}).json()["start_page"] == "terminal"
    assert c.get("/start").json()["path"] == "/ui/"
    assert c.patch("/preferences", json={"start_page": "needs-you"}).status_code == 200
    monkeypatch.setattr(api, "BOARD_AVAILABLE", True)
    assert c.get("/start").json()["path"] == "/#/needs-you"
    monkeypatch.setattr(api, "BOARD_AVAILABLE", False)
    assert c.get("/start").json()["path"] == "/ui/"
    assert c.patch("/preferences", json={"start_page": "https://evil"}).status_code == 422
    # other preferences untouched by a start_page patch
    assert c.get("/preferences").json()["theme_id"] == "cyberpunk"
    # persisted in the DB, audited
    assert e.get_ui_preferences().start_page == "needs-you"
