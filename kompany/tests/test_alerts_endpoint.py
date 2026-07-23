"""Tests for the external alert ingestion endpoint (POST /alerts)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kompany.interfaces.api import app


def _client() -> TestClient:
    return TestClient(app)


def test_file_alert_creates_pending_inbox_card(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    client = _client()
    resp = client.post(
        "/alerts",
        json={
            "source": "browser:linkedin",
            "severity": "high",
            "title": "LinkedIn browser stopped",
            "message": "systemd unit exited non-zero",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "filed"
    assert body["source"] == "browser:linkedin"

    # It shows in the inbox.
    inbox = client.get("/inbox").json()
    assert any(a["id"] == body["id"] for a in inbox)
    card = next(a for a in inbox if a["id"] == body["id"])
    assert card["action_type"] == "system_alert"
    assert card["severity"] == "high"
    assert card["summary"] == "LinkedIn browser stopped"
    assert card["payload"]["source"] == "browser:linkedin"


def test_file_alert_dedup_refreshes_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    client = _client()
    r1 = client.post(
        "/alerts",
        json={"source": "linkedin:session", "severity": "high",
              "title": "Session lost", "message": "first"},
    ).json()
    r2 = client.post(
        "/alerts",
        json={"source": "linkedin:session", "severity": "critical",
              "title": "Session still lost", "message": "second"},
    ).json()
    # Same card, refreshed — not a duplicate.
    assert r1["id"] == r2["id"]
    assert r2["status"] == "updated"
    inbox = client.get("/inbox").json()
    alerts = [a for a in inbox if a["payload"].get("source") == "linkedin:session"]
    assert len(alerts) == 1
    assert alerts[0]["summary"] == "Session still lost"
    assert alerts[0]["severity"] == "critical"


def test_resolve_alert_clears_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    client = _client()
    client.post(
        "/alerts",
        json={"source": "worker:linkedin", "severity": "medium",
              "title": "Worker failed", "message": ""},
    ).json()
    resp = client.post("/alerts/worker:linkedin/resolve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    inbox = client.get("/inbox").json()
    assert not any(a["payload"].get("source") == "worker:linkedin" for a in inbox)


def test_resolve_alert_no_pending_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    client = _client()
    resp = client.post("/alerts/never:filed/resolve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_pending"


def test_invalid_severity_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    client = _client()
    resp = client.post(
        "/alerts",
        json={"source": "x", "severity": "panic", "title": "t", "message": ""},
    )
    assert resp.status_code == 400
