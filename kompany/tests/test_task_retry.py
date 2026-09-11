"""Founder/CEO retry of a blocked task: four surfaces share engine.task_retry."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kompany.core.engine import KompanyEngine
from kompany.interfaces import api
from kompany.state.models import Project, ProjectType, Task, TaskStatus


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    e = KompanyEngine(); monkeypatch.setattr(api, "_engine", e)
    e.projects.create(Project(id="p1", name="P", type=ProjectType.OPERATIONAL, assigned_agents=["coo"]))
    e.projects.create_task(Task(id="t1", project_id="p1", title="Do stuff", assigned_agent="coo", status=TaskStatus.BLOCKED))
    e.projects.update_task_status_raw("t1", "blocked", reason="retry_exhausted: run died 3 times")
    e.db.execute("UPDATE tasks SET retry_count = 2 WHERE id = 't1'"); e.db.commit()
    return TestClient(api.app), e


def test_rest_retry_requeues_with_fresh_budget_and_exposes_reason(world):
    c, e = world
    detail = c.get("/projects/p1").json()["tasks"][0]
    assert detail["status"] == "blocked" and detail["block_reason"].startswith("retry_exhausted") and detail["retry_count"] == 2
    r = c.post("/tasks/t1/retry", json={"reason": "CEO says go again"})
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["previous_status"] == "blocked"
    t = e.projects.get_task("t1")
    assert t.status == TaskStatus.PENDING and t.retry_count == 0 and "CEO says go again" in t.block_reason
    assert any(row["action"] == "Task requeued: Do stuff" for row in e.db.execute("select action from audit_log").fetchall())
    # not retryable twice in a row; unknown id is 404
    assert c.post("/tasks/t1/retry").status_code == 409
    assert c.post("/tasks/nope/retry").status_code == 404


def test_mcp_and_sdk_share_the_op(world):
    _, e = world
    from kompany.interfaces.mcp_dispatch.dispatcher import dispatch_tool
    from kompany.interfaces.mcp_server import TOOLS as ALL_TOOLS

    assert any(t.name == "kompany_task_retry" for t in ALL_TOOLS)
    out = dispatch_tool(e, "kompany_task_retry", {"task_id": "t1"})
    assert out["status"] == "pending"
    assert "error" in dispatch_tool(e, "kompany_task_retry", {"task_id": "t1"})
