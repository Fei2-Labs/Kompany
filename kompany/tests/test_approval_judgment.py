"""Judgment-assisted auto-approve (ADR-0011) — the seam's first consumer.

Covers ``core/approval_judgment.py`` directly (unit) and its wiring into
``FounderSurfacesMixin._auto_approve_eligible`` (integration). The
central invariant under test: a judgment provider can only SUBTRACT
eligibility a deterministic ``tool_authorization`` policy already
granted — it can never grant eligibility the policy withheld, and with
no provider installed the policy's own answer is unchanged.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from kompany.core.approval_judgment import judgment_confirms_auto_approve
from kompany.core.engine import KompanyEngine
from kompany.core.judgment import Judgment, JudgmentProvider
from kompany.plugins.contract import AutonomyTier, CostEstimate, Integration, SideEffect, Tool


class _In(BaseModel):
    text: str = "x"


class _Out(BaseModel):
    ok: bool = True
    detail: str = ""


class _SendTool(Tool):
    name = "test.send"
    description = "external action"
    input_schema = _In
    output_schema = _Out
    side_effect = SideEffect.EXTERNAL_ACTION
    autonomy_tier = AutonomyTier.APPROVAL
    calls: list[str] = []

    def estimate_cost(self, inputs):
        return CostEstimate()

    def execute(self, inputs, ctx):
        _SendTool.calls.append(inputs.text)
        return _Out(detail=f"sent:{inputs.text}")


class _FakeIntegration(Integration):
    integration_id = "test_integ"
    display_name = "Test Integration"
    required_credentials = ()

    def tools(self):
        return [_SendTool()]


@pytest.fixture()
def engine(monkeypatch):
    monkeypatch.setattr(
        "kompany.plugins.loader.discover",
        lambda: {"integration": [_FakeIntegration()]},
    )
    _SendTool.calls = []
    return KompanyEngine()


class _FixedProvider(JudgmentProvider):
    """A judgment provider that always answers the same boolean value."""

    provider_id = "test.fixed"
    is_external = False

    def __init__(self, value: float, confidence: float):
        self._value = value
        self._confidence = confidence

    def judge(self, state, questions):
        return {
            key: Judgment(kind=q.kind, value=self._value, confidence=self._confidence)
            for key, q in questions.items()
        }


class _FakeEngine:
    def __init__(self, provider=None):
        self.judgment_provider = provider


# --- unit: judgment_confirms_auto_approve -----------------------------------


def _call(engine=None):
    return judgment_confirms_auto_approve(
        engine,
        tool_name="test.send",
        tool_description="external action",
        side_effect="external_action",
        estimated_cost_usd=0.0,
        requested_by="linkedin_growth",
    )


def test_no_provider_leaves_policy_standing():
    # No engine at all — resolves to the abstaining default.
    assert _call(engine=None) is True


def test_low_confidence_hold_signal_does_not_override():
    engine = _FakeEngine(_FixedProvider(value=0.9, confidence=0.3))
    assert _call(engine) is True


def test_low_probability_does_not_hold_even_with_confidence():
    engine = _FakeEngine(_FixedProvider(value=0.2, confidence=0.9))
    assert _call(engine) is True


def test_confident_hold_signal_overrides_policy():
    engine = _FakeEngine(_FixedProvider(value=0.9, confidence=0.9))
    assert _call(engine) is False


def test_borderline_values_do_not_trip_the_threshold():
    # Both bars must clear; sitting exactly on one is not enough on its own
    # to accidentally interrupt the founder.
    just_under_prob = _FakeEngine(_FixedProvider(value=0.64, confidence=0.9))
    just_under_conf = _FakeEngine(_FixedProvider(value=0.9, confidence=0.59))
    assert _call(just_under_prob) is True
    assert _call(just_under_conf) is True


# --- integration: the auto-approve path -------------------------------------


def test_auto_approve_unaffected_with_no_provider_installed(engine):
    """The default state of every install: wiring is inert."""
    engine.set_tool_policy("linkedin_growth", "test.send", allowed=True, requires_approval=False)

    card = engine.propose_action(
        "test.send", {"text": "auto"}, summary="Send", requested_by="linkedin_growth"
    )

    assert card["status"] == "approved"
    assert _SendTool.calls == ["auto"]


def test_judgment_can_turn_an_eligible_policy_into_a_hold(engine):
    engine.set_tool_policy("linkedin_growth", "test.send", allowed=True, requires_approval=False)
    engine.judgment_provider = _FixedProvider(value=0.9, confidence=0.9)

    card = engine.propose_action(
        "test.send", {"text": "held"}, summary="Send", requested_by="linkedin_growth"
    )

    assert card["status"] == "pending"
    assert _SendTool.calls == []


def test_providers_are_discovered_once_at_boot_not_per_decision(engine):
    """``_auto_approve_eligible`` runs on every proposed action; it must
    not pay a fresh entry-point scan each time. The engine caches the
    list at boot, same as ``outward_executors``."""
    assert engine.judgment_providers == []

    # Count the expensive part — the entry-point scan itself, not the
    # cheap wrapper that reads the boot cache.
    scans: list[str] = []
    import kompany.plugins.loader as loader_mod

    original = loader_mod.registered

    def _counting(kind, data_dir=None):
        scans.append(kind)
        return original(kind, data_dir)

    loader_mod.registered = _counting
    try:
        engine.set_tool_policy(
            "linkedin_growth", "test.send", allowed=True, requires_approval=False
        )
        for i in range(3):
            engine.propose_action(
                "test.send",
                {"text": f"n{i}"},
                summary="Send",
                requested_by="linkedin_growth",
            )
    finally:
        loader_mod.registered = original

    # The cached [] is consulted; the judgment scan never reruns. (The
    # ``integration`` rescans in this list are the tool registry's own
    # pre-existing behaviour, untouched by ADR-0011.)
    assert "judgment" not in scans
    assert len(_SendTool.calls) == 3


def test_judgment_cannot_grant_eligibility_policy_withheld(engine):
    """A confident 'let it through' signal from judgment is irrelevant —
    there is no policy allowing this tool at all, so it stays pending."""
    engine.judgment_provider = _FixedProvider(value=0.0, confidence=0.99)

    card = engine.propose_action(
        "test.send", {"text": "no policy"}, summary="Send", requested_by="linkedin_growth"
    )

    assert card["status"] == "pending"
    assert _SendTool.calls == []
