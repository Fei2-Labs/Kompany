"""Tests for ``Watchdog._scan_runway`` + the engine's runway snapshot.

Mission-targets task 05-19. Covers:

* No deadline / zero burn → no alert.
* Projected burn > cash and no open alert → write ``runway_alert``.
* Existing open ``runway_alert`` is *not* duplicated.
* Past deadline → no alert (v1 stays scoped to "running out of runway").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from kompany.core.watchdog import KIND_RUNWAY_ALERT, Watchdog
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.health_events import HealthEvents
from kompany.state.projects import Projects


@pytest.fixture
def world(tmp_path):
    db = Database(tmp_path)
    health = HealthEvents(db)
    projects = Projects(db)
    audit = AuditLog(db)
    return {
        "db": db,
        "health": health,
        "projects": projects,
        "audit": audit,
    }


def _make_watchdog(world, provider) -> Watchdog:
    return Watchdog(
        health_events=world["health"],
        projects=world["projects"],
        audit=world["audit"],
        scan_interval_seconds=1,
        stale_threshold_seconds=600,
        runway_provider=provider,
    )


def _future(days: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).isoformat()


def _past(days: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()


def test_scan_runway_no_provider_returns_none(world) -> None:
    """No provider wired → silent no-op."""
    w = _make_watchdog(world, provider=None)
    assert w._scan_runway() is None
    assert world["health"].list(kind=KIND_RUNWAY_ALERT) == []


def test_scan_runway_skips_when_no_deadline(world) -> None:
    w = _make_watchdog(
        world,
        provider=lambda: {
            "cash": 500.0,
            "burn_rate": 100.0,
            "deadline": None,
        },
    )
    assert w._scan_runway() is None


def test_scan_runway_skips_when_burn_rate_zero(world) -> None:
    w = _make_watchdog(
        world,
        provider=lambda: {
            "cash": 5000.0,
            "burn_rate": 0.0,
            "deadline": _future(30),
        },
    )
    assert w._scan_runway() is None


def test_scan_runway_writes_alert_when_projected_burn_exceeds_cash(world) -> None:
    """Burn of $10/h * 24h * 30days = $7200 → exceeds $5k cash → alert fires."""
    w = _make_watchdog(
        world,
        provider=lambda: {
            "cash": 5000.0,
            "burn_rate": 10.0,
            "deadline": _future(30),
            "targets": {"revenue_target": 10000.0},
        },
    )
    event = w._scan_runway()
    assert event is not None
    assert event["kind"] == KIND_RUNWAY_ALERT
    assert event["status"] == "open"
    # Detail carries the math + a snapshot of the targets.
    detail = event["detail"]
    assert detail["cash"] == 5000.0
    assert detail["burn_rate_per_hour"] == 10.0
    assert "projected_burn" in detail
    assert detail["targets"]["revenue_target"] == 10000.0


def test_scan_runway_does_not_duplicate_open_alert(world) -> None:
    """Two consecutive ticks with the same provider snapshot yield one alert."""
    snapshot = {
        "cash": 1000.0,
        "burn_rate": 50.0,
        "deadline": _future(30),
    }
    w = _make_watchdog(world, provider=lambda: snapshot)
    first = w._scan_runway()
    second = w._scan_runway()
    assert first is not None
    assert second is None
    opens = world["health"].list(kind=KIND_RUNWAY_ALERT, status="open")
    assert len(opens) == 1


def test_scan_runway_skips_past_deadline(world) -> None:
    """A deadline already in the past produces no alert."""
    w = _make_watchdog(
        world,
        provider=lambda: {
            "cash": 100.0,
            "burn_rate": 100.0,
            "deadline": _past(1),
        },
    )
    assert w._scan_runway() is None


def test_scan_runway_handles_provider_exception(world) -> None:
    """A raising provider must not crash the watchdog."""
    def boom() -> dict[str, Any]:
        raise RuntimeError("ledger offline")

    w = _make_watchdog(world, provider=boom)
    # Should silently swallow + return None.
    assert w._scan_runway() is None


def test_record_runway_alert_writes_event(world) -> None:
    w = _make_watchdog(world, provider=None)
    event = w.record_runway_alert(detail={"reason": "manual"})
    assert event["kind"] == KIND_RUNWAY_ALERT
    assert event["status"] == "open"


# ---------------------------------------------------------------------------
# Engine integration — _runway_snapshot reads agreed targets
# ---------------------------------------------------------------------------


def test_engine_runway_snapshot_uses_agreed_targets(tmp_path, monkeypatch):
    """``engine._runway_snapshot`` reads the authoritative (``agreed > founder``)
    target so the alert tracks the post-review version, not the founder's raw input."""
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    from kompany.core.engine import KompanyEngine
    from kompany.state.targets import CompanyTargets

    engine = KompanyEngine()
    engine.apply_template(
        "saas-startup",
        override_deadline="2099-01-01",
    )
    snapshot = engine._runway_snapshot()
    assert snapshot is not None
    assert snapshot["deadline"] is not None

    # Now write an agreed snapshot — the new value must take over.
    engine.set_targets(
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=7000.0,
            deadline="2098-01-01",
            source="agreed",
        )
    )
    snapshot2 = engine._runway_snapshot()
    assert snapshot2 is not None
    assert snapshot2["targets"]["revenue_target"] == 7000.0
    assert snapshot2["deadline"].startswith("2098-01-01")


def test_engine_runway_snapshot_none_without_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    from kompany.core.engine import KompanyEngine

    engine = KompanyEngine()
    # apply blank — no manifest deadline.
    engine.apply_template("blank")
    assert engine._runway_snapshot() is None
