"""Tests for the company ledger — balance tracking, AI costs, totals."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory


@pytest.fixture
def ledger(tmp_path):
    db = Database(tmp_path)
    return Ledger(db)


def test_initial_balance_is_zero(ledger):
    assert ledger.get_balance() == 0.0


def test_record_income(ledger):
    entry = ledger.record(
        amount=50.0,
        description="Initial capital",
        category=LedgerCategory.INCOME,
        approved_by="master",
    )
    assert entry.amount == 50.0
    assert entry.balance_after == 50.0
    assert ledger.get_balance() == 50.0


def test_record_expense_reduces_balance(ledger):
    ledger.record(amount=100.0, description="Capital", category=LedgerCategory.INCOME)
    ledger.record(amount=-30.0, description="Purchase", category=LedgerCategory.EXPENSE)
    assert ledger.get_balance() == 70.0


def test_balance_can_go_negative(ledger):
    """Core principle: balance can go negative — mission persists."""
    ledger.record(amount=10.0, description="Capital", category=LedgerCategory.INCOME)
    ledger.record(amount=-25.0, description="Big expense", category=LedgerCategory.EXPENSE)
    assert ledger.get_balance() == -15.0


def test_record_ai_cost(ledger):
    ledger.record(amount=50.0, description="Capital", category=LedgerCategory.INCOME)
    entry = ledger.record_ai_cost(amount_usd=0.03, description="CEO classify")
    assert entry.amount == -0.03
    assert entry.category == LedgerCategory.AI_COST
    assert entry.description == "AI: CEO classify"
    assert ledger.get_balance() == pytest.approx(49.97)


def test_ai_cost_always_negative(ledger):
    """record_ai_cost should negate positive amounts."""
    ledger.record(amount=10.0, description="Capital", category=LedgerCategory.INCOME)
    entry = ledger.record_ai_cost(amount_usd=0.05, description="test")
    assert entry.amount < 0


def test_multiple_ai_costs_accumulate(ledger):
    ledger.record(amount=50.0, description="Capital", category=LedgerCategory.INCOME)
    ledger.record_ai_cost(amount_usd=0.03, description="CEO classify")
    ledger.record_ai_cost(amount_usd=0.15, description="CEO revenue plan")
    ledger.record_ai_cost(amount_usd=0.05, description="CTO research")
    assert ledger.get_balance() == pytest.approx(49.77)


def test_get_totals(ledger):
    ledger.record(amount=100.0, description="Capital", category=LedgerCategory.INCOME)
    ledger.record(amount=-20.0, description="Purchase", category=LedgerCategory.EXPENSE)
    ledger.record_ai_cost(amount_usd=0.10, description="Agent call")
    totals = ledger.get_totals()
    assert totals["income"] == 100.0
    assert totals["expense"] == -20.0
    assert totals["ai_cost"] == pytest.approx(-0.10)


def test_get_recent(ledger):
    ledger.record(amount=50.0, description="First", category=LedgerCategory.INCOME)
    ledger.record(amount=-10.0, description="Second", category=LedgerCategory.EXPENSE)
    ledger.record(amount=-5.0, description="Third", category=LedgerCategory.EXPENSE)
    recent = ledger.get_recent(limit=2)
    assert len(recent) == 2
    assert recent[0]["description"] == "Third"
    assert recent[1]["description"] == "Second"
