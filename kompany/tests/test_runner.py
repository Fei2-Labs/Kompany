"""Tests for project runner orchestration hooks."""

from __future__ import annotations

from types import SimpleNamespace

from kompany.core.runner import ProjectRunner, TaskSpec
from kompany.core.run_context import new_run_id, run_scope
from kompany.state.agent_status import AgentStatusStore
from kompany.state.audit import AuditLog
from kompany.state.checkpoints import CheckpointStore
from kompany.state.database import Database
from kompany.state.delegations import DelegationStore
from kompany.state.memory import AgentMemory
from kompany.state.models import (
    Delegation,
    Project,
    ProjectType,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects


class FakeAgent:
    calls = 0
    last_prompt = ""

    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        FakeAgent.calls += 1
        FakeAgent.last_prompt = prompt
        return SimpleNamespace(text="done", cost_usd=0.01)


class FakeRegistry:
    def get(self, role, company_state=None):
        return FakeAgent()


class FakeEngine:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path)
        self.projects = Projects(self.db)
        self.delegations = DelegationStore(self.db, self.projects)
        self.audit = AuditLog(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.memory = AgentMemory(self.db)
        self.registry = FakeRegistry()
        self.reconciled_delegated_tasks = []
        self.reconcile_error = None

    def reconcile_delegated_task(self, task_id):
        if self.reconcile_error:
            raise self.reconcile_error
        self.reconciled_delegated_tasks.append(task_id)

    def _fail_delegation_reconciliation(self, task, project, exc):
        return self.delegations.fail(task.delegation_id, str(exc))

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


def test_runner_uses_fresh_delegation_packet_and_pushes_child_result(tmp_path):
    engine = FakeEngine(tmp_path)
    project = engine.projects.create(Project(
        id="vinted",
        name="Vinted",
        type=ProjectType.REVENUE,
    ))
    parent_run_id = new_run_id()
    delegation = engine.delegations.create(Delegation(
        session_id="s-1",
        directive_id="dir-1",
        project_id=project.id,
        parent_run_id=parent_run_id,
        context_packet={
            "user_intent": "Review campaign performance and budget",
            "expected_outcome": "One reconciled recommendation",
            "constraints": {"max_depth": 1},
            "artifact_refs": [],
        },
        children=[Task(
            project_id=project.id,
            title="CMO campaign review",
            assigned_agent="cmo",
        )],
    ))
    engine.memory.remember(
        "cmo",
        "OTHER PROJECT SECRET",
        category="observation",
    )

    with run_scope(parent=parent_run_id) as child_run_id:
        task_id = ProjectRunner(engine).run_one_pending(project.id)

    assert task_id == delegation.children[0].id
    assert "Review campaign performance and budget" in FakeAgent.last_prompt
    assert "One reconciled recommendation" in FakeAgent.last_prompt
    assert "OTHER PROJECT SECRET" not in FakeAgent.last_prompt
    assert engine.reconciled_delegated_tasks == [task_id]
    persisted = engine.projects.get_task(task_id)
    assert persisted.execution_run_id == child_run_id


def test_runner_reconciliation_failure_preserves_child_result(tmp_path):
    engine = FakeEngine(tmp_path)
    project = engine.projects.create(Project(
        id="vinted",
        name="Vinted",
        type=ProjectType.REVENUE,
    ))
    delegation = engine.delegations.create(Delegation(
        session_id="s-1",
        directive_id="dir-1",
        project_id=project.id,
        context_packet={"user_intent": "Review campaign"},
        children=[Task(
            project_id=project.id,
            title="Review campaign",
            assigned_agent="cmo",
        )],
    ))
    engine.reconcile_error = RuntimeError("delegation store unavailable")

    task_id = ProjectRunner(engine).run_one_pending(project.id)

    assert engine.projects.get_task(task_id).status == TaskStatus.DELIVERED
    failed = engine.delegations.get(delegation.id)
    assert failed.status.value == "failed"
    assert failed.result == {"error": "delegation store unavailable"}


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
    # Re-run of the failed task now resolves to DELIVERED (honest outcome:
    # the agent produced an asset, no real integration executed). Was
    # COMPLETED before step-A honest-status landed.
    assert statuses[failed.id] == TaskStatus.DELIVERED
    event_types = [event["event_type"] for event in engine.audit.recent(limit=20)]
    assert "project.resume_started" in event_types
    assert "project.resume_completed" in event_types


def test_decompose_handles_first_move_week_plan(tmp_path):
    """First-move directives (proposed by the team during onboarding
    step 5) have plan blobs with ``week_plan``/``proposer_role`` rather
    than the ``paths`` revenue-project structure. The runner must
    decompose them into one task per weekday and round-robin ownership
    across proposer + collaborators — otherwise CEO would do every
    task while the other 10 agents sit idle on the dashboard, which
    is the user-visible bug surfaced 2026-05-26."""
    engine = FakeEngine(tmp_path)
    project = Project(
        name="Pre-sell $300 audit",
        type=ProjectType.OPERATIONAL,
        plan={
            "suggested_directive": "Pre-sell $300 audit to 10 prospects",
            "rationale": "fastest path to revenue",
            "proposer_role": "cro",
            "other_agents_involved": ["cmo", "cfo"],
            "week_plan": [
                "Mon: package offer",
                "Tue: build prospect list",
                "Wed: send 50 outbound messages",
                "Thu: run 3-5 sales calls",
                "Fri: close first paid audit",
            ],
            "success_metric": "≥1 paying customer",
            "source": "team_proposal_first_week",
        },
        assigned_agents=[],
    )
    engine.projects.create(project)

    tasks = ProjectRunner(engine)._decompose(project)
    assert len(tasks) == 5
    titles = [t.title for t in tasks]
    assert titles[0] == "Mon: package offer"
    assert titles[4] == "Fri: close first paid audit"
    # Round-robin across [cro, cmo, cfo]
    assigned = [t.assigned_agent for t in tasks]
    assert assigned == ["cro", "cmo", "cfo", "cro", "cmo"]


def test_first_move_project_completes_and_materializes(tmp_path):
    """A first-move directive (week_plan) must be marked COMPLETED once
    all its tasks finish — and materialize an episode — so the dashboard
    shows a finished run instead of "team working" forever. Re-running
    must NOT duplicate the task set."""
    from kompany.state.models import ProjectStatus

    engine = FakeEngine(tmp_path)
    materialized: list[str] = []
    engine.episodes = SimpleNamespace(
        record_or_update=lambda pid: materialized.append(pid) or {"retention_tier": "full"}
    )

    project = Project(
        name="Pre-sell audit",
        type=ProjectType.OPERATIONAL,
        plan={
            "week_plan": ["Mon: a", "Tue: b", "Wed: c"],
            "proposer_role": "cro",
            "other_agents_involved": [],
            "source": "team_proposal_first_week",
        },
        assigned_agents=[],
    )
    engine.projects.create(project)

    runner = ProjectRunner(engine)
    runner.run(project.id)
    # All 3 tasks done → completed + episode materialized.
    assert engine.projects.get(project.id).status == ProjectStatus.COMPLETED
    assert materialized == [project.id]
    assert len(engine.projects.list_tasks(project.id)) == 3

    # Re-run must not duplicate the task set (idempotent decomposition).
    runner.run(project.id)
    assert len(engine.projects.list_tasks(project.id)) == 3


def test_classify_outcome_blocked_vs_delivered():
    """Honest-status classifier: an agent that says it can't act is
    BLOCKED; one that produced an asset is DELIVERED. Never 'completed'
    until a real integration executes (mode-3 hybrid, step A)."""
    from kompany.core.runner import _classify_outcome

    out_blocked, action_b = _classify_outcome(
        "Wed: Send outreach to 40 contacts",
        "## Execution status\n\nI cannot truthfully confirm that messages "
        "were sent because I do not have access to your email or LinkedIn.",
    )
    assert out_blocked == "blocked"
    assert "send" in action_b.lower()

    out_delivered, action_d = _classify_outcome(
        "Mon: Define a $99 paid offer",
        "## Paid Offer Defined\n\nHere is the full offer copy and pricing...",
    )
    assert out_delivered == "delivered"
    assert action_d  # non-empty founder action


def test_first_move_tasks_end_delivered_not_completed(tmp_path):
    """End-to-end: a first-move run with a plain (non-integration) agent
    leaves tasks DELIVERED — not COMPLETED — and the project still
    completes + materializes (all tasks terminal)."""
    from types import SimpleNamespace
    from kompany.state.models import Project, ProjectType, ProjectStatus, TaskStatus

    engine = FakeEngine(tmp_path)
    engine.episodes = SimpleNamespace(record_or_update=lambda pid: {"retention_tier": "full"})
    project = Project(
        name="Pre-sell",
        type=ProjectType.OPERATIONAL,
        plan={"week_plan": ["Mon: a", "Tue: b"], "proposer_role": "cro",
              "other_agents_involved": [], "source": "team_proposal_first_week"},
        assigned_agents=[],
    )
    engine.projects.create(project)
    ProjectRunner(engine).run(project.id)
    statuses = {t.status for t in engine.projects.list_tasks(project.id)}
    assert statuses == {TaskStatus.DELIVERED}
    assert engine.projects.get(project.id).status == ProjectStatus.COMPLETED


def test_projects_get_tolerates_draft_status(tmp_path):
    """A 'draft' row must construct a Project without a pydantic enum
    error. Templates stage directives as draft; before ProjectStatus
    had a DRAFT member, projects.get() on a draft row crashed — which
    killed the post-onboarding kickoff mid-run (project.kickoff_failed:
    'Input should be active/paused/completed/failed input_value=draft')."""
    from kompany.state.models import ProjectStatus

    engine = FakeEngine(tmp_path)
    engine.db.execute(
        "INSERT INTO projects (id, name, type, status, plan, assigned_agents, "
        "created_at, updated_at) VALUES "
        "('drafted', 'Staged directive', 'operational', 'draft', '{}', '[]', "
        "datetime('now'), datetime('now'))"
    )
    engine.db.commit()
    proj = engine.projects.get("drafted")
    assert proj is not None
    assert proj.status == ProjectStatus.DRAFT
