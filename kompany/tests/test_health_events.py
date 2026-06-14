"""Tests for the ``health_events`` store and its schema migration."""

from __future__ import annotations

import time

import pytest

from kompany.core.run_context import run_scope
from kompany.state.database import Database
from kompany.state.health_events import (
    HEALTH_KINDS,
    HEALTH_STATUSES,
    PLAYER_ACTIONS,
    HealthEvents,
)


def _make_store(tmp_path) -> tuple[Database, HealthEvents]:
    db = Database(tmp_path)
    return db, HealthEvents(db)


def test_schema_has_health_events_table(tmp_path):
    db, _ = _make_store(tmp_path)
    cols = {
        row["name"]
        for row in db.execute("PRAGMA table_info(health_events)").fetchall()
    }
    assert {
        "id",
        "kind",
        "task_id",
        "project_id",
        "run_id",
        "detail_json",
        "status",
        "resolved_by",
        "resolved_at",
        "snoozed_until",
        "created_at",
    } <= cols


def test_tasks_table_has_updated_at_after_migration(tmp_path):
    db, _ = _make_store(tmp_path)
    cols = {
        row["name"]
        for row in db.execute("PRAGMA table_info(tasks)").fetchall()
    }
    assert "updated_at" in cols


def test_record_and_get_round_trip(tmp_path):
    _, store = _make_store(tmp_path)
    event = store.record(
        kind="silent_run",
        task_id="t1",
        project_id="p1",
        detail={"why": "timeout"},
    )
    assert event["kind"] == "silent_run"
    assert event["task_id"] == "t1"
    assert event["project_id"] == "p1"
    assert event["status"] == "open"
    assert event["detail"] == {"why": "timeout"}
    fetched = store.get(event["id"])
    assert fetched == event


def test_record_rejects_unknown_kind(tmp_path):
    _, store = _make_store(tmp_path)
    with pytest.raises(ValueError):
        store.record(kind="weird_kind")


def test_record_picks_up_active_run_id(tmp_path):
    _, store = _make_store(tmp_path)
    with run_scope() as rid:
        event = store.record(kind="recovered", task_id="t9")
    assert event["run_id"] == rid


def test_list_filters(tmp_path):
    _, store = _make_store(tmp_path)
    a = store.record(kind="silent_run", task_id="t1", project_id="p1")
    b = store.record(kind="stranded_in_progress", task_id="t2", project_id="p1")
    c = store.record(kind="recovered", task_id="t3", project_id="p2")

    open_rows = store.list(status="open")
    assert {r["id"] for r in open_rows} == {a["id"], b["id"], c["id"]}

    p1_rows = store.list(project_id="p1")
    assert {r["id"] for r in p1_rows} == {a["id"], b["id"]}

    silent = store.list(kind="silent_run")
    assert [r["id"] for r in silent] == [a["id"]]


def test_resolve_continue(tmp_path):
    _, store = _make_store(tmp_path)
    event = store.record(kind="silent_run", task_id="t1")
    updated = store.resolve(event["id"], action="continue")
    assert updated is not None
    assert updated["status"] == "resolved"
    assert updated["resolved_by"] == "player"
    assert updated["resolved_at"] is not None


def test_resolve_snooze_requires_minutes(tmp_path):
    _, store = _make_store(tmp_path)
    event = store.record(kind="silent_run", task_id="t1")
    with pytest.raises(ValueError):
        store.resolve(event["id"], action="snooze")
    with pytest.raises(ValueError):
        store.resolve(event["id"], action="snooze", snooze_minutes=0)


def test_resolve_snooze_sets_snoozed_until(tmp_path):
    _, store = _make_store(tmp_path)
    event = store.record(kind="silent_run", task_id="t1")
    updated = store.resolve(event["id"], action="snooze", snooze_minutes=15)
    assert updated["status"] == "snoozed"
    assert updated["snoozed_until"] is not None


def test_resolve_dismiss(tmp_path):
    _, store = _make_store(tmp_path)
    event = store.record(kind="silent_run", task_id="t1")
    updated = store.resolve(event["id"], action="dismiss")
    assert updated["status"] == "dismissed"
    assert updated["resolved_by"] == "player"


def test_resolve_rejects_unknown_action(tmp_path):
    _, store = _make_store(tmp_path)
    event = store.record(kind="silent_run", task_id="t1")
    with pytest.raises(ValueError):
        store.resolve(event["id"], action="reboot")


def test_resolve_missing_event_returns_none(tmp_path):
    _, store = _make_store(tmp_path)
    assert store.resolve("he_missing", action="continue") is None


def test_close_open_resolves_matching_silent_run(tmp_path):
    _, store = _make_store(tmp_path)
    with run_scope() as rid:
        event = store.record(kind="silent_run", task_id="t1")
    closed = store.close_open(kind="silent_run", task_id="t1", run_id=rid)
    assert closed == 1
    fetched = store.get(event["id"])
    assert fetched["status"] == "resolved"
    assert fetched["resolved_by"] == "system"


def test_find_active_snoozed_returns_only_within_window(tmp_path):
    _, store = _make_store(tmp_path)
    event = store.record(kind="stranded_in_progress", task_id="t1")
    store.resolve(event["id"], action="snooze", snooze_minutes=1)
    # Should still be findable
    found = store.find_active_snoozed(
        kind="stranded_in_progress", task_id="t1"
    )
    assert found is not None
    assert found["id"] == event["id"]


def test_list_for_project_orders_oldest_first(tmp_path):
    _, store = _make_store(tmp_path)
    # SQLite's datetime('now') is second-resolution; sleep across a second
    # boundary so the two rows really do have distinct created_at values.
    first = store.record(kind="silent_run", project_id="p1")
    time.sleep(1.05)
    second = store.record(kind="recovered", project_id="p1")
    rows = store.list_for_project("p1")
    assert [r["id"] for r in rows] == [first["id"], second["id"]]


def test_constants_match_prd():
    assert HEALTH_KINDS == frozenset({
        "stranded_todo",
        "stranded_in_progress",
        "silent_run",
        "recovered",
        "retry_exhausted",
        # Added by 05-19-mission-targets-and-deadline: watchdog fires this
        # when projected burn through deadline exceeds available cash.
        "runway_alert",
        # Added by 05-19-glossary-and-drift-detection: CoS retrospective
        # writes this when it spots forbidden synonyms in agent output.
        "glossary_drift_alert",
        # Added by 06-11-harness-execution-leg PR5: the ModelSource's
        # derived vehicle binary is missing from PATH — the engine
        # degrades to the legacy single-call path instead of crashing.
        "harness_vehicle_missing",
        # Added by 06-12-self-update-pipeline PR1: a self-update session's
        # diff touched a T3 (protected) path — proposal aborted, branch
        # discarded.
        "self_update_t3_blocked",
        # Added by ADR-0005 (concurrent autonomous runtime): a lane's
        # model pool was exhausted mid-dispatch (loud failure, never
        # silent success) / a lane lease went stale.
        "lane_timeout",
        "lane_stalled",
    })
    assert HEALTH_STATUSES == frozenset({
        "open", "resolved", "snoozed", "dismissed",
    })
    assert PLAYER_ACTIONS == frozenset({"continue", "snooze", "dismiss"})
