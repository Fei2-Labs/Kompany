"""billing_mode ledger rules (06-11-harness-execution-leg PR3, PRD D2).

api mode: per-token cost = real ledger expense (historical behavior).
subscription mode: per-call real cost 0, shadow record only; the monthly
fee is the recurring real expense booked by heartbeat_once.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import kompany.core.subscription_fee as subscription_fee
from kompany.config.model_source import ModelSource
from kompany.core.engine import KompanyEngine
from kompany.llm.cost_tracker import CostTracker
from kompany.llm.models import estimate_cost, resolve_cost
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory
from kompany.state.shadow_costs import ShadowCostStore


class _FakeHub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, dict(payload)))


@pytest.fixture
def world(tmp_path):
    db = Database(tmp_path)
    ledger = Ledger(db)
    ledger.record(
        amount=100.0, description="Capital", category=LedgerCategory.INCOME
    )
    return SimpleNamespace(
        db=db,
        ledger=ledger,
        hub=_FakeHub(),
        shadow=ShadowCostStore(db),
    )


def _tracker(world, source: ModelSource | None) -> CostTracker:
    return CostTracker(
        world.ledger,
        event_hub=world.hub,
        settings=SimpleNamespace(model_source=source),
        shadow_costs=world.shadow,
    )


# ---------------------------------------------------------------------
# api mode — regression guards
# ---------------------------------------------------------------------


def test_no_model_source_books_real_expense(world):
    """Backward compat: no ModelSource → exactly today's behavior."""
    tracker = _tracker(world, None)
    cost = tracker.record("claude-sonnet-4-20250514", 1000, 500, "call")
    expected = estimate_cost("claude-sonnet-4-20250514", 1000, 500)
    assert cost == pytest.approx(expected)
    assert world.ledger.get_balance() == pytest.approx(100.0 - expected)
    assert world.shadow.get_recent() == []
    [(_, payload)] = world.hub.events
    assert payload["shadow"] is False


def test_api_mode_source_books_real_expense(world):
    tracker = _tracker(world, ModelSource(kind="custom_api"))
    cost = tracker.record("claude-sonnet-4-20250514", 1000, 500, "call")
    assert cost > 0
    assert world.ledger.get_balance() == pytest.approx(100.0 - cost)
    assert world.shadow.get_recent() == []


def test_claude_code_model_books_real_expense_without_model_source(world):
    """The pre-PR3 bug shape: with NO ModelSource configured, claude-code
    calls still book API-equivalent (conservative default, unchanged)."""
    tracker = _tracker(world, None)
    cost = tracker.record("claude-code:sonnet", 1000, 500, "call")
    assert cost > 0
    assert world.ledger.get_balance() < 100.0


# ---------------------------------------------------------------------
# subscription mode — the bug fix
# ---------------------------------------------------------------------


def test_subscription_claude_code_call_is_shadow_not_expense(world):
    source = ModelSource(kind="claude_subscription", monthly_fee_usd=20.0)
    tracker = _tracker(world, source)
    real_cost = tracker.record(
        "claude-code:sonnet", 1000, 500, "CEO classify", run_id="run-1"
    )

    # Real cost 0, balance UNCHANGED — the monthly fee is the real expense.
    assert real_cost == 0.0
    assert world.ledger.get_balance() == pytest.approx(100.0)
    assert tracker.session_total == 0.0
    assert tracker.run_total("run-1") == 0.0

    # Shadow row written with the API-equivalent value.
    [row] = world.shadow.get_recent()
    assert row["model"] == "claude-code:sonnet"
    assert row["tokens_in"] == 1000
    assert row["tokens_out"] == 500
    assert row["run_id"] == "run-1"
    assert row["shadow_value_usd"] == pytest.approx(
        estimate_cost("claude-code:sonnet", 1000, 500)
    )
    assert world.shadow.total(run_id="run-1") == pytest.approx(
        row["shadow_value_usd"]
    )

    # SSE stream stays live, marked shadow.
    [(event_type, payload)] = world.hub.events
    assert event_type == "llm.spend"
    assert payload["shadow"] is True
    assert payload["cost_usd"] == 0.0
    assert payload["shadow_value_usd"] == pytest.approx(row["shadow_value_usd"])


def test_subscription_source_api_routed_model_still_real_expense(world):
    """A claude_subscription source only shadows claude-code:* calls;
    a model routed to a real API endpoint stays a real expense."""
    source = ModelSource(kind="claude_subscription", monthly_fee_usd=20.0)
    tracker = _tracker(world, source)
    cost = tracker.record("claude-sonnet-4-20250514", 1000, 500, "call")
    assert cost > 0
    assert world.ledger.get_balance() == pytest.approx(100.0 - cost)
    assert world.shadow.get_recent() == []


# ---------------------------------------------------------------------
# record_external — harness vehicle results
# ---------------------------------------------------------------------


def test_record_external_api_mode_books_real_cost(world):
    tracker = _tracker(world, ModelSource(kind="custom_api"))
    cost = tracker.record_external(
        model="gpt-4o",
        cost_usd=0.42,
        tokens_in=1000,
        tokens_out=500,
        description="harness session task-1",
        run_id="run-x",
        project_id="proj-1",
    )
    assert cost == pytest.approx(0.42)
    assert world.ledger.get_balance() == pytest.approx(100.0 - 0.42)
    assert tracker.run_total("run-x") == pytest.approx(0.42)
    # Ledger row tagged against the project's envelope.
    [entry] = [
        e for e in world.ledger.get_recent() if e["category"] == "ai_cost"
    ]
    assert entry["project_id"] == "proj-1"
    assert "[estimate]" not in entry["description"]
    [(_, payload)] = world.hub.events
    assert payload["shadow"] is False
    assert payload["action_type"] == "harness_result"


def test_record_external_api_mode_flags_estimates(world):
    tracker = _tracker(world, ModelSource(kind="custom_api"))
    tracker.record_external(
        model="gpt-4o",
        cost_usd=0.10,
        tokens_in=100,
        tokens_out=50,
        description="harness session task-2",
        is_estimate=True,
    )
    [entry] = [
        e for e in world.ledger.get_recent() if e["category"] == "ai_cost"
    ]
    assert "[estimate]" in entry["description"]


def test_record_external_tokens_only_derives_estimate(world):
    """codex-style vehicles report tokens only — cost derived + flagged."""
    tracker = _tracker(world, ModelSource(kind="custom_api"))
    cost = tracker.record_external(
        model="gpt-4o",
        cost_usd=0.0,
        tokens_in=1000,
        tokens_out=500,
        description="codex session",
    )
    assert cost == pytest.approx(estimate_cost("gpt-4o", 1000, 500))
    [entry] = [
        e for e in world.ledger.get_recent() if e["category"] == "ai_cost"
    ]
    assert "[estimate]" in entry["description"]


def test_record_external_subscription_books_shadow(world):
    source = ModelSource(kind="claude_subscription", monthly_fee_usd=20.0)
    tracker = _tracker(world, source)
    cost = tracker.record_external(
        model="claude-code:opus",
        cost_usd=1.23,
        tokens_in=5000,
        tokens_out=2000,
        description="harness session task-3",
        run_id="run-y",
    )
    assert cost == 0.0
    assert world.ledger.get_balance() == pytest.approx(100.0)
    [row] = world.shadow.get_recent()
    assert row["shadow_value_usd"] == pytest.approx(1.23)
    assert row["run_id"] == "run-y"
    [(_, payload)] = world.hub.events
    assert payload["shadow"] is True
    assert payload["cost_usd"] == 0.0


# ---------------------------------------------------------------------
# price_overrides precedence
# ---------------------------------------------------------------------


def test_resolve_cost_override_beats_pricing_exact_match():
    base = estimate_cost("claude-sonnet-4-20250514", 1_000_000, 1_000_000)
    overridden = resolve_cost(
        "claude-sonnet-4-20250514",
        1_000_000,
        1_000_000,
        overrides={"claude-sonnet-4-20250514": (1.0, 2.0)},
    )
    assert overridden == pytest.approx(3.0)
    assert overridden != pytest.approx(base)


def test_resolve_cost_without_override_matches_estimate_cost():
    assert resolve_cost("gpt-4o", 1000, 500) == pytest.approx(
        estimate_cost("gpt-4o", 1000, 500)
    )
    assert resolve_cost(
        "gpt-4o", 1000, 500, overrides={"other-model": (9.0, 9.0)}
    ) == pytest.approx(estimate_cost("gpt-4o", 1000, 500))


def test_price_overrides_apply_in_record(world):
    source = ModelSource(
        kind="custom_api",
        price_overrides={"my-private-model": (1.0, 2.0)},
    )
    tracker = _tracker(world, source)
    cost = tracker.record("my-private-model", 1_000_000, 500_000, "call")
    assert cost == pytest.approx(1.0 + 0.5 * 2.0)
    assert world.ledger.get_balance() == pytest.approx(100.0 - cost)


# ---------------------------------------------------------------------
# monthly subscription fee — idempotent month-rollover booking
# ---------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    """Minimal engine for heartbeat_once (mirrors test_engine fixture)."""
    from kompany.state.approvals import ApprovalRequests
    from kompany.state.audit import AuditLog
    from kompany.state.projects import Projects
    from kompany.state.runtime import RuntimeStateStore

    db = Database(tmp_path)
    ledger = Ledger(db)
    ledger.record(
        amount=100.0, description="Capital", category=LedgerCategory.INCOME
    )
    engine = KompanyEngine.__new__(KompanyEngine)
    engine.settings = SimpleNamespace(
        model_source=ModelSource(
            kind="claude_subscription", monthly_fee_usd=20.0
        )
    )
    engine.db = db
    engine.ledger = ledger
    engine.audit = AuditLog(db)
    engine.projects = Projects(db)
    engine.approvals = ApprovalRequests(db)
    engine.runtime = RuntimeStateStore(db)
    return engine


def _freeze(monkeypatch, iso_day: str) -> None:
    frozen = datetime.fromisoformat(iso_day).replace(tzinfo=UTC)
    monkeypatch.setattr(subscription_fee, "_utcnow", lambda: frozen)


def test_monthly_fee_booked_once_per_month(engine, monkeypatch):
    _freeze(monkeypatch, "2026-06-01")
    report = engine.heartbeat_once()
    assert engine.ledger.get_balance() == pytest.approx(80.0)
    assert engine.ledger.get_totals()["subscription_fee"] == pytest.approx(
        -20.0
    )
    assert any(
        n["kind"] == "subscription_fee_booked"
        for n in report["notifications"]
    )

    # Second heartbeat in the same month: nothing booked.
    _freeze(monkeypatch, "2026-06-15")
    report = engine.heartbeat_once()
    assert engine.ledger.get_balance() == pytest.approx(80.0)
    assert not any(
        n["kind"] == "subscription_fee_booked"
        for n in report["notifications"]
    )

    # Next month: booked again.
    _freeze(monkeypatch, "2026-07-01")
    engine.heartbeat_once()
    assert engine.ledger.get_balance() == pytest.approx(60.0)
    assert engine.ledger.get_totals()["subscription_fee"] == pytest.approx(
        -40.0
    )


def test_monthly_fee_rolls_over_year_boundary(engine, monkeypatch):
    """Dec → Jan crosses the year: distinct month keys, fee books again."""
    _freeze(monkeypatch, "2026-12-31")
    engine.heartbeat_once()
    assert engine.ledger.get_balance() == pytest.approx(80.0)

    _freeze(monkeypatch, "2027-01-01")
    engine.heartbeat_once()
    assert engine.ledger.get_balance() == pytest.approx(60.0)
    assert engine.ledger.get_totals()["subscription_fee"] == pytest.approx(
        -40.0
    )


def test_no_fee_for_api_mode_source(engine, monkeypatch):
    engine.settings = SimpleNamespace(model_source=ModelSource(kind="custom_api"))
    _freeze(monkeypatch, "2026-06-01")
    engine.heartbeat_once()
    assert engine.ledger.get_balance() == pytest.approx(100.0)
    assert "subscription_fee" not in engine.ledger.get_totals()


def test_no_fee_without_model_source(engine, monkeypatch):
    engine.settings = SimpleNamespace(model_source=None)
    _freeze(monkeypatch, "2026-06-01")
    engine.heartbeat_once()
    assert engine.ledger.get_balance() == pytest.approx(100.0)
