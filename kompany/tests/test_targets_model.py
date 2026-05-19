"""Pydantic-level tests for ``CompanyTargets`` and ``TargetsBundle``.

Covers the four-field schema, ``extra='forbid'`` discipline, and the ISO
8601 ``deadline`` validator's edge cases. Mission-targets task 05-19.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kompany.state.targets import CompanyTargets, TargetsBundle


def test_default_construction_returns_safe_zeros() -> None:
    """A bare ``CompanyTargets()`` is the "nothing set" sentinel."""
    t = CompanyTargets()
    assert t.initial_budget == 0.0
    assert t.revenue_target == 0.0
    assert t.customer_target is None
    assert t.deadline is None
    assert t.source == "founder"


def test_all_fields_round_trip_via_json() -> None:
    """``model_dump_json`` + ``model_validate_json`` is the storage seam."""
    original = CompanyTargets(
        initial_budget=5000.0,
        revenue_target=10000.0,
        customer_target=50,
        deadline="2026-09-30",
        source="agreed",
    )
    parsed = CompanyTargets.model_validate_json(original.model_dump_json())
    assert parsed.initial_budget == 5000.0
    assert parsed.revenue_target == 10000.0
    assert parsed.customer_target == 50
    # The validator round-trips ``2026-09-30`` through ``datetime.fromisoformat``
    # — the canonical form keeps the same date but may add a time component
    # depending on the Python release. Just assert the prefix.
    assert parsed.deadline is not None and parsed.deadline.startswith("2026-09-30")
    assert parsed.source == "agreed"


def test_extra_forbid_rejects_typos() -> None:
    """``extra='forbid'`` is non-negotiable — typos must fail loudly."""
    with pytest.raises(ValidationError):
        CompanyTargets(
            initial_budget=1000.0,
            revenu_target=2000.0,  # typo: revenu instead of revenue
        )


def test_negative_budget_rejected() -> None:
    with pytest.raises(ValidationError):
        CompanyTargets(initial_budget=-1.0)


def test_negative_revenue_rejected() -> None:
    with pytest.raises(ValidationError):
        CompanyTargets(revenue_target=-100.0)


def test_negative_customer_rejected() -> None:
    with pytest.raises(ValidationError):
        CompanyTargets(customer_target=-1)


def test_customer_target_optional_none() -> None:
    """``customer_target=None`` is the canonical "revenue-only" mode."""
    t = CompanyTargets(revenue_target=5000.0, customer_target=None)
    assert t.customer_target is None


def test_deadline_accepts_iso_date() -> None:
    t = CompanyTargets(deadline="2026-08-19")
    assert t.deadline is not None and t.deadline.startswith("2026-08-19")


def test_deadline_accepts_iso_timestamp_with_tz() -> None:
    t = CompanyTargets(deadline="2026-08-19T00:00:00+00:00")
    assert t.deadline is not None


def test_deadline_empty_string_normalises_to_none() -> None:
    """REST/web callers can send '' for "no deadline" without an error."""
    t = CompanyTargets(deadline="")
    assert t.deadline is None


def test_deadline_none_stays_none() -> None:
    t = CompanyTargets(deadline=None)
    assert t.deadline is None


def test_deadline_garbage_rejected() -> None:
    with pytest.raises(ValidationError):
        CompanyTargets(deadline="tomorrow")


def test_deadline_partial_iso_rejected() -> None:
    with pytest.raises(ValidationError):
        CompanyTargets(deadline="2026/09/30")  # slash isn't ISO 8601


def test_source_must_be_one_of_three_states() -> None:
    """``source`` is a Literal — anything outside the three states fails."""
    with pytest.raises(ValidationError):
        CompanyTargets(source="some_other_state")


def test_source_accepts_each_known_state() -> None:
    for src in ("founder", "team_proposal", "agreed"):
        t = CompanyTargets(source=src)
        assert t.source == src


def test_targets_bundle_round_trip() -> None:
    bundle = TargetsBundle(
        founder=CompanyTargets(initial_budget=5000.0, source="founder"),
        proposal=CompanyTargets(initial_budget=5000.0, source="team_proposal"),
        agreed=None,
        review_thread_id="apr_abc123",
    )
    js = bundle.model_dump_json()
    parsed = TargetsBundle.model_validate_json(js)
    assert parsed.founder.initial_budget == 5000.0
    assert parsed.proposal is not None
    assert parsed.agreed is None
    assert parsed.review_thread_id == "apr_abc123"


def test_targets_bundle_forbids_extras() -> None:
    with pytest.raises(ValidationError):
        TargetsBundle(
            founder=CompanyTargets(),
            rogue_field=True,  # type: ignore[call-arg]
        )
