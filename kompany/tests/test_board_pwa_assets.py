"""PWA shell files at the board root are served as files, not as the SPA HTML."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kompany.core.engine import KompanyEngine
from kompany.interfaces import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>shell</html>")
    (dist / "sw.js").write_text("self.addEventListener('fetch',()=>{})")
    (dist / "manifest.webmanifest").write_text('{"name":"Kompany"}')
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    monkeypatch.delenv("WEB_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(api, "_engine", KompanyEngine())
    monkeypatch.setattr(api, "_BOARD_UI_DIR", dist)
    monkeypatch.setattr(api, "_BOARD_INDEX", dist / "index.html")
    return TestClient(api.app)


def test_service_worker_and_manifest_are_real_files(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "addEventListener" in sw.text
    assert sw.headers["cache-control"] == "no-cache"
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.json()["name"] == "Kompany"


def test_unknown_paths_still_get_the_spa_shell(client):
    assert "shell" in client.get("/some/client/route").text
    assert "shell" in client.get("/missing.js").text


def test_catch_all_never_escapes_dist(client):
    assert "shell" in client.get("/..%2Fsecret").text
    assert client.get("/events/x").status_code == 404
