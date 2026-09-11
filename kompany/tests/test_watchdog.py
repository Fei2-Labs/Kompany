"""Tests for the resilience watchdog and its LLM-client integration."""

from __future__ import annotations

import asyncio
import time

import pytest

from kompany.core.run_context import run_scope
from kompany.core.watchdog import LLMUnavailable, Watchdog
from kompany.llm.client import LLMClient, LLMResponse
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.health_events import HealthEvents
from kompany.state.ledger import Ledger
from kompany.state.models import (
    LedgerCategory,
    Project,
    ProjectType,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects


# ----------------------------------------------------------------------
# Test scaffolding
# ----------------------------------------------------------------------


class FakeSettings:
    anthropic_api_key = "k"
    openai_api_key = ""
    gemini_api_key = ""
    glm_api_key = ""
    kimi_api_key = ""
    custom_api_key = ""
    custom_base_url = ""

    def get_api_key_for_provider(self, name):
        return getattr(self, f"{name}_api_key", "")


def _make_world(tmp_path, silent_timeout=0.2, stale_seconds=1):
    db = Database(tmp_path)
    audit = AuditLog(db)
    projects = Projects(db)
    health = HealthEvents(db)
    ledger = Ledger(db)
    from kompany.llm.cost_tracker import CostTracker
    tracker = CostTracker(ledger)
    watchdog = Watchdog(
        health_events=health,
        projects=projects,
        audit=audit,
        scan_interval_seconds=1,
        stale_threshold_seconds=stale_seconds,
    )
    client = LLMClient(
        settings=FakeSettings(),
        cost_tracker=tracker,
        audit_log=audit,
        watchdog=watchdog,
        silent_timeout_seconds=silent_timeout,
    )
    return {
        "db": db,
        "audit": audit,
        "projects": projects,
        "health": health,
        "ledger": ledger,
        "watchdog": watchdog,
        "client": client,
    }


def _llm_ok(*_args, **_kwargs):
    return LLMResponse(
        text="ok",
        input_tokens=5,
        output_tokens=2,
        cost_usd=0.0,
        model="claude-sonnet-4-20250514",
    )


# ----------------------------------------------------------------------
# Watchdog direct API
# ----------------------------------------------------------------------


def test_record_silent_run_writes_event(tmp_path):
    w = _make_world(tmp_path)
    event = w["watchdog"].record_silent_run(
        task_id="t1", project_id="p1", detail={"why": "timeout"}
    )
    assert event["kind"] == "silent_run"
    assert event["status"] == "open"


def test_record_recovered_closes_open_silent_runs(tmp_path):
    w = _make_world(tmp_path)
    with run_scope():
        silent = w["watchdog"].record_silent_run(task_id="t1", project_id="p1")
        w["watchdog"].record_recovered(task_id="t1", project_id="p1")
    refreshed = w["health"].get(silent["id"])
    assert refreshed["status"] == "resolved"
    assert refreshed["resolved_by"] == "system"


def test_resolve_dispatches_to_health_events(tmp_path):
    w = _make_world(tmp_path)
    event = w["watchdog"].record_silent_run(task_id="t1")
    updated = w["watchdog"].resolve(event["id"], action="continue")
    assert updated["status"] == "resolved"


def test_list_open_returns_only_open(tmp_path):
    w = _make_world(tmp_path)
    a = w["watchdog"].record_silent_run(task_id="ta")
    b = w["watchdog"].record_silent_run(task_id="tb")
    w["watchdog"].resolve(a["id"], action="dismiss")
    open_events = w["watchdog"].list_open()
    assert [e["id"] for e in open_events] == [b["id"]]


# ----------------------------------------------------------------------
# Scanner — proactive stranded detection
# ----------------------------------------------------------------------


def _seed_active_task(world, task_id="t1", project_id="p1", age_seconds=10):
    projects = world["projects"]
    projects.create(Project(
        id=project_id,
        name="P",
        type=ProjectType.OPERATIONAL,
        assigned_agents=["coo"],
    ))
    projects.create_task(Task(
        id=task_id,
        project_id=project_id,
        title="Do stuff",
        assigned_agent="coo",
        status=TaskStatus.ACTIVE,
    ))
    # Backdate updated_at so the scanner sees it as stale.
    world["db"].execute(
        "UPDATE tasks SET updated_at = datetime('now', ?) WHERE id = ?",
        (f"-{age_seconds} seconds", task_id),
    )
    world["db"].commit()


def test_scan_once_flips_stale_task_and_writes_event(tmp_path):
    w = _make_world(tmp_path, stale_seconds=5)
    _seed_active_task(w, task_id="t1", project_id="p1", age_seconds=10)
    events = w["watchdog"].scan_once()
    assert len(events) == 1
    assert events[0]["kind"] == "stranded_in_progress"
    assert events[0]["task_id"] == "t1"
    # Task row is flipped.
    row = w["db"].execute(
        "SELECT status FROM tasks WHERE id = ?", ("t1",)
    ).fetchone()
    assert row["status"] == "stranded_in_progress"


def test_scan_once_skips_fresh_task(tmp_path):
    w = _make_world(tmp_path, stale_seconds=600)
    _seed_active_task(w, task_id="t1", project_id="p1", age_seconds=1)
    events = w["watchdog"].scan_once()
    assert events == []


def test_scan_skips_snoozed_event(tmp_path):
    w = _make_world(tmp_path, stale_seconds=5)
    _seed_active_task(w, task_id="t1", project_id="p1", age_seconds=10)
    first = w["watchdog"].scan_once()
    assert len(first) == 1
    # Player snoozes the stranded event.
    w["watchdog"].resolve(first[0]["id"], action="snooze", snooze_minutes=60)
    # Reset the task back to active and stale so the scanner has work,
    # but the active snooze for this task_id should suppress the warning.
    w["db"].execute(
        "UPDATE tasks SET status = 'active', updated_at = datetime('now', '-10 seconds') WHERE id = ?",
        ("t1",),
    )
    w["db"].commit()
    second = w["watchdog"].scan_once()
    assert second == []


def test_scanner_start_and_stop_are_safe(tmp_path):
    w = _make_world(tmp_path, stale_seconds=600)

    async def main():
        w["watchdog"].start()
        # Let the loop tick once.
        await asyncio.sleep(0.05)
        await w["watchdog"].stop()
        # Idempotent.
        await w["watchdog"].stop()

    asyncio.run(main())


# ----------------------------------------------------------------------
# LLM client integration
# ----------------------------------------------------------------------


def test_llm_call_fast_success_no_health_event(tmp_path):
    w = _make_world(tmp_path)
    w["client"]._call_anthropic = _llm_ok
    with run_scope():
        resp = w["client"].call(
            model="claude-sonnet-4-20250514",
            system="s",
            prompt="p",
            agent_name="ceo",
        )
    assert resp.text == "ok"
    assert w["health"].list() == []


def test_llm_call_429_retry_succeeds(tmp_path):
    """First call raises 429; retry succeeds. Two ledger entries; recovered event."""
    w = _make_world(tmp_path)
    state = {"calls": 0}

    def flaky(*_args, **_kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            err = RuntimeError("rate limit exceeded")
            err.status_code = 429
            raise err
        return _llm_ok()

    w["client"]._call_anthropic = flaky
    with run_scope():
        resp = w["client"].call(
            model="claude-sonnet-4-20250514",
            system="s",
            prompt="p",
            agent_name="ceo",
            task_id="t1",
            project_id="p1",
        )
    assert resp.text == "ok"
    assert state["calls"] == 2

    kinds = [e["kind"] for e in w["health"].list()]
    assert "silent_run" in kinds
    assert "recovered" in kinds
    # Ledger has at least one AI cost entry (success path); we don't double-charge
    # on fast 429 failure because the provider call raised before token counting.
    ai_rows = w["db"].execute(
        "SELECT * FROM ledger WHERE category = 'ai_cost'"
    ).fetchall()
    assert len(ai_rows) >= 1


def test_llm_call_two_failures_raises_LLMUnavailable(tmp_path):
    """Both attempts fail -> retry_exhausted event + LLMUnavailable."""
    w = _make_world(tmp_path)

    def always_fail(*_args, **_kwargs):
        raise RuntimeError("network down")

    w["client"]._call_anthropic = always_fail
    with run_scope():
        with pytest.raises(LLMUnavailable):
            w["client"].call(
                model="claude-sonnet-4-20250514",
                system="s",
                prompt="p",
                agent_name="ceo",
                task_id="t1",
                project_id="p1",
            )

    kinds = [e["kind"] for e in w["health"].list()]
    assert "retry_exhausted" in kinds
    assert "silent_run" in kinds


def test_llm_call_hangs_then_succeeds_writes_silent_and_recovered(tmp_path):
    """Hang >timeout, eventually returns -> silent_run + recovered, 1 ledger entry."""
    w = _make_world(tmp_path, silent_timeout=0.1)

    def slow_then_ok(*_args, **_kwargs):
        time.sleep(0.25)
        return _llm_ok()

    w["client"]._call_anthropic = slow_then_ok
    with run_scope():
        resp = w["client"].call(
            model="claude-sonnet-4-20250514",
            system="s",
            prompt="p",
            agent_name="ceo",
            task_id="t1",
            project_id="p1",
        )
    assert resp.text == "ok"
    kinds = [e["kind"] for e in w["health"].list()]
    assert "silent_run" in kinds
    assert "recovered" in kinds
    # Only one successful provider call -> one ledger ai_cost row.
    ai_rows = w["db"].execute(
        "SELECT * FROM ledger WHERE category = 'ai_cost'"
    ).fetchall()
    assert len(ai_rows) == 1


def test_llm_call_without_watchdog_keeps_legacy_behavior(tmp_path):
    """When no watchdog is wired, the client behaves as before."""
    db = Database(tmp_path)
    from kompany.llm.cost_tracker import CostTracker
    ledger = Ledger(db)
    client = LLMClient(
        settings=FakeSettings(),
        cost_tracker=CostTracker(ledger),
    )
    client._call_anthropic = _llm_ok
    resp = client.call("claude-sonnet-4-20250514", "s", "p")
    assert resp.text == "ok"


# ----------------------------------------------------------------------
# Startup reconciliation (Stage A deployment plan: session-persistence)
# ----------------------------------------------------------------------


def _make_world_with_agent_status(tmp_path, stale_seconds=600):
    from kompany.state.agent_status import AgentStatusStore

    db = Database(tmp_path)
    audit = AuditLog(db)
    projects = Projects(db)
    health = HealthEvents(db)
    agent_status = AgentStatusStore(db)
    watchdog = Watchdog(
        health_events=health,
        projects=projects,
        audit=audit,
        scan_interval_seconds=1,
        stale_threshold_seconds=stale_seconds,
        agent_status=agent_status,
    )
    return {
        "db": db,
        "audit": audit,
        "projects": projects,
        "health": health,
        "agent_status": agent_status,
        "watchdog": watchdog,
    }


def _seed_fresh_active_task(world, task_id="t1", project_id="p1"):
    """A task that was updated just now — NOT stale by any threshold.

    ``scan_once`` would never touch this (its staleness window hasn't
    elapsed), but ``reconcile_on_startup`` must catch it anyway: the
    process is only now booting, so nothing could possibly still be
    running it.
    """
    projects = world["projects"]
    projects.create(Project(
        id=project_id, name="P", type=ProjectType.OPERATIONAL,
        assigned_agents=["coo"],
    ))
    projects.create_task(Task(
        id=task_id, project_id=project_id, title="Do stuff",
        assigned_agent="coo", status=TaskStatus.ACTIVE,
    ))


def test_reconcile_on_startup_strands_fresh_active_task(tmp_path):
    w = _make_world_with_agent_status(tmp_path, stale_seconds=600)
    _seed_fresh_active_task(w)

    # Confirm scan_once alone would NOT catch this (proves the two
    # mechanisms are complementary, not redundant): the task was just
    # created, so it isn't past the staleness threshold yet.
    assert w["watchdog"].scan_once() == []

    result = w["watchdog"].reconcile_on_startup()

    # Orphaned at boot → back in the queue (the runtime's problem, not the
    # founder's); the stranded event is recorded but closed immediately.
    assert len(result["stranded_tasks"]) == 1
    assert result["stranded_tasks"][0]["task_id"] == "t1"
    assert result["stranded_tasks"][0]["detail"]["requeued"] is True
    row = w["db"].execute(
        "SELECT status, retry_count, block_reason FROM tasks WHERE id = ?", ("t1",)
    ).fetchone()
    assert row["status"] == "pending" and row["retry_count"] == 1
    assert "auto-retry 1/2" in row["block_reason"]
    assert w["watchdog"].list_open() == []


def test_reconcile_on_startup_skips_snoozed_task(tmp_path):
    w = _make_world_with_agent_status(tmp_path)
    _seed_fresh_active_task(w)
    event = w["health"].record(
        kind="stranded_in_progress", task_id="t1", project_id="p1", detail={},
    )
    w["health"].resolve(event["id"], action="snooze", snooze_minutes=60)

    result = w["watchdog"].reconcile_on_startup()

    assert result["stranded_tasks"] == []
    row = w["db"].execute(
        "SELECT status FROM tasks WHERE id = ?", ("t1",)
    ).fetchone()
    assert row["status"] == TaskStatus.ACTIVE.value


def test_reconcile_on_startup_resets_stale_agent_status(tmp_path):
    w = _make_world_with_agent_status(tmp_path)
    w["agent_status"].set("ceo", "working", "Some task", project_id="p1")
    w["agent_status"].set("builder", "thinking", None)
    w["agent_status"].set("cmo", "idle", None)  # already idle — untouched

    result = w["watchdog"].reconcile_on_startup()

    reset_roles = {row["agent_role"] for row in result["reset_agents"]}
    assert reset_roles == {"ceo", "builder"}

    ceo = w["agent_status"].get("ceo")
    assert ceo["status"] == "idle"
    assert ceo["current_task"] is None
    builder = w["agent_status"].get("builder")
    assert builder["status"] == "idle"

    types = [e["event_type"] for e in w["audit"].recent(limit=10)]
    assert "agent_status.startup_reset" in types


def test_reconcile_on_startup_noop_when_nothing_to_reconcile(tmp_path):
    w = _make_world_with_agent_status(tmp_path)
    result = w["watchdog"].reconcile_on_startup()
    assert result == {"stranded_tasks": [], "reset_agents": []}


def test_reconcile_on_startup_without_agent_status_store_is_safe(tmp_path):
    """Legacy construction path (no ``agent_status=``) must not crash —
    it simply skips the agent-status half of reconciliation."""
    db = Database(tmp_path)
    audit = AuditLog(db)
    projects = Projects(db)
    health = HealthEvents(db)
    watchdog = Watchdog(
        health_events=health, projects=projects, audit=audit,
    )
    result = watchdog.reconcile_on_startup()
    assert result == {"stranded_tasks": [], "reset_agents": []}


def test_list_active_tasks_ignores_updated_at(tmp_path):
    projects = Projects(Database(tmp_path))
    projects.create(Project(
        id="p1", name="P", type=ProjectType.OPERATIONAL, assigned_agents=["coo"],
    ))
    projects.create_task(Task(
        id="t1", project_id="p1", title="Do stuff",
        assigned_agent="coo", status=TaskStatus.ACTIVE,
    ))
    projects.create_task(Task(
        id="t2", project_id="p1", title="Done already",
        assigned_agent="coo", status=TaskStatus.COMPLETED,
    ))
    active = projects.list_active_tasks()
    assert [t.id for t in active] == ["t1"]


# ----------------------------------------------------------------------
# Stranded-task recovery: requeue under budget, block with reason after
# ----------------------------------------------------------------------


def _strand_and_age(w, task_id, age_seconds=10):
    """Run the first scanner pass (active → stranded) and age the row."""
    first = w["watchdog"].scan_once()
    assert [e["kind"] for e in first] == ["stranded_in_progress"]
    w["db"].execute(
        "UPDATE tasks SET updated_at = datetime('now', ?) WHERE id = ?",
        (f"-{age_seconds} seconds", task_id),
    )
    w["db"].commit()


def test_scan_requeues_stranded_task_then_exhausts_into_blocked(tmp_path):
    w = _make_world(tmp_path, stale_seconds=5)
    _seed_active_task(w, task_id="t1", project_id="p1", age_seconds=10)
    wd = w["watchdog"]
    assert wd.max_stranded_retries == 2

    # pass 1: stale active → stranded (transient, gives a slow run time to finish)
    _strand_and_age(w, "t1")
    # pass 2: still stranded a threshold later → requeued (retry 1/2)
    second = wd.scan_once()
    assert len(second) == 1 and second[0]["detail"]["requeued"] is True
    row = w["db"].execute("SELECT status, retry_count FROM tasks WHERE id = 't1'").fetchone()
    assert (row["status"], row["retry_count"]) == ("pending", 1)
    assert wd.list_open() == []  # self-healed: no open alarm

    # the run dies again twice → retry 2/2, then exhausted
    for expected_retry in (2,):
        w["db"].execute("UPDATE tasks SET status='active', updated_at=datetime('now','-10 seconds') WHERE id='t1'"); w["db"].commit()
        _strand_and_age(w, "t1")
        ev = wd.scan_once()
        assert ev[0]["detail"]["retry"] == expected_retry
    w["db"].execute("UPDATE tasks SET status='active', updated_at=datetime('now','-10 seconds') WHERE id='t1'"); w["db"].commit()
    _strand_and_age(w, "t1")
    final = wd.scan_once()
    assert [e["kind"] for e in final] == ["retry_exhausted"]
    task = w["projects"].get_task("t1")
    assert task.status == TaskStatus.BLOCKED and task.retry_count == 2
    assert task.block_reason.startswith("retry_exhausted") and "needs your decision" in task.block_reason
    assert [e["kind"] for e in wd.list_open()] == ["retry_exhausted"]  # this one IS a founder decision


def test_reconcile_recovers_legacy_blocked_rows_without_reason(tmp_path):
    """Rows an old watchdog left ``blocked`` with no outcome/reason get one recovery."""
    w = _make_world_with_agent_status(tmp_path, stale_seconds=600)
    _seed_fresh_active_task(w)
    w["db"].execute("UPDATE tasks SET status='blocked', result=NULL WHERE id='t1'"); w["db"].commit()
    result = w["watchdog"].reconcile_on_startup()
    assert [e["task_id"] for e in result["stranded_tasks"]] == ["t1"]
    assert w["projects"].get_task("t1").status == TaskStatus.PENDING
    # a genuinely blocked task (runner outcome with founder_action) is left alone
    w["db"].execute("UPDATE tasks SET status='blocked', result='{\"founder_action\": \"connect Stripe\"}' WHERE id='t1'"); w["db"].commit()
    assert w["watchdog"].reconcile_on_startup()["stranded_tasks"] == []


def test_row_to_task_coerces_transient_status_to_blocked_with_reason(tmp_path):
    w = _make_world(tmp_path)
    _seed_active_task(w, task_id="t1", project_id="p1")
    w["db"].execute("UPDATE tasks SET status='stranded_in_progress' WHERE id='t1'"); w["db"].commit()
    t = w["projects"].get_task("t1")
    assert t.status == TaskStatus.BLOCKED and t.block_reason == "stranded (stranded_in_progress)"
