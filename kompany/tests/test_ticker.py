"""Tests for the daemon tick loop's Ticker (06-12-daemon-tick-loop PR1).

No real LLM, no subprocess: a FakeEngine (test_harness_execution
precedent) backs the real stores with a FakeRegistry agent, so the
advance step exercises the genuine ``ProjectRunner.run_one_pending``
slice on the legacy single-call path (``model_source=None``).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from kompany.core.harness_execution import (
    ACTION_BUDGET_INCREASE,
    ACTION_ENVELOPE_TOPUP,
)
from kompany.core.ticker import TICK_HISTORY_KEEP, Ticker
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.checkpoints import CheckpointStore
from kompany.state.daemon_ticks import DaemonTickStore
from kompany.state.database import Database
from kompany.state.health_events import HealthEvents
from kompany.state.memory import AgentMemory
from kompany.state.models import ApprovalRequest, Project, ProjectType, Task, TaskStatus
from kompany.state.projects import Projects


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeAgent:
    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        return SimpleNamespace(text="done", cost_usd=0.01)


class FakeRegistry:
    def get(self, role, company_state=None):
        return FakeAgent()


class FakeRuntime:
    def __init__(self, state="running"):
        self.state = state

    def get(self):
        return {"state": self.state, "reason": None, "since": None}


class FakeHub:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, payload=None):
        self.events.append((event_type, dict(payload or {})))


class FakeEngine:
    """Minimal engine surface for Ticker + legacy run_one_pending path."""

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
        self.runtime = FakeRuntime()
        self.settings = SimpleNamespace(
            data_dir=tmp_path,
            model_primary="claude-sonnet-4-20250514",
            model_source=None,
            harness_execution_enabled=True,
            harness_permission_routing=True,
        )
        self.heartbeat_calls = 0
        self.heartbeat_error: Exception | None = None

    def heartbeat_once(self, dispatch=False, adapter="dry-run"):
        self.heartbeat_calls += 1
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return {"notifications": []}


def _make(tmp_path, **ticker_kwargs):
    engine = FakeEngine(tmp_path)
    ticks = DaemonTickStore(engine.db)
    hub = FakeHub()
    ticker = Ticker(
        engine=engine,
        ticks=ticks,
        tick_interval_seconds=1,
        hub=hub,
        **ticker_kwargs,
    )
    return engine, ticks, hub, ticker


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


def _approval(engine, action_type, project_id):
    engine.approvals.create(ApprovalRequest(
        action_type=action_type,
        summary="gate",
        project_id=project_id,
        requested_by="writer",
        severity="high",
    ))


# ---------------------------------------------------------------------------
# tick_once — runtime gate + heartbeat
# ---------------------------------------------------------------------------


def test_suspended_runtime_records_idle_tick_and_does_nothing(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    engine.runtime.state = "suspended"
    _seed_project(engine)
    row = ticker.tick_once()
    assert row["outcome"] == "idle_suspended"
    assert row["actions"] == []
    assert engine.heartbeat_calls == 0
    # The pending task was not touched.
    tasks = engine.projects.list_tasks(engine.projects.list_active()[0].id)
    assert all(_task_status(engine, t.id) == "pending" for t in tasks)


def test_heartbeat_called_once_per_tick(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    row = ticker.tick_once()
    assert engine.heartbeat_calls == 1
    assert "heartbeat" in row["actions"]
    ticker.tick_once()
    assert engine.heartbeat_calls == 2


def test_heartbeat_exception_records_error_and_tick_continues(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    engine.heartbeat_error = RuntimeError("boom")
    _seed_project(engine)
    row = ticker.tick_once()
    assert row["outcome"] == "error"
    assert "heartbeat:error" in row["actions"]
    assert row["detail"]["errors"]["heartbeat"] == "boom"
    # Advance still ran despite the heartbeat failure.
    assert any(a.startswith("advanced_task:") for a in row["actions"])


# ---------------------------------------------------------------------------
# tick_once — advance work (one task per tick)
# ---------------------------------------------------------------------------


def test_advance_picks_oldest_project_and_runs_exactly_one_task(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    old_project, old_tasks = _seed_project(
        engine, name="old", age_minutes=60, task_titles=("First", "Second")
    )
    _seed_project(engine, name="new", age_minutes=0, task_titles=("Other",))
    row = ticker.tick_once()
    advanced = [a for a in row["actions"] if a.startswith("advanced_task:")]
    assert advanced == [f"advanced_task:{old_tasks[0].id}"]
    # Exactly one task left pending state; the rest are untouched.
    assert _task_status(engine, old_tasks[0].id) == "delivered"
    assert _task_status(engine, old_tasks[1].id) == "pending"


def test_advance_skips_project_with_pending_topup_approval(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    old_project, old_tasks = _seed_project(engine, name="old", age_minutes=60)
    new_project, new_tasks = _seed_project(engine, name="new", age_minutes=0)
    _approval(engine, ACTION_ENVELOPE_TOPUP, old_project.id)
    row = ticker.tick_once()
    assert f"skipped_pending_approval:{old_project.id}" in row["actions"]
    assert f"advanced_task:{new_tasks[0].id}" in row["actions"]
    assert _task_status(engine, old_tasks[0].id) == "pending"


def test_advance_skips_project_with_pending_budget_increase(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    project, tasks = _seed_project(engine)
    _approval(engine, ACTION_BUDGET_INCREASE, project.id)
    row = ticker.tick_once()
    assert row["actions"].count(f"skipped_pending_approval:{project.id}") == 1
    assert not any(a.startswith("advanced_task:") for a in row["actions"])
    assert _task_status(engine, tasks[0].id) == "pending"


def test_no_work_when_nothing_pending(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    row = ticker.tick_once()
    assert "no_work" in row["actions"]


def test_auto_execute_false_never_advances(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path, auto_execute=False)
    project, tasks = _seed_project(engine)
    row = ticker.tick_once()
    assert not any(a.startswith("advanced_task:") for a in row["actions"])
    assert "no_work" not in row["actions"]
    assert _task_status(engine, tasks[0].id) == "pending"


# ---------------------------------------------------------------------------
# tick_once — recording, housekeeping, SSE
# ---------------------------------------------------------------------------


def test_tick_row_recorded_with_actions(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    ticker.tick_once()
    rows = ticks.recent(limit=1)
    assert len(rows) == 1
    assert "heartbeat" in rows[0]["actions"]
    assert rows[0]["outcome"] == "ok"


def test_housekeeping_prunes_tick_history_to_500(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    for i in range(TICK_HISTORY_KEEP + 5):
        ticks.record(
            started_at=f"old-{i}", duration_ms=0, actions=[], outcome="ok"
        )
    row = ticker.tick_once()
    assert "pruned_ticks:5" in row["actions"]
    # 500 kept by the prune + the freshly recorded tick row.
    assert ticks.count() == TICK_HISTORY_KEEP + 1


def test_sse_event_published_with_activity_kind_tick(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    row = ticker.tick_once()
    assert len(hub.events) == 1
    event_type, payload = hub.events[0]
    assert event_type == "daemon.tick"
    assert payload["activity_kind"] == "tick"
    assert payload["outcome"] == "ok"
    assert payload["actions"] == row["actions"]
    assert isinstance(payload["duration_ms"], int)


def test_last_tick_at_and_tick_count_update(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    assert ticker.last_tick_at is None
    assert ticker.tick_count == 0
    ticker.tick_once()
    first = ticker.last_tick_at
    assert first is not None
    ticker.tick_once()
    assert ticker.tick_count == 2
    assert ticker.last_tick_at >= first


# ---------------------------------------------------------------------------
# Lifecycle — start / stop / loop resilience
# ---------------------------------------------------------------------------


def test_start_outside_running_loop_defers(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    ticker.start()
    assert not ticker.running


async def test_start_is_idempotent_and_stop_cancels_cleanly(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    ticker.start()
    first_task = ticker._task
    ticker.start()
    assert ticker._task is first_task
    assert ticker.running
    await ticker.stop()
    assert not ticker.running
    # Idempotent stop.
    await ticker.stop()


async def test_loop_survives_a_tick_that_raises(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    calls = {"n": 0}

    def flaky_tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick exploded")
        return {}

    ticker.tick_once = flaky_tick

    fires = {"n": 0}

    async def sleeper(_interval):
        fires["n"] += 1
        if fires["n"] > 3:
            raise asyncio.CancelledError

    ticker._sleeper = sleeper
    with contextlib.suppress(asyncio.CancelledError):
        await ticker._loop()
    # The first tick raised but the loop kept going for two more.
    assert calls["n"] == 3
    # The failed tick was still recorded with outcome="error".
    assert any(r["outcome"] == "error" for r in ticks.recent())


async def test_loop_ticks_after_each_sleep(tmp_path):
    engine, ticks, hub, ticker = _make(tmp_path)
    fires = {"n": 0}

    async def sleeper(_interval):
        fires["n"] += 1
        if fires["n"] > 2:
            raise asyncio.CancelledError

    ticker._sleeper = sleeper
    with contextlib.suppress(asyncio.CancelledError):
        await ticker._loop()
    assert ticker.tick_count == 2
    assert engine.heartbeat_calls == 2


def test_run_one_pending_decomposes_fresh_project(tmp_path):
    """Ticker slice must start NEW projects, not only continue old ones.

    Live-verification gap (06-12): a freshly created week_plan project has
    zero tasks until something decomposes the plan; run_one_pending now
    decomposes-if-empty with the same no-duplicate guard as run().
    """
    from kompany.core.runner import ProjectRunner

    engine, _, _, _ = _make(tmp_path)
    project = Project(
        name="fresh",
        type=ProjectType.OPERATIONAL,
        plan={
            "week_plan": ["Do the one thing"],
            "proposer_role": "builder",
            "source": "team_proposal_first_week_verify",
        },
    )
    engine.projects.create(project)
    runner = ProjectRunner(engine)
    task_id = runner.run_one_pending(project.id)
    assert task_id is not None
    tasks = engine.projects.list_tasks(project.id)
    assert len(tasks) == 1
    # Second slice must not duplicate the task set.
    runner.run_one_pending(project.id)
    assert len(engine.projects.list_tasks(project.id)) == 1


def test_tick_advances_project_with_no_tasks_yet(tmp_path):
    """Eligibility must include zero-task projects (live finding #2: the
    advance filter skipped them, so the decompose-if-empty slice never
    ran and fresh projects sat invisible to the daemon forever)."""
    engine, ticks, hub, ticker = _make(tmp_path)
    project = Project(
        name="fresh-empty",
        type=ProjectType.OPERATIONAL,
        plan={
            "week_plan": ["Do the one thing"],
            "proposer_role": "builder",
            "source": "team_proposal_first_week_verify",
        },
    )
    engine.projects.create(project)
    ticker.tick_once()
    recorded = ticks.recent(1)[0]
    assert any(a.startswith("advanced_task:") for a in recorded["actions"])
    assert len(engine.projects.list_tasks(project.id)) == 1
