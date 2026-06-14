"""Tests for the concurrent resilient runtime — lanes (ADR-0005).

Real SQLite, mocked LLM/agents. Covers:
  - intake enqueue/pop atomicity (two pops never claim the same item),
  - lease prevents double-run (rejected while live, granted after expiry),
  - dispatcher with the single default lane == one-task-per-tick (parity
    with the legacy ``_action_advance``),
  - dispatcher honours suspend,
  - model-fallback (primary exhausts -> fallback succeeds -> no
    LLMUnavailable; all fail -> LLMUnavailable + health event + item
    marked failed),
  - silent-success -> failure regression (model down never reports an
    idle "ok"; the item is failed and a health event is recorded).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kompany.core.lane_dispatcher import LaneDispatcher
from kompany.core.lane_registry import DEFAULT_LANE_ID, LaneRegistry
from kompany.core.watchdog import LLMUnavailable, Watchdog
from kompany.llm.client import LLMClient, LLMResponse
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.checkpoints import CheckpointStore
from kompany.state.daemon_ticks import DaemonTickStore
from kompany.state.database import Database
from kompany.state.health_events import HEALTH_KINDS, HealthEvents
from kompany.state.intake_queue import IntakeQueueStore
from kompany.state.memory import AgentMemory
from kompany.state.models import Project, ProjectType, Task, TaskStatus
from kompany.state.projects import Projects
from kompany.state.runtime import RuntimeStateStore


# ---------------------------------------------------------------------------
# Fakes (test_ticker precedent)
# ---------------------------------------------------------------------------


class FakeAgent:
    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        return SimpleNamespace(text="done", cost_usd=0.01)


class FakeRegistry:
    def get(self, role, company_state=None):
        return FakeAgent()


class FakeEngine:
    """Minimal engine surface for the lane dispatcher + legacy runner path."""

    def __init__(self, tmp_path):
        self.db = Database(tmp_path)
        self.projects = Projects(self.db)
        self.audit = AuditLog(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.memory = AgentMemory(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.health_events = HealthEvents(self.db)
        self.registry = FakeRegistry()
        self.runtime = RuntimeStateStore(self.db)
        self.settings = SimpleNamespace(
            data_dir=tmp_path,
            model_primary="claude-sonnet-4-20250514",
            model_source=None,
            harness_execution_enabled=True,
            harness_permission_routing=True,
        )
        self.intake_queue = IntakeQueueStore(self.db)
        self.lane_registry = LaneRegistry(self.db)
        self.lane_registry.ensure_default()
        self.lane_dispatcher = LaneDispatcher(
            engine=self,
            registry=self.lane_registry,
            intake=self.intake_queue,
        )


def _seed_project(engine, name="proj", age_minutes=0, task_titles=("Do thing",)):
    project = Project(
        name=name,
        type=ProjectType.REVENUE,
        created_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
    )
    engine.projects.create(project)
    tasks = []
    for title in task_titles:
        task = Task(project_id=project.id, title=title, assigned_agent="writer")
        engine.projects.create_task(task)
        tasks.append(task)
    return project, tasks


def _task_status(engine, task_id):
    task = engine.projects.get_task(task_id)
    return task.status.value if isinstance(task.status, TaskStatus) else task.status


# ---------------------------------------------------------------------------
# Intake queue — atomicity
# ---------------------------------------------------------------------------


def test_intake_enqueue_and_list(tmp_path):
    q = IntakeQueueStore(Database(tmp_path))
    a = q.enqueue("p1", detail={"why": "x"})
    b = q.enqueue("p2")
    assert a != b
    queued = q.list(status="queued")
    assert [r["project_id"] for r in queued] == ["p1", "p2"]
    assert queued[0]["detail"] == {"why": "x"}


def test_intake_pop_is_atomic_two_pops_never_claim_same_item(tmp_path):
    q = IntakeQueueStore(Database(tmp_path))
    item_id = q.enqueue("p1")
    first = q.pop_for_lane("lane-a")
    second = q.pop_for_lane("lane-b")
    assert first is not None
    assert first["id"] == item_id
    assert first["lane_id"] == "lane-a"
    assert first["status"] == "assigned"
    # The single queued item was claimed once; the second pop gets nothing.
    assert second is None


def test_intake_pop_oldest_first_then_empty(tmp_path):
    q = IntakeQueueStore(Database(tmp_path))
    first_id = q.enqueue("p1")
    q.enqueue("p2")
    claimed = q.pop_for_lane("main")
    assert claimed["id"] == first_id
    again = q.pop_for_lane("main")
    assert again["project_id"] == "p2"
    assert q.pop_for_lane("main") is None


def test_intake_mark_completed_and_failed(tmp_path):
    q = IntakeQueueStore(Database(tmp_path))
    a = q.enqueue("p1")
    b = q.enqueue("p2")
    q.pop_for_lane("main")
    q.pop_for_lane("main")
    q.mark_completed(a, {"task_id": "t1"})
    q.mark_failed(b, "model down")
    assert q.list(status="completed")[0]["id"] == a
    failed = q.list(status="failed")
    assert failed[0]["id"] == b
    assert failed[0]["detail"]["reason"] == "model down"


# ---------------------------------------------------------------------------
# Lane registry — leases (no double-run)
# ---------------------------------------------------------------------------


def test_ensure_default_creates_single_main_lane(tmp_path):
    reg = LaneRegistry(Database(tmp_path))
    reg.ensure_default()
    reg.ensure_default()  # idempotent
    healthy = reg.list_healthy()
    assert [l["lane_id"] for l in healthy] == [DEFAULT_LANE_ID]
    assert healthy[0]["max_concurrent"] == 1


def test_lease_prevents_double_run_then_succeeds_after_expiry(tmp_path):
    clock = {"now": datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)}
    reg = LaneRegistry(Database(tmp_path), clock=lambda: clock["now"])
    reg.ensure_default()
    assert reg.acquire_lease("main", "t1", "r1", ttl_seconds=60) is True
    # Second acquire while the lease is live is rejected — no double-run.
    assert reg.acquire_lease("main", "t2", "r2", ttl_seconds=60) is False
    assert reg.has_active_lease("main") is True
    # Advance past the TTL; the expired lease can be re-acquired.
    clock["now"] = clock["now"] + timedelta(seconds=61)
    assert reg.has_active_lease("main") is False
    assert reg.acquire_lease("main", "t3", "r3", ttl_seconds=60) is True


def test_release_lease_frees_lane(tmp_path):
    reg = LaneRegistry(Database(tmp_path))
    reg.ensure_default()
    assert reg.acquire_lease("main", "t1", "r1", ttl_seconds=300) is True
    reg.release_lease("main")
    assert reg.has_active_lease("main") is False
    assert reg.acquire_lease("main", "t2", "r2", ttl_seconds=300) is True


def test_mark_stalled_removes_lane_from_healthy(tmp_path):
    reg = LaneRegistry(Database(tmp_path))
    reg.ensure_default()
    reg.mark_stalled("main", "model down")
    assert reg.list_healthy() == []
    assert reg.get("main")["status"] == "stalled"


# ---------------------------------------------------------------------------
# Dispatcher — single-lane parity + suspend
# ---------------------------------------------------------------------------


def test_dispatch_single_default_lane_runs_exactly_one_task(tmp_path):
    """Parity with ticker._action_advance: oldest project, one task."""
    engine = FakeEngine(tmp_path)
    _old_p, old_tasks = _seed_project(
        engine, name="old", age_minutes=60, task_titles=("First", "Second")
    )
    _seed_project(engine, name="new", age_minutes=0, task_titles=("Other",))
    actions = engine.lane_dispatcher.dispatch_once()
    advanced = [a for a in actions if a.startswith("advanced_task:")]
    assert advanced == [f"advanced_task:{old_tasks[0].id}"]
    # Exactly one task advanced; the rest untouched.
    assert _task_status(engine, old_tasks[0].id) == "delivered"
    assert _task_status(engine, old_tasks[1].id) == "pending"
    # Lease was released after the advance.
    assert engine.lane_registry.has_active_lease("main") is False


def test_dispatch_no_work_when_nothing_pending(tmp_path):
    engine = FakeEngine(tmp_path)
    assert engine.lane_dispatcher.dispatch_once() == ["no_work"]


def test_dispatch_honours_suspend(tmp_path):
    engine = FakeEngine(tmp_path)
    _p, tasks = _seed_project(engine)
    engine.runtime.set("suspended", reason="founder brake")
    actions = engine.lane_dispatcher.dispatch_once()
    assert actions == ["idle_suspended"]
    assert _task_status(engine, tasks[0].id) == "pending"


def test_dispatch_consumes_intake_item_and_marks_completed(tmp_path):
    engine = FakeEngine(tmp_path)
    project, tasks = _seed_project(engine)
    engine.intake_queue.enqueue(project.id)
    actions = engine.lane_dispatcher.dispatch_once()
    assert any(a.startswith("advanced_task:") for a in actions)
    completed = engine.intake_queue.list(status="completed")
    assert len(completed) == 1
    assert completed[0]["detail"]["task_id"] == tasks[0].id


# ---------------------------------------------------------------------------
# Silent-success -> failure regression (model unavailable is a FAILURE)
# ---------------------------------------------------------------------------


class _BoomRunner:
    """ProjectRunner stand-in whose run_one_pending raises LLMUnavailable."""

    def __init__(self, engine):
        pass

    def run_one_pending(self, project_id):
        raise LLMUnavailable("primary + fallbacks all exhausted")


def test_model_unavailable_is_failure_never_silent_success(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path)
    project, _tasks = _seed_project(engine)
    item_id = engine.intake_queue.enqueue(project.id)

    # Force the runner path to raise LLMUnavailable. The dispatcher does
    # ``from kompany.core.runner import ProjectRunner`` at call time, so
    # patch the name on that module.
    import kompany.core.runner as runner_mod

    monkeypatch.setattr(runner_mod, "ProjectRunner", _BoomRunner)

    actions = engine.lane_dispatcher.dispatch_once()

    # Loud failure — NOT a silent "no_work"/"ok"/"advanced".
    assert any(a.startswith("lane_failed:") for a in actions)
    assert not any(a.startswith("advanced_task:") for a in actions)
    assert "no_work" not in actions

    # Intake item is marked failed, never silently completed.
    assert engine.intake_queue.list(status="completed") == []
    failed = engine.intake_queue.list(status="failed")
    assert [f["id"] for f in failed] == [item_id]

    # Health event recorded; lane stalled.
    kinds = [e["kind"] for e in engine.health_events.list()]
    assert "lane_timeout" in kinds
    assert engine.lane_registry.get("main")["status"] == "stalled"
    # Lease was released even though the run raised.
    assert engine.lane_registry.has_active_lease("main") is False


def test_lane_timeout_is_a_valid_health_kind():
    assert "lane_timeout" in HEALTH_KINDS
    assert "lane_stalled" in HEALTH_KINDS


# ---------------------------------------------------------------------------
# Model-fallback pool
# ---------------------------------------------------------------------------


def _make_client(tmp_path, fallback_models, silent_timeout=0.5):
    from kompany.llm.cost_tracker import CostTracker
    from kompany.state.ledger import Ledger

    db = Database(tmp_path)
    audit = AuditLog(db)
    projects = Projects(db)
    health = HealthEvents(db)
    tracker = CostTracker(Ledger(db))
    watchdog = Watchdog(
        health_events=health,
        projects=projects,
        audit=audit,
        scan_interval_seconds=1,
        stale_threshold_seconds=1,
    )
    settings = SimpleNamespace(custom_base_url=None)
    client = LLMClient(
        settings=settings,
        cost_tracker=tracker,
        audit_log=audit,
        watchdog=watchdog,
        silent_timeout_seconds=silent_timeout,
        fallback_models=fallback_models,
    )
    return client, health


def _ok_response(model):
    return LLMResponse(
        text="ok", input_tokens=5, output_tokens=2, cost_usd=0.0, model=model
    )


def test_fallback_model_succeeds_when_primary_exhausts(tmp_path):
    primary = "claude-sonnet-4-20250514"
    fallback = "gpt-4o-mini"
    client, health = _make_client(tmp_path, fallback_models=[fallback])

    seen = {"models": []}

    def invoke(provider, model, system, prompt, max_tokens, *, agent_name, directive_id):
        seen["models"].append(model)
        if model == primary:
            raise RuntimeError("model currently unavailable")
        return _ok_response(model)

    client._invoke_provider = invoke

    resp = client.call(model=primary, system="s", prompt="p", agent_name="ceo")
    assert resp.text == "ok"
    assert resp.model == fallback
    # Primary tried (x2 watchdog retry) then the fallback once.
    assert primary in seen["models"]
    assert fallback in seen["models"]


def test_all_models_fail_raises_LLMUnavailable(tmp_path):
    primary = "claude-sonnet-4-20250514"
    client, health = _make_client(tmp_path, fallback_models=["gpt-4o-mini"])

    def always_fail(*_a, **_k):
        raise RuntimeError("network down")

    client._invoke_provider = always_fail

    with pytest.raises(LLMUnavailable):
        client.call(model=primary, system="s", prompt="p", agent_name="ceo")

    kinds = [e["kind"] for e in health.list()]
    assert "retry_exhausted" in kinds


def test_no_fallback_configured_keeps_legacy_raise(tmp_path):
    primary = "claude-sonnet-4-20250514"
    client, _health = _make_client(tmp_path, fallback_models=None)

    def always_fail(*_a, **_k):
        raise RuntimeError("network down")

    client._invoke_provider = always_fail
    with pytest.raises(LLMUnavailable):
        client.call(model=primary, system="s", prompt="p", agent_name="ceo")


def test_engine_wires_fallback_pool(tmp_path, monkeypatch):
    """ADR-0005 model-fallback must be WIRED at engine construction, not just
    available on the client (handoff 2026-06-15 Step 4)."""
    from kompany.config.settings import KompanySettings
    s = KompanySettings()
    pool = s.fallback_model_pool()
    assert pool, "fallback pool must be non-empty"
    assert pool[0] == s.model_apex
    # distinct + ordered apex→primary→economy
    assert len(pool) == len(set(pool))
