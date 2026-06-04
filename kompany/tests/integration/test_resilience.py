"""End-to-end: resilience watchdog wiring inside the engine + episode payload.

Covers PRD critical test cases:

1. LLM 429 then success -> silent_run + recovered + ledger >= 1 ai_cost.
2. LLM network error twice -> retry_exhausted + LLMUnavailable.
3. LLM hang then slow success -> silent_run + recovered, single ai_cost.
4. Seeded stale ``active`` task -> scanner flips to ``stranded_in_progress``.
5. Snoozed event suppresses re-warning within the snooze window.
6. Episode materializer populates ``health_events`` slot for a project.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from kompany.core.run_context import run_scope
from kompany.core.watchdog import LLMUnavailable, Watchdog
from kompany.llm.client import LLMClient, LLMResponse
from kompany.llm.cost_tracker import CostTracker
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.episode_payload import EpisodePayloadV1
from kompany.state.episodes import Episodes
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


def _make_stack(tmp_path, *, silent_timeout=0.15, stale_seconds=1):
    db = Database(tmp_path)
    audit = AuditLog(db)
    projects = Projects(db)
    health = HealthEvents(db)
    ledger = Ledger(db)
    cost_tracker = CostTracker(ledger)
    episodes = Episodes(db)
    watchdog = Watchdog(
        health_events=health,
        projects=projects,
        audit=audit,
        stale_threshold_seconds=stale_seconds,
        scan_interval_seconds=1,
    )
    client = LLMClient(
        settings=FakeSettings(),
        cost_tracker=cost_tracker,
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
        "episodes": episodes,
    }


def _ok_response():
    return LLMResponse(
        text="ok",
        input_tokens=10,
        output_tokens=4,
        cost_usd=0.0,
        model="claude-sonnet-4-20250514",
    )


def _seed_active_task(stack, task_id="t1", project_id="p1", age_seconds=10):
    stack["projects"].create(Project(
        id=project_id,
        name="P",
        type=ProjectType.OPERATIONAL,
        assigned_agents=["coo"],
    ))
    stack["projects"].create_task(Task(
        id=task_id,
        project_id=project_id,
        title="Build it",
        assigned_agent="coo",
        status=TaskStatus.ACTIVE,
    ))
    stack["db"].execute(
        "UPDATE tasks SET updated_at = datetime('now', ?) WHERE id = ?",
        (f"-{age_seconds} seconds", task_id),
    )
    stack["db"].commit()


# ----------------------------------------------------------------------
# Case 1 — 429 then success
# ----------------------------------------------------------------------

def test_429_then_success_writes_silent_and_recovered(tmp_path):
    stack = _make_stack(tmp_path)
    state = {"calls": 0}

    def call(*_args, **_kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            err = RuntimeError("rate limit exceeded")
            err.status_code = 429
            raise err
        return _ok_response()

    stack["client"]._call_anthropic = call
    with run_scope():
        resp = stack["client"].call(
            model="claude-sonnet-4-20250514",
            system="s",
            prompt="p",
            agent_name="ceo",
            task_id="t1",
            project_id="p1",
        )
    assert resp.text == "ok"
    kinds = sorted(e["kind"] for e in stack["health"].list())
    assert "silent_run" in kinds and "recovered" in kinds
    ai_rows = stack["db"].execute(
        "SELECT COUNT(*) c FROM ledger WHERE category = 'ai_cost'"
    ).fetchone()
    assert ai_rows["c"] >= 1


# ----------------------------------------------------------------------
# Case 1b — fast success on the first try (the common path) must record
# cost exactly like the slow/retry success exits. Regression for the bug
# where the watchdog fast path returned the raw response without
# ``_record_success`` — every call booked $0, no ledger row, no
# ``llm.call`` audit, no ``llm.spend`` SSE.
# ----------------------------------------------------------------------

def test_fast_success_records_cost_and_audit(tmp_path):
    stack = _make_stack(tmp_path)

    stack["client"]._call_anthropic = lambda *_a, **_k: _ok_response()
    with run_scope():
        resp = stack["client"].call(
            model="claude-sonnet-4-20250514",
            system="s",
            prompt="p",
            agent_name="ceo",
            task_id="t1",
            project_id="p1",
        )

    assert resp.cost_usd > 0
    ai_rows = stack["db"].execute(
        "SELECT COUNT(*) c FROM ledger WHERE category = 'ai_cost'"
    ).fetchone()
    assert ai_rows["c"] == 1
    llm_calls = stack["db"].execute(
        "SELECT COUNT(*) c FROM audit_log WHERE event_type = 'llm.call'"
    ).fetchone()
    assert llm_calls["c"] == 1
    # No health noise on the clean path.
    assert stack["health"].list() == []


# ----------------------------------------------------------------------
# Case 2 — network down x2
# ----------------------------------------------------------------------

def test_two_failures_raises_LLMUnavailable_and_writes_retry_exhausted(tmp_path):
    stack = _make_stack(tmp_path)

    def always_fail(*_args, **_kwargs):
        raise ConnectionError("network down")

    stack["client"]._call_anthropic = always_fail
    with run_scope():
        with pytest.raises(LLMUnavailable):
            stack["client"].call(
                model="claude-sonnet-4-20250514",
                system="s",
                prompt="p",
                agent_name="ceo",
                task_id="t1",
                project_id="p1",
            )

    kinds = sorted(e["kind"] for e in stack["health"].list())
    assert "retry_exhausted" in kinds
    assert "silent_run" in kinds


# ----------------------------------------------------------------------
# Case 3 — slow success
# ----------------------------------------------------------------------

def test_hang_then_success_records_one_ledger_entry(tmp_path):
    stack = _make_stack(tmp_path, silent_timeout=0.1)

    def slow(*_args, **_kwargs):
        time.sleep(0.25)
        return _ok_response()

    stack["client"]._call_anthropic = slow
    with run_scope():
        stack["client"].call(
            model="claude-sonnet-4-20250514",
            system="s",
            prompt="p",
            agent_name="ceo",
            task_id="t1",
            project_id="p1",
        )

    ai_rows = stack["db"].execute(
        "SELECT COUNT(*) c FROM ledger WHERE category = 'ai_cost'"
    ).fetchone()
    assert ai_rows["c"] == 1
    kinds = [e["kind"] for e in stack["health"].list()]
    assert "silent_run" in kinds
    assert "recovered" in kinds


# ----------------------------------------------------------------------
# Case 4 — stranded scanner
# ----------------------------------------------------------------------

def test_scanner_marks_stale_active_task_stranded(tmp_path):
    stack = _make_stack(tmp_path, stale_seconds=2)
    _seed_active_task(stack, task_id="t1", project_id="p1", age_seconds=10)
    events = stack["watchdog"].scan_once()
    assert len(events) == 1
    assert events[0]["kind"] == "stranded_in_progress"
    row = stack["db"].execute(
        "SELECT status FROM tasks WHERE id = ?", ("t1",)
    ).fetchone()
    assert row["status"] == "stranded_in_progress"


# ----------------------------------------------------------------------
# Case 5 — snooze suppresses re-warning
# ----------------------------------------------------------------------

def test_snoozed_event_suppresses_rewarning(tmp_path):
    stack = _make_stack(tmp_path, stale_seconds=2)
    _seed_active_task(stack, task_id="t1", project_id="p1", age_seconds=10)
    first = stack["watchdog"].scan_once()
    assert len(first) == 1
    stack["watchdog"].resolve(first[0]["id"], action="snooze", snooze_minutes=60)
    # Reset task to active+stale for the next scan.
    stack["db"].execute(
        "UPDATE tasks SET status='active', updated_at=datetime('now', '-10 seconds') WHERE id = ?",
        ("t1",),
    )
    stack["db"].commit()
    second = stack["watchdog"].scan_once()
    assert second == []


# ----------------------------------------------------------------------
# Case 6 — episode materializer populates health_events slot
# ----------------------------------------------------------------------

def test_episode_payload_includes_health_events(tmp_path):
    stack = _make_stack(tmp_path)
    stack["projects"].create(Project(
        id="p1",
        name="P",
        type=ProjectType.OPERATIONAL,
        assigned_agents=["coo"],
        triggers_directive_id="dir-1",
    ))
    stack["watchdog"].record_silent_run(
        task_id="t1", project_id="p1", detail={"reason": "demo"}
    )
    stack["watchdog"].record_recovered(task_id="t1", project_id="p1")

    payload = stack["episodes"].materialize("p1")
    assert len(payload.health_events) == 2
    # Validate against the frozen schema (round-trip through JSON).
    serialized = payload.model_dump_json()
    parsed = EpisodePayloadV1.model_validate_json(serialized)
    assert len(parsed.health_events) == 2
    assert {e.kind for e in parsed.health_events} == {"silent_run", "recovered"}
    # New optional fields must round-trip.
    silent = next(e for e in parsed.health_events if e.kind == "silent_run")
    assert silent.id is not None
    assert silent.status in {"open", "resolved"}
    assert silent.project_id == "p1"
