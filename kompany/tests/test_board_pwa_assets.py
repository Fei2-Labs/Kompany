"""PWA shell files at the board root are served as files, not as the SPA HTML."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kompany.interfaces.api import board_file_response


@pytest.fixture
def client(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>shell</html>")
    (dist / "sw.js").write_text("self.addEventListener('fetch',()=>{})")
    (dist / "manifest.webmanifest").write_text('{"name":"Kompany"}')
    (tmp_path / "secret.txt").write_text("outside dist")
    app = FastAPI()

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        return board_file_response(dist, full_path)

    return TestClient(app)


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
    assert "shell" in client.get("/..%2Fsecret.txt").text
    assert "shell" in client.get("/.hidden").text
