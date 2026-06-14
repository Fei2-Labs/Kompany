"""Tests for core/approval_effects — post-approve money hooks (PR5).

The engine delegate is exercised through the REAL
``KompanyEngine.approve_request`` / ``reject_request`` methods bound to a
fake engine (real SQLite-backed stores, scripted treasury) so the wiring
inside the engine — not just the effect functions — is under test.
"""

from __future__ import annotations

import pytest

from kompany.core.approval_effects import (
    HARNESS_EFFECT_ACTIONS,
    apply_post_approve_effect,
)
from kompany.core.engine import KompanyEngine
from kompany.core.harness_execution import (
    ACTION_BUDGET_INCREASE,
    ACTION_ENVELOPE_TOPUP,
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


class FakeEngine:
    """Real stores, scripted treasury — mirrors ``engine.fund_project``
    semantics (ValueError on insufficient unallocated treasury)."""

    def __init__(self, tmp_path, free_treasury=100.0):
        self.db = Database(tmp_path)
        self.projects = Projects(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.audit = AuditLog(self.db)
        self.free_treasury = float(free_treasury)
        self.fund_calls: list[tuple[str, float]] = []

    def fund_project(self, project_id, amount):
        if self.projects.get(project_id) is None:
            raise ValueError(f"Unknown project {project_id!r}")
        if amount > self.free_treasury:
            raise ValueError(
                f"Insufficient unallocated treasury: requested "
                f"€{amount:.2f}, free €{self.free_treasury:.2f}"
            )
        self.fund_calls.append((project_id, float(amount)))
        self.free_treasury -= float(amount)
        project = self.projects.add_funding(project_id, float(amount))
        return {
            "project_id": project_id,
            "funded": project.funded_amount,
            "spent": 0.0,
            "remaining": project.funded_amount,
        }


def _make_project(engine, funded=0.0) -> Project:
    return engine.projects.create(Project(
        name="Revenue", type=ProjectType.REVENUE, plan={},
        funded_amount=funded,
    ))


def _make_task(engine, project, **kwargs) -> Task:
    task = Task(
        project_id=project.id,
        title=kwargs.pop("title", "Build landing page"),
        assigned_agent=kwargs.pop("assigned_agent", "builder"),
        **kwargs,
    )
    return engine.projects.create_task(task)


def _topup_request(engine, project, task, amount=0.5) -> ApprovalRequest:
    """Mirror the payload the executor's park path creates."""
    return engine.approvals.create(ApprovalRequest(
        action_type=ACTION_ENVELOPE_TOPUP,
        summary="Top up the envelope",
        payload={
            "project_id": project.id,
            "task_id": task.id,
            "suggested_top_up_usd": amount,
        },
        project_id=project.id,
        requested_by="builder",
        severity="high",
    ))


def _increase_request(engine, project, task, increase=0.5) -> ApprovalRequest:
    """Mirror the payload the executor's budget-pause path creates."""
    return engine.approvals.create(ApprovalRequest(
        action_type=ACTION_BUDGET_INCREASE,
        summary="Approve budget increase",
        payload={
            "project_id": project.id,
            "task_id": task.id,
            "cap_usd": task.budget_cap_usd,
            "spent_usd": 0.62,
            "proposed_increase_usd": increase,
            "session_id": "sess-1",
        },
        project_id=project.id,
        requested_by="builder",
        severity="high",
    ))


# ---------------------------------------------------------------------------
# Envelope top-up
# ---------------------------------------------------------------------------


def test_approve_topup_funds_project(tmp_path):
    engine = FakeEngine(tmp_path, free_treasury=10.0)
    project = _make_project(engine)
    task = _make_task(engine, project)
    request = _topup_request(engine, project, task, amount=0.5)

    payload = KompanyEngine.approve_request(engine, request.id)

    assert payload["effect"]["status"] == "funded"
    assert engine.fund_calls == [(project.id, 0.5)]
    assert engine.projects.get(project.id).funded_amount == pytest.approx(0.5)
    # Idempotency stamp + system comment on the thread.
    row = engine.approvals.get(request.id)
    assert row.payload["effect_applied"] is True
    comments = engine.approvals.list_comments(request.id)
    assert any("Funded" in c.body for c in comments)
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "approval_effect.project_funded" in events


def test_approve_topup_is_idempotent_on_replay(tmp_path):
    """Replayed approve (remote replay / double-tap) must not double-fund."""
    engine = FakeEngine(tmp_path, free_treasury=10.0)
    project = _make_project(engine)
    task = _make_task(engine, project)
    request = _topup_request(engine, project, task, amount=0.5)

    KompanyEngine.approve_request(engine, request.id)
    replay = KompanyEngine.approve_request(engine, request.id)

    assert replay["effect"]["status"] == "already_applied"
    assert engine.fund_calls == [(project.id, 0.5)]  # exactly once
    assert engine.projects.get(project.id).funded_amount == pytest.approx(0.5)


def test_approve_topup_insufficient_treasury_comments_no_booking(tmp_path):
    engine = FakeEngine(tmp_path, free_treasury=0.1)
    project = _make_project(engine)
    task = _make_task(engine, project)
    request = _topup_request(engine, project, task, amount=0.5)

    payload = KompanyEngine.approve_request(engine, request.id)

    assert payload["effect"]["status"] == "insufficient_treasury"
    assert engine.fund_calls == []  # nothing booked
    assert engine.projects.get(project.id).funded_amount == 0.0
    # Mission integrity: shortfall explained + revenue path suggested.
    comments = engine.approvals.list_comments(request.id)
    assert len(comments) == 1
    assert "Insufficient unallocated treasury" in comments[0].body
    assert "revenue" in comments[0].body.lower()
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "approval_effect.funding_short" in events
    # NOT stamped → a later replay retries once treasury recovers.
    assert "effect_applied" not in (engine.approvals.get(request.id).payload)
    engine.free_treasury = 10.0
    retry = KompanyEngine.approve_request(engine, request.id)
    assert retry["effect"]["status"] == "funded"
    assert engine.fund_calls == [(project.id, 0.5)]


# ---------------------------------------------------------------------------
# Task budget increase
# ---------------------------------------------------------------------------


def test_approve_budget_increase_raises_task_cap(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project, budget_cap_usd=0.5)
    engine.projects.update_task_status(
        task.id, TaskStatus.PENDING,
        result={"output": "half done", "outcome": "budget_exceeded",
                "founder_action": "Approve the budget increase..."},
    )
    request = _increase_request(engine, project, task, increase=0.5)

    payload = KompanyEngine.approve_request(engine, request.id)

    assert payload["effect"]["status"] == "cap_raised"
    row = engine.projects.get_task(task.id)
    assert row.budget_cap_usd == pytest.approx(1.0)
    assert row.status == TaskStatus.PENDING  # still waiting for the re-run
    # founder_action updated, the rest of the result preserved.
    assert "approved" in row.result["founder_action"].lower()
    assert row.result["output"] == "half done"
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "approval_effect.budget_cap_raised" in events
    # Replay does not raise the cap twice.
    replay = KompanyEngine.approve_request(engine, request.id)
    assert replay["effect"]["status"] == "already_applied"
    assert engine.projects.get_task(task.id).budget_cap_usd == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Reject path
# ---------------------------------------------------------------------------


def test_reject_keeps_task_pending_with_next_step(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project, budget_cap_usd=0.5)
    engine.projects.update_task_status(
        task.id, TaskStatus.PENDING, result={"output": "half done"}
    )
    request = _increase_request(engine, project, task)

    payload = KompanyEngine.reject_request(
        engine, request.id, reason="too expensive"
    )

    assert payload["effect"]["status"] == "declined"
    row = engine.projects.get_task(task.id)
    assert row.status == TaskStatus.PENDING
    assert row.budget_cap_usd == pytest.approx(0.5)  # cap unchanged
    action = row.result["founder_action"]
    assert "re-scope" in action.lower() and "abandon" in action.lower()
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "approval_effect.declined" in events


def test_reject_topup_mentions_rescope_or_abandon(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    engine.projects.update_task_status(task.id, TaskStatus.PENDING)
    request = _topup_request(engine, project, task)

    payload = KompanyEngine.reject_request(engine, request.id)

    action = payload["effect"]["founder_action"]
    assert "re-scope" in action.lower() and "abandon" in action.lower()
    assert engine.fund_calls == []
    assert engine.projects.get_task(task.id).status == TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_effect_actions_cover_both_pr4_action_types():
    from kompany.core.self_update.effects import ACTION_SELF_UPDATE

    from kompany.channels.outbox import ACTION_CHANNEL_POST
    from kompany.core.csuite_review import ACTION_CSUITE_REVIEW
    from kompany.core.tool_actions import ACTION_TOOL_EXECUTE

    assert HARNESS_EFFECT_ACTIONS == {
        ACTION_ENVELOPE_TOPUP, ACTION_BUDGET_INCREASE, ACTION_SELF_UPDATE,
        ACTION_CHANNEL_POST, ACTION_TOOL_EXECUTE, ACTION_CSUITE_REVIEW,
    }


def test_invalid_payload_is_a_safe_noop(tmp_path):
    engine = FakeEngine(tmp_path)
    request = engine.approvals.create(ApprovalRequest(
        action_type=ACTION_BUDGET_INCREASE,
        summary="Broken payload",
        payload={},
    ))
    effect = apply_post_approve_effect(engine, request)
    assert effect["status"] == "invalid_payload"
    assert engine.fund_calls == []
