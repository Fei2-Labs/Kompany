"""Tests for the CFO agent — mechanical budget operations."""

from __future__ import annotations

import pytest

from kompany.agents.cfo import CFOAgent
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory


@pytest.fixture
def cfo(tmp_path):
    db = Database(tmp_path)
    ledger = Ledger(db)
    ledger.record(amount=50.0, description="Capital", category=LedgerCategory.INCOME)
    return CFOAgent(llm=None, settings=None, ledger=ledger)


def test_check_budget_sufficient(cfo):
    result = cfo.check_budget(30.0)
    assert result["sufficient"] is True
    assert result["shortfall"] == 0


def test_check_budget_insufficient(cfo):
    result = cfo.check_budget(4500.0)
    assert result["sufficient"] is False
    assert result["shortfall"] == 4450.0
    assert result["available"] == 50.0


def test_check_budget_exact(cfo):
    result = cfo.check_budget(50.0)
    assert result["sufficient"] is True
    assert result["shortfall"] == 0


def test_get_balance(cfo):
    assert cfo.get_balance() == 50.0


def test_get_summary(cfo):
    summary = cfo.get_summary()
    assert summary["balance"] == 50.0
    assert summary["total_income"] == 50.0
    assert summary["total_expenses"] == 0
    assert summary["total_ai_costs"] == 0
