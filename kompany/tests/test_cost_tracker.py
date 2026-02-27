"""Tests for cost tracker and model pricing."""

from __future__ import annotations

import pytest

from kompany.llm.cost_tracker import CostTracker
from kompany.llm.models import ModelPricing, PRICING, estimate_cost
from kompany.state.database import Database
from kompany.state.ledger import Ledger


def test_estimate_cost_opus():
    cost = estimate_cost("claude-opus-4-20250514", 1000, 500)
    expected = 1000 * 15.0 / 1_000_000 + 500 * 75.0 / 1_000_000
    assert cost == pytest.approx(expected)


def test_estimate_cost_sonnet():
    cost = estimate_cost("claude-sonnet-4-20250514", 1000, 500)
    expected = 1000 * 3.0 / 1_000_000 + 500 * 15.0 / 1_000_000
    assert cost == pytest.approx(expected)


def test_estimate_cost_unknown_model_uses_sonnet_pricing():
    cost = estimate_cost("unknown-model", 1000, 500)
    expected = 1000 * 3.0 / 1_000_000 + 500 * 15.0 / 1_000_000
    assert cost == pytest.approx(expected)


def test_cost_tracker_session_total():
    tracker = CostTracker(ledger=None)
    tracker.record("claude-sonnet-4-20250514", 1000, 500, "test call")
    assert tracker.session_total > 0


def test_cost_tracker_records_to_ledger(tmp_path):
    db = Database(tmp_path)
    ledger = Ledger(db)
    ledger.record(amount=10.0, description="Capital",
                  category=__import__("kompany.state.models", fromlist=["LedgerCategory"]).LedgerCategory.INCOME)
    tracker = CostTracker(ledger=ledger)
    tracker.record("claude-sonnet-4-20250514", 1000, 500, "CEO classify")
    assert ledger.get_balance() < 10.0
    totals = ledger.get_totals()
    assert "ai_cost" in totals
