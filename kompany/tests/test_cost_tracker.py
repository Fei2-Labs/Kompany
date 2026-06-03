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


def test_run_total_isolated_per_run():
    """Each run_scope accumulates its own cost; run_total() reads the
    active run, not the process-wide session_total."""
    from kompany.core.run_context import current_run_id, run_scope

    tracker = CostTracker(ledger=None)

    with run_scope() as run_a:
        tracker.record("claude-sonnet-4-20250514", 1000, 500, "call a1")
        a_after_one = tracker.run_total()
        tracker.record("claude-sonnet-4-20250514", 1000, 500, "call a2")
        a_total = tracker.run_total()

    with run_scope() as run_b:
        tracker.record("claude-sonnet-4-20250514", 1000, 500, "call b1")
        b_total = tracker.run_total()

    # run A saw two calls, run B saw one — each only its own.
    assert a_total == pytest.approx(2 * a_after_one)
    assert b_total == pytest.approx(a_after_one)
    assert tracker.run_total(run_a) == pytest.approx(a_total)
    assert tracker.run_total(run_b) == pytest.approx(b_total)
    # session_total still aggregates everything (kept for coarse consumers).
    assert tracker.session_total == pytest.approx(a_total + b_total)


def test_run_total_zero_outside_scope():
    tracker = CostTracker(ledger=None)
    tracker.record("claude-sonnet-4-20250514", 1000, 500, "no scope")
    # Outside any run_scope, current_run_id() is None → run_total() is 0.0,
    # even though session_total grew.
    assert tracker.run_total() == 0.0
    assert tracker.session_total > 0


def test_run_total_concurrent_threads_do_not_cross_contaminate():
    """Two concurrent process_directive-style runs must accumulate cost
    independently. ContextVars isolate run_scope per thread; the per-run
    accumulator keys on run_id, so each run reports only its own spend."""
    import threading

    from kompany.core.run_context import run_scope

    tracker = CostTracker(ledger=None)
    one_call = estimate_cost("claude-sonnet-4-20250514", 1000, 500)
    results: dict[str, float] = {}
    start = threading.Barrier(2)

    def run(name: str, n_calls: int) -> None:
        with run_scope():
            start.wait()
            for _ in range(n_calls):
                tracker.record("claude-sonnet-4-20250514", 1000, 500, name)
            results[name] = tracker.run_total()

    t1 = threading.Thread(target=run, args=("alpha", 3))
    t2 = threading.Thread(target=run, args=("beta", 5))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each run reports ONLY its own cost despite interleaved record() calls
    # on the shared tracker.
    assert results["alpha"] == pytest.approx(3 * one_call)
    assert results["beta"] == pytest.approx(5 * one_call)


def test_run_totals_bounded_no_unbounded_growth(monkeypatch):
    """A long-lived engine books thousands of runs; ``_run_totals`` must
    stay bounded (oldest evicted) rather than leak one entry per directive."""
    import kompany.llm.cost_tracker as ct
    from kompany.core.run_context import new_run_id

    monkeypatch.setattr(ct, "_MAX_RUN_TOTALS", 8)
    tracker = CostTracker(ledger=None)

    run_ids = []
    for _ in range(50):
        rid = new_run_id()
        run_ids.append(rid)
        tracker.record("claude-sonnet-4-20250514", 1000, 500, "x", run_id=rid)

    # Never exceeds the cap despite 50 distinct runs.
    assert len(tracker._run_totals) <= 8
    # The most recent run is retained and still reports its own cost.
    assert tracker.run_total(run_ids[-1]) > 0
    # An evicted (long-finished) run falls back to 0.0 — never wrong cost.
    assert tracker.run_total(run_ids[0]) == 0.0
    # session_total still aggregates every call regardless of eviction.
    assert tracker.session_total == pytest.approx(
        50 * estimate_cost("claude-sonnet-4-20250514", 1000, 500)
    )


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
