"""Tests for the C-suite review gate (ADR-0007).

Classification is pure-function tested. The review flow runs against real
SQLite-backed stores (approvals / projects / audit) with a fake agent
registry whose reviewers return scripted text — no LLM, no network. The two
task-completion paths are exercised by mocking ``request_csuite_review`` and
asserting it fires for outward tasks and not for internal ones.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kompany.core import csuite_review
from kompany.core.csuite_review import (
    ACTION_CSUITE_REVIEW,
    approve_csuite_review,
    gate_completed_task,
    reject_csuite_review,
    request_csuite_review,
    revision_requested_csuite_review,
)
from kompany.core.deliverables import (
    REVIEW_ROLES,
    DeliverableClass,
    classify_deliverable,
    needs_csuite_review,
)
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.models import (
    ApprovalRequest,
    Project,
    ProjectType,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeReviewer:
    """Returns a scripted review reply (text + zero cost)."""

    def __init__(self, text: str, raise_exc: bool = False):
        self._text = text
        self._raise = raise_exc

    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        if self._raise:
            raise RuntimeError("agent boom")
        return SimpleNamespace(text=self._text, cost_usd=0.0)


class FakeRegistry:
    """Maps role -> FakeReviewer; raises on unknown role unless default set."""

    def __init__(self, by_role: dict[str, FakeReviewer]):
        self._by_role = by_role

    def get(self, role, company_state=None):
        if role not in self._by_role:
            raise ValueError(f"Unknown agent role: {role}")
        return self._by_role[role]


class FakeEngine:
    def __init__(self, tmp_path, registry: FakeRegistry | None = None):
        self.db = Database(tmp_path)
        self.projects = Projects(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.audit = AuditLog(self.db)
        self.registry = registry or FakeRegistry({})


def _project(engine, ptype=ProjectType.REVENUE) -> Project:
    return engine.projects.create(Project(name="P", type=ptype, plan={}))


def _task(engine, project, title="Send launch email", output="hello world"):
    task = engine.projects.create_task(Task(
        project_id=project.id, title=title, assigned_agent="writer",
    ))
    engine.projects.update_task_status(
        task.id, TaskStatus.DELIVERED,
        result={"output": output, "outcome": "delivered"},
    )
    return engine.projects.get_task(task.id)


def _pass_registry(roles):
    return FakeRegistry({r: FakeReviewer("No defects.\nPASS") for r in roles})


# ---------------------------------------------------------------------------
# classify_deliverable
# ---------------------------------------------------------------------------


def test_classify_each_class():
    assert classify_deliverable("Refactor internal billing module") == (
        DeliverableClass.INTERNAL
    )
    assert classify_deliverable("Send cold email to prospects") == (
        DeliverableClass.EXTERNAL_COMMUNICATION
    )
    assert classify_deliverable("Publish blog post about our values") == (
        DeliverableClass.PUBLISHED_CONTENT
    )
    assert classify_deliverable("Launch the product to users") == (
        DeliverableClass.PRODUCT_LAUNCH
    )
    assert classify_deliverable("Decide whether to pivot the company") == (
        DeliverableClass.CRITICAL_DECISION
    )


def test_classify_action_type_overrides_title():
    # An internal-sounding title still classifies by the explicit action.
    assert classify_deliverable(
        "Internal note", action_type="email"
    ) == DeliverableClass.EXTERNAL_COMMUNICATION
    assert classify_deliverable(
        "draft", action_type="channel_post"
    ) == DeliverableClass.PUBLISHED_CONTENT


def test_classify_strategic_project_tiebreaker():
    assert classify_deliverable(
        "Finalize the strategy", project_type="strategic"
    ) == DeliverableClass.CRITICAL_DECISION
    # Same title in a non-strategic project stays internal.
    assert classify_deliverable(
        "Finalize the strategy", project_type="operational"
    ) == DeliverableClass.INTERNAL


def test_needs_review_skips_internal_only():
    assert needs_csuite_review(DeliverableClass.INTERNAL) is False
    for cls in DeliverableClass:
        if cls is DeliverableClass.INTERNAL:
            continue
        assert needs_csuite_review(cls) is True


def test_review_roles_cover_every_outward_class():
    valid = {
        "ceo", "cfo", "cto", "cpo", "cmo", "cro", "coo", "csa", "ciso",
        "cos", "cv",
    }
    for cls in DeliverableClass:
        assert cls in REVIEW_ROLES
        if cls is DeliverableClass.INTERNAL:
            assert REVIEW_ROLES[cls] == []
        else:
            roles = REVIEW_ROLES[cls]
            assert roles, f"{cls} has no reviewers"
            assert set(roles) <= valid, f"{cls} has unknown role"


# ---------------------------------------------------------------------------
# request_csuite_review
# ---------------------------------------------------------------------------


def test_hold_files_high_severity_approval(tmp_path):
    roles = REVIEW_ROLES[DeliverableClass.EXTERNAL_COMMUNICATION]
    reg = FakeRegistry({
        roles[0]: FakeReviewer("- Tone is off-brand\nHOLD"),
        roles[1]: FakeReviewer("No defects.\nPASS"),
    })
    engine = FakeEngine(tmp_path, reg)
    project = _project(engine)
    task = _task(engine, project)

    out = request_csuite_review(
        engine, task, project, DeliverableClass.EXTERNAL_COMMUNICATION
    )

    assert out["status"] == "pending_review"
    req = engine.approvals.get(out["approval_id"])
    assert req.action_type == ACTION_CSUITE_REVIEW
    assert req.severity == "high"
    assert req.payload["task_id"] == task.id
    assert req.payload["deliverable_class"] == "external_communication"
    # Task stamped pending, carries the approval id.
    row = engine.projects.get_task(task.id)
    assert row.result["csuite_review_pending"] is True
    assert row.result["csuite_review_approval_id"] == req.id
    assert "csuite_approved" not in row.result
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "csuite_review.held" in events


def test_blocking_defect_without_hold_still_files(tmp_path):
    # A reviewer that votes PASS but lists a concrete defect must still hold.
    roles = REVIEW_ROLES[DeliverableClass.EXTERNAL_COMMUNICATION]
    reg = FakeRegistry({
        roles[0]: FakeReviewer("- Missing unsubscribe link\nPASS"),
        roles[1]: FakeReviewer("No defects.\nPASS"),
    })
    engine = FakeEngine(tmp_path, reg)
    project = _project(engine)
    task = _task(engine, project)

    out = request_csuite_review(
        engine, task, project, DeliverableClass.EXTERNAL_COMMUNICATION
    )
    assert out["status"] == "pending_review"


def test_clean_pass_stamps_approved_no_card(tmp_path):
    roles = REVIEW_ROLES[DeliverableClass.PUBLISHED_CONTENT]
    engine = FakeEngine(tmp_path, _pass_registry(roles))
    project = _project(engine)
    task = _task(engine, project, title="Publish blog post")

    out = request_csuite_review(
        engine, task, project, DeliverableClass.PUBLISHED_CONTENT
    )

    assert out["status"] == "approved"
    row = engine.projects.get_task(task.id)
    assert row.result["csuite_approved"] is True
    assert "csuite_review_pending" not in row.result
    assert engine.approvals.list_pending() == []
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "csuite_review.passed" in events


def test_agent_failure_fails_closed(tmp_path):
    roles = REVIEW_ROLES[DeliverableClass.EXTERNAL_COMMUNICATION]
    reg = FakeRegistry({
        roles[0]: FakeReviewer("", raise_exc=True),
        roles[1]: FakeReviewer("No defects.\nPASS"),
    })
    engine = FakeEngine(tmp_path, reg)
    project = _project(engine)
    task = _task(engine, project)

    out = request_csuite_review(
        engine, task, project, DeliverableClass.EXTERNAL_COMMUNICATION
    )
    assert out["status"] == "pending_review"
    req = engine.approvals.get(out["approval_id"])
    assert req.payload["review_failed_closed"] is True


# ---------------------------------------------------------------------------
# approve / reject / revision handlers + idempotency
# ---------------------------------------------------------------------------


def _held_request(engine, project, task) -> ApprovalRequest:
    reg = _pass_registry([])  # not used; we file directly
    return engine.approvals.create(ApprovalRequest(
        action_type=ACTION_CSUITE_REVIEW,
        summary="C-suite review HOLD",
        payload={
            "task_id": task.id,
            "project_id": project.id,
            "deliverable_class": "external_communication",
            "findings": [{"role": "cmo", "verdict": "HOLD",
                          "defects": ["off brand"]}],
        },
        project_id=project.id,
        requested_by="csuite_review",
        severity="high",
    ))


def test_approve_clears_task_to_ship(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _project(engine)
    task = _task(engine, project)
    engine.projects.update_task_status(
        task.id, TaskStatus.DELIVERED,
        result={"output": "x", "csuite_review_pending": True},
    )
    req = _held_request(engine, project, task)

    res = approve_csuite_review(engine, req)
    assert res["status"] == "approved"
    row = engine.projects.get_task(task.id)
    assert row.result["csuite_approved"] is True
    assert row.result["csuite_review_pending"] is False
    assert engine.approvals.get(req.id).payload["effect_applied"] is True
    # Idempotent replay.
    replay = approve_csuite_review(engine, engine.approvals.get(req.id))
    assert replay["status"] == "already_applied"


def test_reject_holds_deliverable(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _project(engine)
    task = _task(engine, project)
    req = _held_request(engine, project, task)

    res = reject_csuite_review(engine, req)
    assert res["status"] == "rejected"
    row = engine.projects.get_task(task.id)
    assert row.result["csuite_rejected"] is True
    assert row.result["csuite_approved"] is False
    assert row.result["csuite_review_pending"] is False
    replay = reject_csuite_review(engine, engine.approvals.get(req.id))
    assert replay["status"] == "already_applied"


def test_revision_refiles_and_stamps(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _project(engine)
    task = _task(engine, project)
    req = _held_request(engine, project, task)

    successor = revision_requested_csuite_review(engine, req, "fix the tone")
    assert successor.predecessor_id == req.id
    assert successor.action_type == ACTION_CSUITE_REVIEW
    assert successor.payload["revision_hint"] == "fix the tone"
    assert successor.payload["effect_applied"] is False
    row = engine.projects.get_task(task.id)
    assert row.result["csuite_revision_requested"] is True
    assert row.result["csuite_revision_feedback"] == "fix the tone"
    assert row.result["csuite_review_pending"] is True


# ---------------------------------------------------------------------------
# gate_completed_task (front door used by both completion paths)
# ---------------------------------------------------------------------------


def test_gate_skips_internal_deliverable(tmp_path):
    roles = REVIEW_ROLES[DeliverableClass.EXTERNAL_COMMUNICATION]
    engine = FakeEngine(tmp_path, _pass_registry(roles))
    project = _project(engine)
    task = _task(engine, project, title="Refactor internal module")

    out = gate_completed_task(engine, task, project, "delivered")
    assert out is None
    assert engine.approvals.list_pending() == []


def test_gate_skips_non_ship_outcome(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _project(engine)
    task = _task(engine, project, title="Send launch email")
    assert gate_completed_task(engine, task, project, "blocked") is None


def test_gate_runs_review_for_outward_deliverable(tmp_path):
    roles = REVIEW_ROLES[DeliverableClass.EXTERNAL_COMMUNICATION]
    reg = FakeRegistry({
        roles[0]: FakeReviewer("- bad\nHOLD"),
        roles[1]: FakeReviewer("No defects.\nPASS"),
    })
    engine = FakeEngine(tmp_path, reg)
    project = _project(engine)
    task = _task(engine, project, title="Send outreach email to leads")

    out = gate_completed_task(engine, task, project, "delivered")
    assert out["status"] == "pending_review"


# ---------------------------------------------------------------------------
# Both completion paths invoke the gate
# ---------------------------------------------------------------------------


def test_legacy_runner_invokes_gate(tmp_path, monkeypatch):
    import kompany.core.csuite_review as cr
    from kompany.core.runner import ProjectRunner, TaskSpec
    from kompany.state.agent_status import AgentStatusStore
    from kompany.state.checkpoints import CheckpointStore
    from kompany.state.memory import AgentMemory

    class RunnerEngine:
        def __init__(self):
            self.db = Database(tmp_path)
            self.projects = Projects(self.db)
            self.audit = AuditLog(self.db)
            self.agent_status = AgentStatusStore(self.db)
            self.checkpoints = CheckpointStore(self.db)
            self.memory = AgentMemory(self.db)
            self.registry = FakeRegistry({
                "writer": FakeReviewer("done")
            })

        def get_company_state(self):
            return {}

    engine = RunnerEngine()
    project = engine.projects.create(Project(
        name="R", type=ProjectType.REVENUE, plan={},
    ))
    runner = ProjectRunner(engine)
    monkeypatch.setattr(
        runner, "_decompose",
        lambda project: [TaskSpec(
            title="Send launch email to customers",
            assigned_agent="writer", prompt="",
        )],
    )
    calls = []
    monkeypatch.setattr(
        cr, "gate_completed_task",
        lambda *a, **k: calls.append((a[1].title, a[3])) or None,
    )
    # The runner imports the symbol lazily, so patch the module attr.
    runner.run(project.id)
    assert calls and calls[0][0] == "Send launch email to customers"
    assert calls[0][1] in ("delivered", "completed")


def test_harness_finish_invokes_gate(tmp_path, monkeypatch):
    import kompany.core.csuite_review as cr
    from kompany.core.harness import HarnessResult
    from kompany.core.harness_execution import executor as ex

    class HEngine:
        def __init__(self):
            self.db = Database(tmp_path)
            self.projects = Projects(self.db)
            self.audit = AuditLog(self.db)
            self.memory = _NoopMemory()
            self.checkpoints = _NoopCheckpoints()
            self.registry = FakeRegistry({})

    class _NoopMemory:
        def remember(self, **k):
            pass

    class _NoopCheckpoints:
        def save(self, **k):
            pass

    engine = HEngine()
    project = engine.projects.create(Project(
        name="H", type=ProjectType.REVENUE, plan={},
    ))
    task = engine.projects.create_task(Task(
        project_id=project.id, title="Publish blog post about launch",
        assigned_agent="writer",
    ))
    run_result = HarnessResult(
        final_text="published draft", files_changed=[], session_id="s1",
        exit_status="ok", error=None,
    )
    result = SimpleNamespace(
        tasks_completed=0, tasks_failed=0, total_ai_cost=0.0, outputs=[],
    )
    calls = []
    monkeypatch.setattr(
        cr, "gate_completed_task",
        lambda *a, **k: calls.append((a[1].title, a[3])) or None,
    )
    monkeypatch.setattr(
        ex, "classify_harness_outcome",
        lambda rr, te: ("delivered", "ship it"),
    )
    ex._finish_task(
        engine, task, project, result, run_result,
        booked=0.0, tool_events=0, vehicle="claude",
    )
    assert calls and calls[0][0] == "Publish blog post about launch"
    assert calls[0][1] == "delivered"
