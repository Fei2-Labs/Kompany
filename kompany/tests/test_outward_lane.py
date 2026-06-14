"""Tests for the outward-execution lane (ADR-0008 Step 4).

Real SQLite. The pre-flight pipeline, the OutwardExecutor, and the LLM are
mocked so we assert ONLY the lane's orchestration:

  - gated action class → parks, executor NOT called,
  - auto + pre-flight pass + executor registered → executes, row 'sent',
  - auto + pre-flight HOLD → parks carrying the holds, no execute,
  - auto + no executor → parks "no executor", never silently dropped,
  - empty queue → no-op (no behaviour change),
  - lease prevents double-send,
  - suspend → no dispatch,
  - LLMUnavailable in pre-flight → parked + health event, NOT silent success.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kompany.channels.outbox import OutboxStore, enqueue_outward
from kompany.core import outward_lane as outward_lane_mod
from kompany.core.deai_gate import GateResult
from kompany.core.outward_lane import OUTWARD_LANE_ID, OutwardLane
from kompany.core.outward_preflight import PreflightResult
from kompany.core.watchdog import LLMUnavailable
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.health_events import HealthEvents


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeExecutor:
    """Records calls; configurable result. channel="" → channel-agnostic."""

    def __init__(self, channel="", ok=True, external_ref="ext-1"):
        self.channel = channel
        self.kind = "fake_executor"
        self.executor_id = "test.fake"
        self.calls: list[dict] = []
        self._ok = ok
        self._external_ref = external_ref

    def execute(self, action):
        self.calls.append(dict(action))
        return {
            "ok": self._ok,
            "detail": "done" if self._ok else "boom",
            "external_ref": self._external_ref,
        }


class FakeEngine:
    def __init__(self, tmp_path, executors=None, cap=0.0):
        from kompany.core.lane_registry import LaneRegistry

        self.db = Database(tmp_path)
        self.audit = AuditLog(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.health_events = HealthEvents(self.db)
        self.outward_policies = _PolicyStore(self.db)
        self.lane_registry = LaneRegistry(self.db)
        self.lane_registry.ensure_default()
        self.outward_executors = executors if executors is not None else []
        self.settings = SimpleNamespace(outward_auto_cost_cap_usd=cap)
        self.runtime = _Runtime()

    def get_company_state(self):
        return {"name": "Acme"}


class _Runtime:
    def __init__(self):
        self._state = None

    def get(self):
        return {"state": self._state} if self._state else {}

    def suspend(self):
        self._state = "suspended"


class _PolicyStore:
    """Minimal OutwardActionPolicyStore stand-in (no seeding noise)."""

    def __init__(self, db):
        self._modes: dict[str, str] = {}

    def set_mode(self, action_class, mode):
        self._modes[action_class] = mode

    def get(self, action_class):
        return self._modes.get(action_class, "gated")


def _patch_preflight(monkeypatch, result):
    monkeypatch.setattr(
        outward_lane_mod, "run_preflight", lambda engine, action: result
    )


def _patch_preflight_raises(monkeypatch, exc):
    def boom(engine, action):
        raise exc

    monkeypatch.setattr(outward_lane_mod, "run_preflight", boom)


def _enqueue(engine, channel="x", action_class="ai_voice_post", text="hi"):
    return enqueue_outward(
        engine, channel=channel, text=text,
        action_class=action_class, deliverable_class="published_content",
    )


def _row(engine, row_id):
    return OutboxStore(engine.db).get(row_id)


def _pending_outward(engine):
    return [
        r for r in engine.approvals.list_pending()
        if r.action_type == "outward_action"
    ]


# ---------------------------------------------------------------------------
# Empty queue → no-op
# ---------------------------------------------------------------------------


def test_empty_queue_is_noop(tmp_path):
    engine = FakeEngine(tmp_path)
    lane = OutwardLane(engine, engine.lane_registry)
    assert lane.dispatch_once() == []
    # No approval, no lease left behind.
    assert engine.approvals.list_pending() == []
    assert engine.lane_registry.has_active_lease(OUTWARD_LANE_ID) is False


# ---------------------------------------------------------------------------
# Gated class → parks, executor NOT called
# ---------------------------------------------------------------------------


def test_gated_class_parks_and_does_not_execute(tmp_path, monkeypatch):
    executor = FakeExecutor(channel="x")
    engine = FakeEngine(tmp_path, executors=[executor])
    # action class is gated by default (policy returns "gated").
    row = _enqueue(engine)
    # Pre-flight must never even run for a gated action.
    _patch_preflight_raises(monkeypatch, AssertionError("preflight ran!"))

    lane = OutwardLane(engine, engine.lane_registry)
    actions = lane.dispatch_once()

    assert actions == [f"outward_parked:{row['id']}"]
    assert executor.calls == []  # NOT executed
    assert _row(engine, row["id"])["status"] == "parked"
    parked = _pending_outward(engine)
    assert len(parked) == 1
    assert parked[0].payload["outbox_id"] == row["id"]
    assert "gated" in parked[0].payload["reason"]
    assert engine.lane_registry.has_active_lease(OUTWARD_LANE_ID) is False


# ---------------------------------------------------------------------------
# Auto + pre-flight pass + executor registered → executes, row 'sent'
# ---------------------------------------------------------------------------


def test_auto_pass_with_executor_executes_and_marks_sent(tmp_path, monkeypatch):
    executor = FakeExecutor(channel="x", ok=True, external_ref="post-99")
    engine = FakeEngine(tmp_path, executors=[executor])
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    row = _enqueue(engine)
    _patch_preflight(monkeypatch, PreflightResult(ok=True, passed=["all"]))

    lane = OutwardLane(engine, engine.lane_registry)
    actions = lane.dispatch_once()

    assert actions == [f"outward_sent:{row['id']}"]
    assert len(executor.calls) == 1
    assert executor.calls[0]["id"] == row["id"]
    sent = _row(engine, row["id"])
    assert sent["status"] == "sent"
    assert sent["external_ref"] == "post-99"
    # No approval card filed for a clean auto send.
    assert _pending_outward(engine) == []
    assert engine.lane_registry.has_active_lease(OUTWARD_LANE_ID) is False


def test_executor_failure_marks_failed_not_sent(tmp_path, monkeypatch):
    executor = FakeExecutor(channel="x", ok=False)
    engine = FakeEngine(tmp_path, executors=[executor])
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    row = _enqueue(engine)
    _patch_preflight(monkeypatch, PreflightResult(ok=True, passed=["all"]))

    actions = OutwardLane(engine, engine.lane_registry).dispatch_once()
    assert actions == [f"outward_failed:{row['id']}"]
    assert _row(engine, row["id"])["status"] == "failed"


# ---------------------------------------------------------------------------
# Auto + pre-flight HOLD → parks with holds, no execute
# ---------------------------------------------------------------------------


def test_auto_preflight_hold_parks_with_holds_no_execute(tmp_path, monkeypatch):
    executor = FakeExecutor(channel="x")
    engine = FakeEngine(tmp_path, executors=[executor])
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    row = _enqueue(engine)
    hold = GateResult(verdict="hold", findings=["fabricated timeline"], gate="fabrication")
    _patch_preflight(
        monkeypatch, PreflightResult(ok=False, holds=[hold], passed=["csuite_review", "deai"])
    )

    actions = OutwardLane(engine, engine.lane_registry).dispatch_once()

    assert actions == [f"outward_parked:{row['id']}"]
    assert executor.calls == []  # never executed under a HOLD
    assert _row(engine, row["id"])["status"] == "parked"
    parked = _pending_outward(engine)
    assert len(parked) == 1
    assert parked[0].payload["holds"] == ["[fabrication] fabricated timeline"]
    assert "HOLD" in parked[0].payload["reason"]


# ---------------------------------------------------------------------------
# Auto + no executor → parks "no executor"
# ---------------------------------------------------------------------------


def test_auto_no_executor_parks_no_executor(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path, executors=[])  # engine ships none
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    row = _enqueue(engine)
    _patch_preflight(monkeypatch, PreflightResult(ok=True, passed=["all"]))

    actions = OutwardLane(engine, engine.lane_registry).dispatch_once()

    assert actions == [f"outward_parked:{row['id']}"]
    assert _row(engine, row["id"])["status"] == "parked"
    parked = _pending_outward(engine)
    assert parked[0].payload["reason"] == "no executor"


def test_executor_channel_must_match(tmp_path, monkeypatch):
    # Executor for "telegram" cannot serve an "x" action → parks no executor.
    engine = FakeEngine(tmp_path, executors=[FakeExecutor(channel="telegram")])
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    row = _enqueue(engine, channel="x")
    _patch_preflight(monkeypatch, PreflightResult(ok=True, passed=["all"]))
    actions = OutwardLane(engine, engine.lane_registry).dispatch_once()
    assert actions == [f"outward_parked:{row['id']}"]
    assert _pending_outward(engine)[0].payload["reason"] == "no executor"


# ---------------------------------------------------------------------------
# Lease prevents double-send
# ---------------------------------------------------------------------------


def test_lease_prevents_double_send(tmp_path, monkeypatch):
    executor = FakeExecutor(channel="x")
    engine = FakeEngine(tmp_path, executors=[executor])
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    _enqueue(engine)
    _patch_preflight(monkeypatch, PreflightResult(ok=True, passed=["all"]))

    lane = OutwardLane(engine, engine.lane_registry)
    # Pre-acquire the outward lease (simulating a concurrent live tick) by
    # registering the lane then taking its lease.
    lane._ensure_lane()
    assert engine.lane_registry.acquire_lease(
        OUTWARD_LANE_ID, None, None, ttl_seconds=600
    ) is True

    actions = lane.dispatch_once()
    assert actions == [f"lane_busy:{OUTWARD_LANE_ID}"]
    assert executor.calls == []  # not executed while another holds the lease


# ---------------------------------------------------------------------------
# Suspend → no dispatch
# ---------------------------------------------------------------------------


def test_suspend_blocks_dispatch(tmp_path, monkeypatch):
    executor = FakeExecutor(channel="x")
    engine = FakeEngine(tmp_path, executors=[executor])
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    row = _enqueue(engine)
    engine.runtime.suspend()
    _patch_preflight_raises(monkeypatch, AssertionError("ran while suspended"))

    actions = OutwardLane(engine, engine.lane_registry).dispatch_once()
    assert actions == ["idle_suspended"]
    assert executor.calls == []
    assert _row(engine, row["id"])["status"] == "queued"  # untouched


# ---------------------------------------------------------------------------
# LLMUnavailable in pre-flight → parked + health event, NOT silent success
# ---------------------------------------------------------------------------


def test_llm_unavailable_in_preflight_parks_and_records_health(tmp_path, monkeypatch):
    executor = FakeExecutor(channel="x")
    engine = FakeEngine(tmp_path, executors=[executor])
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    row = _enqueue(engine)
    _patch_preflight_raises(monkeypatch, LLMUnavailable("all models exhausted"))

    actions = OutwardLane(engine, engine.lane_registry).dispatch_once()

    # Loud failure — NOT outward_sent, NOT a silent no-op.
    assert actions == [f"outward_failed:{row['id']}"]
    assert "outward_sent:" + row["id"] not in actions
    assert executor.calls == []  # never reached the executor

    # Parked for approval (never silently dropped).
    assert _row(engine, row["id"])["status"] == "parked"
    parked = _pending_outward(engine)
    assert len(parked) == 1
    assert "unavailable" in parked[0].payload["reason"]

    # Health event recorded (consistent with lane_dispatcher).
    kinds = [e["kind"] for e in engine.health_events.list()]
    assert "lane_timeout" in kinds
    # Lease released even though pre-flight raised.
    assert engine.lane_registry.has_active_lease(OUTWARD_LANE_ID) is False


# ---------------------------------------------------------------------------
# Hard floor: spend side-effect is always gated regardless of policy
# ---------------------------------------------------------------------------


def test_spend_side_effect_is_always_gated(tmp_path, monkeypatch):
    executor = FakeExecutor(channel="x")
    engine = FakeEngine(tmp_path, executors=[executor])
    # Even with the class flipped to auto, a spend side-effect parks.
    engine.outward_policies.set_mode("ai_voice_post", "auto")
    row = enqueue_outward(
        engine, channel="x", text="pay invoice",
        action_class="ai_voice_post", side_effect="spend",
    )
    _patch_preflight_raises(monkeypatch, AssertionError("preflight ran on spend"))

    actions = OutwardLane(engine, engine.lane_registry).dispatch_once()
    assert actions == [f"outward_parked:{row['id']}"]
    assert executor.calls == []
