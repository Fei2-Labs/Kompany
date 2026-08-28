from __future__ import annotations

from fastapi.testclient import TestClient

from kompany.interfaces.api import app
from kompany.interfaces.api_parts import ops


class _FakeMemory:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def remember(self, **kwargs):
        self.calls.append(kwargs)
        return 17


class _FakeProjects:
    def get(self, project_id: str):
        return object() if project_id == "a7fdb21b" else None


class _FakeEngine:
    def __init__(self) -> None:
        self.memory = _FakeMemory()
        self.projects = _FakeProjects()
        self.rebuilt: list[str] = []

    def rebuild_episode(self, project_id: str):
        self.rebuilt.append(project_id)
        return {"project_id": project_id}

    class _Audit:
        def record(self, *args, **kwargs):
            return None

    audit = _Audit()


def test_memory_ingest_persists_project_observation(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(ops, "get_engine", lambda: engine)

    response = TestClient(app).post(
        "/memories",
        json={
            "agent_role": "linkedin_growth",
            "content": "A substantive post reply produced a profile-view increase.",
            "category": "observation",
            "project_id": "a7fdb21b",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": 17,
        "project_id": "a7fdb21b",
        "context": "project:a7fdb21b",
    }
    assert engine.memory.calls[0]["agent_role"] == "linkedin_growth"
    assert engine.memory.calls[0]["context"] == "project:a7fdb21b"
    assert engine.rebuilt == ["a7fdb21b"]


def test_memory_ingest_rejects_unknown_project(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(ops, "get_engine", lambda: engine)

    response = TestClient(app).post(
        "/memories",
        json={
            "agent_role": "linkedin_growth",
            "content": "observation",
            "project_id": "missing",
        },
    )

    assert response.status_code == 404
    assert engine.memory.calls == []
