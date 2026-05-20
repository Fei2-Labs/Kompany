"""Tests for project runner orchestration hooks."""

from __future__ import annotations

from types import SimpleNamespace

from kompany.core.runner import ProjectRunner, TaskSpec
from kompany.state.agent_status import AgentStatusStore
from kompany.state.audit import AuditLog
from kompany.state.checkpoints import CheckpointStore
from kompany.state.database import Database
from kompany.state.memory import AgentMemory
from kompany.state.models import Project, ProjectType, Task, TaskStatus
from kompany.state.projects import Projects


class FakeAgent:
    calls = 0

    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        FakeAgent.calls += 1
        return SimpleNamespace(text="done", cost_usd=0.01)


class FakeRegistry:
    def get(self, role, company_state=None):
        return FakeAgent()


class FakeEngine:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path)
        self.projects = Projects(self.db)
        self.audit = AuditLog(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.memory = AgentMemory(self.db)
        self.registry = FakeRegistry()

    def get_company_state(self):
        return {}


def test_runner_records_task_audit_status_and_checkpoint(tmp_path, monkeypatch):
    FakeAgent.calls = 0
    engine = FakeEngine(tmp_path)
    project = engine.projects.create(Project(
        name="Revenue",
        type=ProjectType.REVENUE,
        plan={"paths": [{"name": "Consulting"}], "recommended_path": "Consulting"},
    ))
    runner = ProjectRunner(engine)
    monkeypatch.setattr(
        runner,
        "_decompose",
        lambda project: [TaskSpec(title="Write offer", assigned_agent="writer", prompt="")],
    )

    result = runner.run(project.id)

    assert result.tasks_completed == 1
    assert engine.agent_status.get("writer")["status"] == "idle"
    latest = engine.checkpoints.latest(project.id)
    assert latest is not None
    assert latest["state"]["tasks_completed"] == 1
    event_types = [event["event_type"] for event in engine.audit.recent(limit=10)]
    assert "project.execution_started" in event_types
    assert "task.started" in event_types
    assert "checkpoint.saved" in event_types
    assert "task.completed" in event_types
    assert "project.execution_completed" in event_types


def test_runner_resume_skips_completed_and_retries_failed(tmp_path):
    FakeAgent.calls = 0
    engine = FakeEngine(tmp_path)
    project = engine.projects.create(Project(
        name="Resume",
        type=ProjectType.OPERATIONAL,
        plan={},
    ))
    completed = engine.projects.create_task(Task(
        project_id=project.id,
        title="Already done",
        assigned_agent="writer",
        status=TaskStatus.COMPLETED,
    ))
    failed = engine.projects.create_task(Task(
        project_id=project.id,
        title="Retry this",
        assigned_agent="writer",
        status=TaskStatus.FAILED,
    ))
    engine.checkpoints.save(
        project_id=project.id,
        task_id=completed.id,
        step_index=1,
        state={"last_completed_task": completed.id},
    )

    result = ProjectRunner(engine).resume(project.id)

    assert result.tasks_completed == 1
    assert result.tasks_failed == 0
    assert FakeAgent.calls == 1
    statuses = {task.id: task.status for task in engine.projects.list_tasks(project.id)}
    assert statuses[completed.id] == TaskStatus.COMPLETED
    assert statuses[failed.id] == TaskStatus.COMPLETED
    event_types = [event["event_type"] for event in engine.audit.recent(limit=20)]
    assert "project.resume_started" in event_types
    assert "project.resume_completed" in event_types
