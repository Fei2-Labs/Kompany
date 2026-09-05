"""GET /projects/{id} task rows expose assigned_agent + result (NEEDS YOU feed)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kompany.core.engine import KompanyEngine
from kompany.interfaces import api
from kompany.state.models import Project, ProjectType, Task, TaskStatus


def test_project_detail_tasks_carry_agent_and_result(monkeypatch):
    engine = KompanyEngine()
    monkeypatch.setattr(api, "_engine", engine)
    p = engine.projects.create(Project(name="Launch", type=ProjectType.REVENUE))
    task = engine.projects.create_task(Task(project_id=p.id, title="Email leads", assigned_agent="cro"))
    # Runtime path: a task lands BLOCKED with its connect ask in result.
    engine.projects.update_task_status(
        task.id, TaskStatus.BLOCKED,
        result={"founder_action": "connect an email account in Settings"},
    )
    body = TestClient(api.app).get(f"/projects/{p.id}").json()
    task = body["tasks"][0]
    assert task["status"] == "blocked"
    assert task["agent"] == "cro"
    assert task["result"]["founder_action"] == "connect an email account in Settings"
