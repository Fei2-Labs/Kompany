"""Probation gate for evolved souls (09-26-evolution-probation).

Pure SQL over ``tasks``; the revert path is monkeypatched so no git
workspace is needed. Engines are the real ``KompanyEngine`` against the
conftest-isolated data dir.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import kompany.core.artifact_evolution.probation as prob_mod
from kompany.core.artifact_evolution.probation import (
    close_probation,
    probation_tick,
    role_from_target,
    start_probation,
)
from kompany.core.engine import KompanyEngine


@pytest.fixture
def engine():
    e = KompanyEngine()
    e.db.execute(
        "INSERT INTO projects (id, name, type, status) VALUES ('p-1', 'Proj', 'service', 'active')"
    )
    e.db.commit()
    return e


def _task(engine, tid, role, status, days_ago=0.0):
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    engine.db.execute(
        "INSERT INTO tasks (id, project_id, title, status, assigned_agent, updated_at) "
        "VALUES (?, 'p-1', ?, ?, ?, ?)",
        (tid, tid, status, role, ts),
    )
    engine.db.commit()


def _applied(engine, pid="ap-1", target="cmo.yaml"):
    engine.db.execute(
        "INSERT INTO artifact_proposals (id, kind, target, instruction, status, commit_sha) "
        "VALUES (?, 'soul', ?, 'evolve', 'applied', 'abc123')",
        (pid, target),
    )
    engine.db.commit()
    return engine.artifact_proposals.get(pid)


def test_role_from_target():
    assert role_from_target("cmo.yaml") == "cmo"
    assert role_from_target("CTO") == "cto"


def test_start_records_baseline_from_window(engine):
    _task(engine, "t-1", "cmo", "failed", days_ago=2)
    _task(engine, "t-2", "cmo", "completed", days_ago=3)
    _task(engine, "t-3", "cmo", "failed", days_ago=40)  # outside 14d window
    _task(engine, "t-4", "cto", "failed", days_ago=1)  # other role
    row = start_probation(engine, _applied(engine))
    assert row["role"] == "cmo"
    assert (row["baseline_failed"], row["baseline_total"]) == (1, 2)
    assert row["status"] == "probation"
    assert row["trial_target"] == 5


def test_start_ignores_non_soul_or_disabled(engine):
    engine.db.execute(
        "INSERT INTO artifact_proposals (id, kind, target, instruction, status) "
        "VALUES ('wf-1', 'workflow', 'daily.yaml', 'x', 'applied')"
    )
    engine.db.commit()
    assert start_probation(engine, engine.artifact_proposals.get("wf-1")) is None
    engine.settings.evolution_probation_enabled = False
    assert start_probation(engine, _applied(engine)) is None


def _future_task(engine, tid, role, status, minutes=1):
    ts = (datetime.now(UTC) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    engine.db.execute(
        "INSERT INTO tasks (id, project_id, title, status, assigned_agent, updated_at) "
        "VALUES (?, 'p-1', ?, ?, ?, ?)",
        (tid, tid, status, role, ts),
    )
    engine.db.commit()


def test_tick_waits_for_trial_target(engine):
    start_probation(engine, _applied(engine))
    _future_task(engine, "n-1", "cmo", "failed")
    _future_task(engine, "n-2", "cmo", "failed")
    assert probation_tick(engine) == []
    row = engine.artifact_probations.get("ap-1")
    assert (row["trial_failed"], row["trial_total"], row["status"]) == (2, 2, "probation")


def test_tick_reverts_when_worse_than_baseline(engine, monkeypatch):
    _task(engine, "b-1", "cmo", "completed", days_ago=1)
    _task(engine, "b-2", "cmo", "completed", days_ago=1)
    start_probation(engine, _applied(engine))
    reverts = []
    monkeypatch.setattr(
        "kompany.core.artifact_evolution.pipeline.revert_artifact_proposal",
        lambda eng, pid, reason="": reverts.append((pid, reason)) or {},
    )
    for i in range(3):
        _future_task(engine, f"n-{i}", "cmo", "failed", minutes=i + 1)
    for i in range(3, 5):
        _future_task(engine, f"n-{i}", "cmo", "completed", minutes=i + 1)

    out = probation_tick(engine)

    assert out == ["evolution_probation:ap-1:reverted"]
    assert reverts[0][0] == "ap-1" and reverts[0][1].startswith("probation failed")
    row = engine.artifact_probations.get("ap-1")
    assert row["status"] == "reverted"
    assert "3/5" in row["note"]


def test_tick_passes_when_not_worse(engine, monkeypatch):
    _task(engine, "b-1", "cmo", "failed", days_ago=1)
    _task(engine, "b-2", "cmo", "completed", days_ago=1)
    start_probation(engine, _applied(engine))
    monkeypatch.setattr(
        "kompany.core.artifact_evolution.pipeline.revert_artifact_proposal",
        lambda *a, **k: pytest.fail("must not revert"),
    )
    _future_task(engine, "n-0", "cmo", "failed", minutes=1)  # 1/5 = 20% < 50%
    for i in range(1, 5):
        _future_task(engine, f"n-{i}", "cmo", "completed", minutes=i + 1)
    assert probation_tick(engine) == ["evolution_probation:ap-1:passed"]
    assert engine.artifact_probations.get("ap-1")["status"] == "passed"


def test_single_failure_never_reverts(engine, monkeypatch):
    """Zero baseline + one trial failure is noise, not evidence."""
    start_probation(engine, _applied(engine))
    monkeypatch.setattr(
        "kompany.core.artifact_evolution.pipeline.revert_artifact_proposal",
        lambda *a, **k: pytest.fail("must not revert"),
    )
    _future_task(engine, "n-0", "cmo", "failed", minutes=1)
    for i in range(1, 5):
        _future_task(engine, f"n-{i}", "cmo", "completed", minutes=i + 1)
    assert probation_tick(engine) == ["evolution_probation:ap-1:passed"]


def test_tick_expires_inconclusive_after_max_days(engine, monkeypatch):
    start_probation(engine, _applied(engine))
    later = datetime.now(UTC) + timedelta(days=31)
    monkeypatch.setattr(prob_mod, "_utcnow", lambda: later)
    assert probation_tick(engine) == ["evolution_probation:ap-1:inconclusive"]
    assert engine.artifact_probations.get("ap-1")["status"] == "inconclusive"


def test_close_probation_on_founder_revert(engine):
    start_probation(engine, _applied(engine))
    close_probation(engine, "ap-1", "founder revert")
    assert engine.artifact_probations.get("ap-1")["status"] == "closed"
    assert probation_tick(engine) == []


def test_engine_wiring_and_status_surface(engine):
    names = [n for n, _ in engine.ticker.actions]
    assert names.index("evolution_probation") < names.index("founder_report")
    start_probation(engine, _applied(engine))
    status = engine.evolution_status()
    assert status["probations"][0]["proposal_id"] == "ap-1"


def test_settings_yaml_loads_probation_keys(tmp_path):
    from kompany.config.settings import KompanySettings

    cfg = tmp_path / "config.yaml"
    cfg.write_text("evolution_probation_enabled: false\nevolution_probation_trials: 8\n")
    s = KompanySettings.load(str(cfg))
    assert s.evolution_probation_enabled is False
    assert s.evolution_probation_trials == 8
    assert s.auto_evolution_enabled is True
