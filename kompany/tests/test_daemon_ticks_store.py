"""Tests for the daemon_ticks store (06-12-daemon-tick-loop PR1)."""

from __future__ import annotations

from kompany.state.daemon_ticks import DaemonTickStore
from kompany.state.database import Database


def _store(tmp_path) -> DaemonTickStore:
    return DaemonTickStore(Database(tmp_path))


def _record(store, n=1, outcome="ok", actions=None, detail=None):
    rows = []
    for i in range(n):
        rows.append(store.record(
            started_at=f"2026-06-12T00:00:{i:02d}+00:00",
            duration_ms=i,
            actions=actions if actions is not None else [f"a{i}"],
            outcome=outcome,
            detail=detail,
        ))
    return rows


def test_record_round_trips_actions_and_detail(tmp_path):
    store = _store(tmp_path)
    row = store.record(
        started_at="2026-06-12T00:00:00+00:00",
        duration_ms=42,
        actions=["heartbeat", "advanced_task:t1"],
        outcome="ok",
        detail={"errors": {}},
    )
    assert row["started_at"] == "2026-06-12T00:00:00+00:00"
    assert row["duration_ms"] == 42
    assert row["actions"] == ["heartbeat", "advanced_task:t1"]
    assert row["outcome"] == "ok"
    assert row["detail"] == {"errors": {}}


def test_record_without_detail_round_trips_none(tmp_path):
    store = _store(tmp_path)
    row = store.record(
        started_at="2026-06-12T00:00:00+00:00",
        duration_ms=0,
        actions=[],
        outcome="idle_suspended",
        detail=None,
    )
    assert row["detail"] is None
    assert row["actions"] == []


def test_recent_orders_newest_first_and_limits(tmp_path):
    store = _store(tmp_path)
    _record(store, n=5)
    rows = store.recent(limit=3)
    assert len(rows) == 3
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids, reverse=True)
    assert rows[0]["actions"] == ["a4"]


def test_count(tmp_path):
    store = _store(tmp_path)
    assert store.count() == 0
    _record(store, n=4)
    assert store.count() == 4


def test_prune_keeps_500_newest(tmp_path):
    store = _store(tmp_path)
    _record(store, n=505)
    removed = store.prune(keep=500)
    assert removed == 5
    assert store.count() == 500
    # The oldest rows are the ones that went.
    ids = [r["id"] for r in store.recent(limit=500)]
    assert min(ids) == 6


def test_prune_noop_under_keep(tmp_path):
    store = _store(tmp_path)
    _record(store, n=3)
    assert store.prune(keep=500) == 0
    assert store.count() == 3
