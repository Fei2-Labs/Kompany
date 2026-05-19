"""Tests for the ``state.targets`` service layer.

Covers the get / set / clear roundtrip, the three-state resolution
(``agreed > founder > legacy keys``), and the bundle reader. Mission-
targets task 05-19.
"""

from __future__ import annotations

import pytest

from kompany.state.database import Database
from kompany.state.targets import (
    CompanyTargets,
    clear_targets,
    compose_summary,
    get_bundle,
    get_state,
    get_targets,
    set_review_thread_id,
    set_targets,
)


@pytest.fixture
def db(tmp_path) -> Database:
    """Fresh SQLite store per test — keeps every roundtrip isolated."""
    return Database(tmp_path)


def test_get_targets_on_empty_db_returns_zeros(db: Database) -> None:
    """Fresh installs have no rows → the service returns the safe default."""
    t = get_targets(db)
    assert t.initial_budget == 0.0
    assert t.revenue_target == 0.0
    assert t.customer_target is None
    assert t.deadline is None
    assert t.source == "founder"


def test_set_then_get_founder_state(db: Database) -> None:
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=10000.0,
            customer_target=50,
            deadline="2026-08-19",
            source="founder",
        ),
    )
    t = get_targets(db)
    # No ``agreed`` row → falls back to founder.
    assert t.source == "founder"
    assert t.initial_budget == 5000.0
    assert t.revenue_target == 10000.0
    assert t.customer_target == 50


def test_agreed_state_beats_founder_in_get_targets(db: Database) -> None:
    """The authoritative read prefers ``agreed`` over ``founder``."""
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=10000.0,
            source="founder",
        ),
    )
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=7000.0,
            source="agreed",
        ),
    )
    t = get_targets(db)
    assert t.source == "agreed"
    assert t.revenue_target == 7000.0


def test_proposal_state_does_not_promote_to_authoritative(db: Database) -> None:
    """``team_proposal`` exists for the review trace but is never read by agents."""
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=10000.0,
            source="founder",
        ),
    )
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=6000.0,
            source="team_proposal",
        ),
    )
    t = get_targets(db)
    # Founder wins until founder finalises (agreed is written).
    assert t.source == "founder"
    assert t.revenue_target == 10000.0


def test_get_state_returns_each_state_independently(db: Database) -> None:
    set_targets(db, CompanyTargets(revenue_target=10000.0, source="founder"))
    set_targets(db, CompanyTargets(revenue_target=6000.0, source="team_proposal"))
    set_targets(db, CompanyTargets(revenue_target=7000.0, source="agreed"))
    f = get_state(db, "founder")
    p = get_state(db, "team_proposal")
    a = get_state(db, "agreed")
    assert f is not None and f.revenue_target == 10000.0
    assert p is not None and p.revenue_target == 6000.0
    assert a is not None and a.revenue_target == 7000.0


def test_get_state_returns_none_for_missing(db: Database) -> None:
    assert get_state(db, "team_proposal") is None
    assert get_state(db, "agreed") is None


def test_legacy_initial_budget_key_synthesises_founder(db: Database) -> None:
    """Templates that wrote ``initial_budget`` directly still surface via get."""
    db.execute(
        "INSERT INTO company_config (key, value) VALUES ('initial_budget', '5000.0')"
    )
    db.commit()
    t = get_targets(db)
    assert t.source == "founder"
    assert t.initial_budget == 5000.0


def test_clear_targets_wipes_all_rows(db: Database) -> None:
    set_targets(db, CompanyTargets(initial_budget=5000.0, source="founder"))
    set_targets(
        db, CompanyTargets(initial_budget=5000.0, source="team_proposal")
    )
    set_targets(db, CompanyTargets(initial_budget=5000.0, source="agreed"))
    set_review_thread_id(db, "apr_xyz")
    clear_targets(db)
    bundle = get_bundle(db)
    assert bundle.proposal is None
    assert bundle.agreed is None
    assert bundle.review_thread_id is None


def test_get_bundle_returns_all_three_states_plus_thread(db: Database) -> None:
    set_targets(db, CompanyTargets(revenue_target=10000.0, source="founder"))
    set_targets(
        db, CompanyTargets(revenue_target=6000.0, source="team_proposal")
    )
    set_targets(db, CompanyTargets(revenue_target=7000.0, source="agreed"))
    set_review_thread_id(db, "apr_xyz")
    b = get_bundle(db)
    assert b.founder.revenue_target == 10000.0
    assert b.proposal is not None and b.proposal.revenue_target == 6000.0
    assert b.agreed is not None and b.agreed.revenue_target == 7000.0
    assert b.review_thread_id == "apr_xyz"


def test_set_founder_mirrors_to_legacy_keys(db: Database) -> None:
    """Templates / older readers consult flat keys; the writer keeps them in sync."""
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=10000.0,
            customer_target=50,
            deadline="2026-08-19",
            source="founder",
        ),
    )
    row = db.execute(
        "SELECT value FROM company_config WHERE key = 'initial_budget'"
    ).fetchone()
    assert row["value"] == "5000.0"
    row = db.execute(
        "SELECT value FROM company_config WHERE key = 'revenue_target'"
    ).fetchone()
    assert row["value"] == "10000.0"
    row = db.execute(
        "SELECT value FROM company_config WHERE key = 'customer_target'"
    ).fetchone()
    assert row["value"] == "50"


def test_compose_summary_without_targets_is_terse() -> None:
    out = compose_summary(CompanyTargets())
    assert "none set" in out.lower()


def test_compose_summary_with_revenue_and_deadline() -> None:
    out = compose_summary(
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=10000.0,
            customer_target=50,
            deadline="2099-01-01",
        ),
        cash=4200.0,
    )
    assert "$10,000" in out
    assert "$5,000" in out
    assert "50" in out
    assert "2099-01-01" in out
    assert "4,200" in out
