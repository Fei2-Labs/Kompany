"""08-29 self-evolution R7: flip-rate metrics + evolution_rubber_stamp.

A gate that never says no is a rubber stamp, not a gate. Code lane = founder
rejected ÷ (approved + rejected); artifact lane = doctor-triggered reverts
÷ total auto-applies (applied + reverted). Either at 0 over the rolling
window with enough decisions to judge is an ``evolution_rubber_stamp``
health event, recorded once and resolved on the next clean doctor run.
"""

from __future__ import annotations

import pytest

from kompany.core.artifact_evolution.flip_stats import (
    KIND_RUBBER_STAMP,
    MIN_SAMPLE,
    WINDOW_DAYS,
    flip_rates,
    reconcile_rubber_stamp,
)
from kompany.core.doctor import run_doctor
from kompany.core.engine import KompanyEngine


@pytest.fixture
def engine():
    return KompanyEngine()


def _code(engine, *statuses):
    for st in statuses:
        pid = engine.self_update_proposals.create(f"instr {st}")
        engine.self_update_proposals.update(pid, status=st)


def test_fresh_engine_no_rubber_stamp(engine):
    rates = flip_rates(engine)
    assert rates["code_lane"]["decisions"] == 0
    assert rates["artifact_lane"]["auto_applies"] == 0
    assert rates["rubber_stamp"] is False
    assert rates["code_lane"]["rate"] == 0.0 and rates["artifact_lane"]["rate"] == 0.0


def test_code_lane_rubber_stamp_when_all_approved(engine):
    _code(engine, *(["approved"] * MIN_SAMPLE))
    rates = flip_rates(engine)
    assert rates["code_lane"]["decisions"] == MIN_SAMPLE
    assert rates["code_lane"]["rate"] == 0.0
    assert rates["rubber_stamp"] is True


def test_code_lane_not_rubber_stamp_when_rejected(engine):
    _code(engine, "approved", "approved", "approved", "rejected")
    rates = flip_rates(engine)
    assert rates["code_lane"]["rate"] == 0.25
    assert rates["rubber_stamp"] is False


def test_below_min_sample_is_not_rubber_stamp(engine):
    _code(engine, "approved", "approved")
    rates = flip_rates(engine)
    assert rates["code_lane"]["decisions"] == 2 < MIN_SAMPLE
    assert rates["rubber_stamp"] is False


def test_artifact_lane_rubber_stamp_when_doctor_never_reverts(engine):
    for _ in range(MIN_SAMPLE):
        pid = engine.artifact_proposals.create("soul", "growth-hacker", "x")
        engine.artifact_proposals.update(pid, status="applied")
    rates = flip_rates(engine)
    assert rates["artifact_lane"]["auto_applies"] == MIN_SAMPLE
    assert rates["artifact_lane"]["rate"] == 0.0
    assert rates["rubber_stamp"] is True


def test_artifact_lane_doctor_revert_counts_as_flip(engine):
    applied, reverted = 3, 1
    for _ in range(applied):
        pid = engine.artifact_proposals.create("soul", "a", "x")
        engine.artifact_proposals.update(pid, status="applied")
    pid = engine.artifact_proposals.create("soul", "b", "x")
    engine.artifact_proposals.update(pid, status="reverted", doctor_status="fail")
    rates = flip_rates(engine)
    assert rates["artifact_lane"]["auto_applies"] == applied + reverted
    assert rates["artifact_lane"]["doctor_reverts"] == 1
    assert rates["artifact_lane"]["rate"] == 0.25
    assert rates["rubber_stamp"] is False


def test_founder_revert_counts_denominator_not_numerator(engine):
    for _ in range(3):
        pid = engine.artifact_proposals.create("soul", "a", "x")
        engine.artifact_proposals.update(pid, status="applied")
    pid = engine.artifact_proposals.create("soul", "b", "x")
    engine.artifact_proposals.update(pid, status="reverted")  # founder: doctor_status stays None
    rates = flip_rates(engine)
    assert rates["artifact_lane"]["auto_applies"] == 4
    assert rates["artifact_lane"]["doctor_reverts"] == 0
    assert rates["artifact_lane"]["rate"] == 0.0
    assert rates["rubber_stamp"] is True  # still a rubber stamp: the doctor never flipped one


def test_rolling_window_excludes_old_decisions(engine):
    _code(engine, "approved", "approved", "approved", "approved", "rejected")
    engine.db.execute("UPDATE self_update_proposals SET updated_at = datetime('now', '-40 days') WHERE status = 'rejected'")
    engine.db.commit()
    rates = flip_rates(engine, window_days=30)
    assert rates["code_lane"]["decisions"] == 4
    assert rates["code_lane"]["rejected"] == 0
    assert rates["rubber_stamp"] is True  # within the window: all approved, sample met


def test_rubber_stamp_event_lifecycle(engine):
    _code(engine, *(["approved"] * MIN_SAMPLE))
    rep = run_doctor(engine)
    ids = {n["id"] for n in _flatten(rep)}
    assert "evolution_flip" in ids
    open_ = engine.health_events.list(status="open", kind=KIND_RUBBER_STAMP)
    assert len(open_) == 1
    assert open_[0]["detail"]["code_lane"]["rejected"] == 0
    # a second run must not stack a duplicate open event
    run_doctor(engine)
    assert len(engine.health_events.list(status="open", kind=KIND_RUBBER_STAMP)) == 1
    # a rejection clears it automatically on the next run
    _code(engine, "rejected")
    run_doctor(engine)
    assert engine.health_events.list(status="open", kind=KIND_RUBBER_STAMP) == []


def test_reconcile_is_idempotent_noop(engine):
    reconcile_rubber_stamp(engine)
    assert engine.health_events.list(kind=KIND_RUBBER_STAMP) == []
    reconcile_rubber_stamp(engine)  # no exception


def test_evolution_status_surfaces_flip_rates(engine):
    _code(engine, "approved", "approved", "approved")
    st = engine.evolution_status()
    assert st["flip_rates"]["window_days"] == WINDOW_DAYS
    assert st["flip_rates"]["code_lane"]["decisions"] == 3


def _flatten(n):
    out = [n]
    for c in n.get("children", []):
        out.extend(_flatten(c))
    return out