"""Tests for ``engine.run_target_feasibility_review`` and its three founder paths.

Mission-targets task 05-19. Covers:

* No founder targets → ``run_target_feasibility_review`` returns ``None``.
* Skip-LLM path produces a CFO / CoS / CEO triple and creates one
  ``approval_request(action_type='target_feasibility')``.
* Founder approve → ``agreed`` = recommended_targets.
* Founder reject → ``agreed`` = original_targets.
* Founder revise → successor approval is created with the hint folded in.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    from kompany.core.engine import KompanyEngine

    return KompanyEngine()


def test_review_returns_none_without_founder_targets(engine: Any) -> None:
    """No founder row → no review can be produced."""
    out = engine.run_target_feasibility_review(skip_llm=True)
    assert out is None


def test_review_creates_approval_request_when_founder_set(engine: Any) -> None:
    engine.apply_template("saas-startup")
    out = engine.run_target_feasibility_review(skip_llm=True)
    assert out is not None
    assert out["action_type"] == "target_feasibility"
    payload = out["payload"]
    assert "cfo_view" in payload
    assert "cos_view" in payload
    assert "ceo_proposal" in payload
    assert "original_targets" in payload
    assert "recommended_targets" in payload


def test_review_records_team_proposal_snapshot(engine: Any) -> None:
    """The recommended numbers must mirror to ``targets.team_proposal`` so
    ``kompany target show`` can render them."""
    engine.apply_template("saas-startup")
    engine.run_target_feasibility_review(skip_llm=True)
    bundle = engine.get_targets_bundle()
    assert bundle.proposal is not None
    assert bundle.proposal.source == "team_proposal"


def test_approve_writes_recommended_as_agreed(engine: Any) -> None:
    engine.apply_template(
        "saas-startup",
        # Force the heuristic to compress revenue: requires
        # revenue_target > 5 * initial_budget.
        override_budget=1000.0,
        override_revenue_target=10000.0,
    )
    out = engine.run_target_feasibility_review(skip_llm=True)
    approval_id = out["id"]
    rec_rev = out["payload"]["recommended_targets"]["revenue_target"]
    engine.approve_request(approval_id, approved_by="master")
    bundle = engine.get_targets_bundle()
    assert bundle.agreed is not None
    assert bundle.agreed.source == "agreed"
    assert bundle.agreed.revenue_target == rec_rev


def test_reject_writes_original_as_agreed(engine: Any) -> None:
    engine.apply_template(
        "saas-startup",
        override_budget=1000.0,
        override_revenue_target=10000.0,
    )
    out = engine.run_target_feasibility_review(skip_llm=True)
    approval_id = out["id"]
    original_rev = out["payload"]["original_targets"]["revenue_target"]
    engine.reject_request(approval_id, rejected_by="master")
    bundle = engine.get_targets_bundle()
    assert bundle.agreed is not None
    assert bundle.agreed.revenue_target == original_rev


def test_revise_creates_successor_approval_with_hint(engine: Any) -> None:
    engine.apply_template(
        "saas-startup",
        override_budget=1000.0,
        override_revenue_target=10000.0,
    )
    out = engine.run_target_feasibility_review(skip_llm=True)
    original_id = out["id"]
    revision = engine.request_approval_revision(
        request_id=original_id,
        counter="please try $8000 revenue with a 120-day deadline",
        by_type="user",
    )
    assert revision is not None
    successor = revision["successor"]
    assert successor["action_type"] == "target_feasibility"
    assert successor["predecessor_id"] == original_id
    # Counter hint folded into payload for the founder to act on.
    assert "revision_hint" in (successor.get("payload") or {})


def test_review_mirrors_thread_id_to_company_config(engine: Any) -> None:
    """The review approval id surfaces in ``targets.review_thread_id``."""
    engine.apply_template("saas-startup")
    out = engine.run_target_feasibility_review(skip_llm=True)
    bundle = engine.get_targets_bundle()
    assert bundle.review_thread_id == out["id"]


def test_audit_event_recorded(engine: Any) -> None:
    """One ``company.target_feasibility_requested`` event must land."""
    engine.apply_template("saas-startup")
    engine.run_target_feasibility_review(skip_llm=True)
    rows = engine.db.execute(
        "SELECT event_type FROM audit_log "
        "WHERE event_type = 'company.target_feasibility_requested'"
    ).fetchall()
    assert len(rows) == 1


def test_finalize_audit_event_on_approve(engine: Any) -> None:
    """``company.targets_agreed`` lands when the founder approves."""
    engine.apply_template("saas-startup")
    out = engine.run_target_feasibility_review(skip_llm=True)
    engine.approve_request(out["id"], approved_by="master")
    rows = engine.db.execute(
        "SELECT detail FROM audit_log "
        "WHERE event_type = 'company.targets_agreed'"
    ).fetchall()
    assert len(rows) == 1
