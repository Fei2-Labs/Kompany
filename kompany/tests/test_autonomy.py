"""Tests for the autonomy gate."""

from __future__ import annotations

from kompany.core.autonomy import AutonomyGate


def test_master_tier_always_requires_approval():
    gate = AutonomyGate()
    assert gate.check("master", 1.0) is False
    assert gate.check("master", 0.0) is False


def test_auto_tier_within_threshold():
    gate = AutonomyGate()
    assert gate.check("auto", 5.0) is True


def test_auto_tier_exceeds_threshold():
    gate = AutonomyGate()
    assert gate.check("auto", 5.01) is False


def test_auto_tier_missing_cost_is_allowed():
    gate = AutonomyGate()
    assert gate.check("auto", None) is True


def test_ceo_tier_within_threshold():
    gate = AutonomyGate()
    assert gate.check("ceo", 30.0) is True


def test_auto_threshold_is_independent_from_ceo_threshold():
    gate = AutonomyGate()
    gate.thresholds["ceo"] = 100.0
    assert gate.check("auto", 5.01) is False


def test_ceo_tier_exceeds_threshold():
    gate = AutonomyGate()
    assert gate.check("ceo", 100.0) is False


def test_unknown_tier_denied():
    gate = AutonomyGate()
    assert gate.check("unknown", 5.0) is False
