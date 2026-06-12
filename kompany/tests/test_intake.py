"""Browser intake endpoint tests (issue #23, task 06-12-browser-intake).

POST /intake: token guard, ops routing through process_directive,
dev routing to dev-queue.md + best-effort gh issue, rate limit, dedupe.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import kompany.interfaces.api as api_mod
from kompany.interfaces.api import app


class _FakeEngine:
    def __init__(self, tmp_path, token="sekret"):
        self.settings = SimpleNamespace(
            intake_token=token,
            mobile_remote_token="",
            data_dir=tmp_path,
        )
        self.directives: list[str] = []

    def process_directive(self, text, session_id=None):
        self.directives.append(text)
        return SimpleNamespace(
            to_dict=lambda: {"status": "completed", "message": "ok", "session_id": session_id}
        )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = _FakeEngine(tmp_path)
    monkeypatch.setattr(api_mod, "_engine", engine)
    api_mod._intake_reset_guards()
    c = TestClient(app)
    c.engine = engine
    yield c
    api_mod._intake_reset_guards()


def _auth(token="sekret"):
    return {"Authorization": f"Bearer {token}"}


def _ok_gh(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="https://github.com/x/y/issues/1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_ops_intake_routes_through_process_directive(client):
    r = client.post(
        "/intake",
        json={"url": "https://ex.com/a", "selection": "quoted text", "note": "research this", "kind": "ops"},
        headers=_auth(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "routed"
    assert body["kind"] == "ops"
    assert body["result"]["status"] == "completed"
    assert client.engine.directives == [
        "Intake from browser: research this\nURL: https://ex.com/a\nSelection: quoted text"
    ]


def test_kind_auto_takes_ops_path(client):
    r = client.post("/intake", json={"url": "https://ex.com/b"}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["kind"] == "ops"
    assert len(client.engine.directives) == 1


def test_dev_intake_appends_queue_and_files_issue(client, tmp_path, monkeypatch):
    calls = _ok_gh(monkeypatch)
    r = client.post(
        "/intake",
        json={"url": "https://ex.com/c", "note": "fix the tick loop", "kind": "dev"},
        headers=_auth(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["github"]["filed"] is True
    queue = tmp_path / "dev-queue.md"
    assert queue.exists()
    assert "fix the tick loop — https://ex.com/c" in queue.read_text()
    assert client.engine.directives == []  # never hit the CEO
    assert calls and calls[0][:3] == ["gh", "issue", "create"]


def test_dev_intake_tolerates_gh_failure(client, tmp_path, monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", boom)
    r = client.post(
        "/intake",
        json={"url": "https://ex.com/d", "kind": "dev"},
        headers=_auth(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["github"]["filed"] is False
    assert "gh" in body["github"]["error"]
    assert (tmp_path / "dev-queue.md").exists()


def test_missing_and_bad_token_rejected(client):
    assert client.post("/intake", json={"url": "https://ex.com"}).status_code == 401
    assert (
        client.post("/intake", json={"url": "https://ex.com"}, headers=_auth("wrong")).status_code
        == 401
    )


def test_unconfigured_intake_is_503(client):
    client.engine.settings.intake_token = ""
    client.engine.settings.mobile_remote_token = ""
    r = client.post("/intake", json={"url": "https://ex.com"}, headers=_auth())
    assert r.status_code == 503


def test_falls_back_to_mobile_remote_token(client):
    client.engine.settings.intake_token = ""
    client.engine.settings.mobile_remote_token = "mob"
    r = client.post("/intake", json={"url": "https://ex.com"}, headers=_auth("mob"))
    assert r.status_code == 200


def test_dedupe_identical_url_note_within_window(client):
    payload = {"url": "https://ex.com/e", "note": "same"}
    r1 = client.post("/intake", json=payload, headers=_auth())
    r2 = client.post("/intake", json=payload, headers=_auth())
    assert r1.status_code == 200 and r1.json()["status"] == "routed"
    assert r2.status_code == 200 and r2.json()["status"] == "duplicate"
    assert len(client.engine.directives) == 1


def test_rate_limit_10_per_minute(client):
    for i in range(10):
        r = client.post("/intake", json={"url": f"https://ex.com/{i}"}, headers=_auth())
        assert r.status_code == 200
    r = client.post("/intake", json={"url": "https://ex.com/over"}, headers=_auth())
    assert r.status_code == 429
