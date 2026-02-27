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


# --- Multi-provider pricing tests ---


def test_estimate_cost_gpt4o():
    cost = estimate_cost("gpt-4o", 1000, 500)
    expected = 1000 * 2.50 / 1_000_000 + 500 * 10.0 / 1_000_000
    assert cost == pytest.approx(expected)


def test_estimate_cost_gemini_flash():
    cost = estimate_cost("gemini-2.0-flash", 1000, 500)
    expected = 1000 * 0.10 / 1_000_000 + 500 * 0.40 / 1_000_000
    assert cost == pytest.approx(expected)


def test_estimate_cost_glm4_air():
    cost = estimate_cost("glm-4-air", 1000, 500)
    expected = 1000 * 0.14 / 1_000_000 + 500 * 0.14 / 1_000_000
    assert cost == pytest.approx(expected)


def test_estimate_cost_moonshot():
    cost = estimate_cost("moonshot-v1-8k", 1000, 500)
    expected = 1000 * 1.67 / 1_000_000 + 500 * 1.67 / 1_000_000
    assert cost == pytest.approx(expected)


def test_estimate_cost_prefix_fallback():
    """Unknown GPT variant should use prefix-based fallback pricing."""
    cost = estimate_cost("gpt-5-turbo", 1000, 500)
    expected = 1000 * 2.50 / 1_000_000 + 500 * 10.0 / 1_000_000
    assert cost == pytest.approx(expected)


def test_estimate_cost_prefix_fallback_gemini():
    """Unknown Gemini variant should use prefix-based fallback pricing."""
    cost = estimate_cost("gemini-3.0-ultra", 1000, 500)
    expected = 1000 * 0.15 / 1_000_000 + 500 * 0.60 / 1_000_000
    assert cost == pytest.approx(expected)


def test_all_pricing_entries_valid():
    """Every entry in PRICING should have non-negative values."""
    for model, p in PRICING.items():
        assert p.input_per_mtok >= 0, f"{model} has negative input pricing"
        assert p.output_per_mtok >= 0, f"{model} has negative output pricing"
