"""Tests for per-project budget envelopes (fund / spend / overdraw gate)."""

from __future__ import annotations

import pytest

from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory, Project, ProjectType


def _ledger(tmp_path) -> Ledger:
    db = Database(tmp_path)
    return Ledger(db)


def test_spent_for_project_only_counts_tagged_rows(tmp_path):
    led = _ledger(tmp_path)
    led.record(amount=100.0, description="seed", category=LedgerCategory.INCOME)
    led.record(
        amount=-10.0, description="p1 spend",
        category=LedgerCategory.EXPENSE, project_id="p1",
    )
    led.record(
        amount=-5.0, description="untagged spend",
        category=LedgerCategory.EXPENSE,
    )
    assert led.spent_for_project("p1") == pytest.approx(10.0)
    assert led.spent_for_project("p2") == 0.0


def test_refund_nets_against_project_spend(tmp_path):
    led = _ledger(tmp_path)
    led.record(amount=100.0, description="seed", category=LedgerCategory.INCOME)
    led.record(
        amount=-10.0, description="p1 spend",
        category=LedgerCategory.EXPENSE, project_id="p1",
    )
    led.record(
        amount=4.0, description="p1 partial refund",
        category=LedgerCategory.REFUND, project_id="p1",
    )
    assert led.spent_for_project("p1") == pytest.approx(6.0)


def test_project_income_does_not_refill_envelope(tmp_path):
    led = _ledger(tmp_path)
    led.record(amount=100.0, description="seed", category=LedgerCategory.INCOME)
    led.record(
        amount=-10.0, description="p1 spend",
        category=LedgerCategory.EXPENSE, project_id="p1",
    )
    led.record(
        amount=49.0, description="p1 first sale",
        category=LedgerCategory.INCOME, project_id="p1",
    )
    # Revenue raises the company balance but never shrinks "spent".
    assert led.spent_for_project("p1") == pytest.approx(10.0)
    assert led.get_balance() == pytest.approx(139.0)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    from kompany.core.engine import KompanyEngine

    eng = KompanyEngine()
    eng.ledger.record(
        amount=50.0, description="seed capital",
        category=LedgerCategory.INCOME, approved_by="master",
    )
    project = Project(name="P-A", type=ProjectType.REVENUE, target_amount=100.0)
    eng.projects.create(project)
    return eng, project.id


def test_fund_project_earmarks_without_moving_cash(engine):
    eng, pid = engine
    budget = eng.fund_project(pid, 20.0)
    assert budget["funded"] == pytest.approx(20.0)
    assert budget["remaining"] == pytest.approx(20.0)
    # Earmark is not a transaction: balance untouched.
    assert eng.ledger.get_balance() == pytest.approx(50.0)
    assert eng.unallocated_treasury() == pytest.approx(30.0)


def test_fund_project_rejects_over_allocation(engine):
    eng, pid = engine
    with pytest.raises(ValueError, match="Insufficient unallocated treasury"):
        eng.fund_project(pid, 60.0)


def test_record_project_expense_gates_on_envelope(engine):
    eng, pid = engine
    eng.fund_project(pid, 20.0)
    budget = eng.record_project_expense(pid, 5.0, "ad spend")
    assert budget["remaining"] == pytest.approx(15.0)
    # Spend reduces consolidated balance too.
    assert eng.ledger.get_balance() == pytest.approx(45.0)
    with pytest.raises(ValueError, match="Envelope overdraw"):
        eng.record_project_expense(pid, 15.01, "too much")


def test_envelopes_are_isolated_between_projects(engine):
    eng, pid_a = engine
    project_b = Project(name="P-B", type=ProjectType.REVENUE, target_amount=100.0)
    eng.projects.create(project_b)
    eng.fund_project(pid_a, 10.0)
    eng.fund_project(project_b.id, 30.0)
    eng.record_project_expense(project_b.id, 25.0, "B's big spend")
    # B's spending never eats A's envelope.
    assert eng.project_budget(pid_a)["remaining"] == pytest.approx(10.0)
    assert eng.project_budget(project_b.id)["remaining"] == pytest.approx(5.0)
