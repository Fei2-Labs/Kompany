"""Tests for BudgetMeter — external budget enforcement (research pitfall 9)."""

from __future__ import annotations

import threading

import pytest

from kompany.core.harness import BudgetMeter


def test_uncapped_never_exceeds():
    meter = BudgetMeter(None)
    assert meter.add(1_000_000.0)
    assert meter.add(1_000_000.0)
    assert meter.spent_usd == pytest.approx(2_000_000.0)
    assert meter.cap_usd is None


def test_spend_accumulates():
    meter = BudgetMeter(10.0)
    assert meter.add(0.25)
    assert meter.add(0.75)
    assert meter.spent_usd == pytest.approx(1.0)


def test_exact_cap_is_not_a_breach():
    meter = BudgetMeter(1.0)
    assert meter.add(0.5)
    assert meter.add(0.5)  # lands exactly on the cap — still allowed
    assert meter.spent_usd == pytest.approx(1.0)


def test_float_dust_at_cap_is_not_a_breach():
    # 3 × 0.01 sums to 0.030000000000000002 in binary floats — landing
    # exactly on the cap must not count as a breach.
    meter = BudgetMeter(0.03)
    assert meter.add(0.01)
    assert meter.add(0.01)
    assert meter.add(0.01)


def test_cap_plus_epsilon_breaches():
    meter = BudgetMeter(1.0)
    assert meter.add(0.5)
    assert meter.add(0.5)
    assert not meter.add(0.0001)  # strictly above the cap
    assert meter.spent_usd == pytest.approx(1.0001)


def test_stays_breached_after_crossing():
    meter = BudgetMeter(0.01)
    assert not meter.add(0.02)
    assert not meter.add(0.0)  # total still above cap


def test_zero_cap_breaches_on_first_positive_delta():
    meter = BudgetMeter(0.0)
    assert meter.add(0.0)
    assert not meter.add(0.000001)


def test_thread_safety_smoke():
    # 8 threads × 1000 deltas; a lost update would show in the total.
    meter = BudgetMeter(None)

    def worker() -> None:
        for _ in range(1000):
            meter.add(0.001)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert meter.spent_usd == pytest.approx(8.0)
