"""Tests for ``Ledger.recent_burn_rate`` used by the dashboard chip."""

from __future__ import annotations

import pytest

from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory


def _ledger(tmp_path) -> Ledger:
    db = Database(tmp_path)
    return Ledger(db)


def test_burn_rate_zero_when_no_expenses(tmp_path):
    led = _ledger(tmp_path)
    led.record(amount=100.0, description="seed", category=LedgerCategory.INCOME)
    assert led.recent_burn_rate(window_hours=1) == 0.0


def test_burn_rate_counts_recent_expenses(tmp_path):
    led = _ledger(tmp_path)
    led.record(amount=100.0, description="seed", category=LedgerCategory.INCOME)
    led.record_ai_cost(amount_usd=0.50, description="r1")
    led.record_ai_cost(amount_usd=0.30, description="r2")
    # Window of 1 hour → 0.80 USD / 1 h = 0.80 USD/h
    burn = led.recent_burn_rate(window_hours=1)
    assert burn == pytest.approx(0.80, rel=1e-3)


def test_burn_rate_window_division(tmp_path):
    led = _ledger(tmp_path)
    led.record(amount=100.0, description="seed", category=LedgerCategory.INCOME)
    led.record_ai_cost(amount_usd=2.0, description="big")
    # Same expenses over 4h window → 0.5 USD/h
    burn = led.recent_burn_rate(window_hours=4)
    assert burn == pytest.approx(0.5, rel=1e-3)


def test_burn_rate_invalid_window(tmp_path):
    led = _ledger(tmp_path)
    with pytest.raises(ValueError):
        led.recent_burn_rate(window_hours=0)
