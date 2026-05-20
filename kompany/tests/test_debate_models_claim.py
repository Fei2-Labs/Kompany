"""Pydantic-level tests for the evidence-traced debate models.

Task 05-19 evidence-traced-debate, PR1. Covers:

* ``Source`` / ``Claim`` validation.
* ``Claim.is_inferred_only`` semantics across the cardinality matrix.
* ``AgentPosition`` accepts the new ``claims`` field and the legacy
  ``analysis`` field; ``effective_claims`` returns the right shape.
* ``DebateSynthesis`` and ``CEODecision`` analogues for the same.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kompany.core.debate_models import (
    AgentPosition,
    CEODecision,
    Claim,
    DebateRound,
    DebateSynthesis,
    Source,
    SourceType,
)


# ---------------------------------------------------------------------------
# Source / Claim / enum basics
# ---------------------------------------------------------------------------

def test_source_enum_has_six_known_types() -> None:
    assert {st.value for st in SourceType} == {
        "user_input",
        "template_default",
        "ledger_entry",
        "agent_memory",
        "audit_event",
        "inferred",
    }


def test_source_default_ref_and_label_are_empty_strings() -> None:
    s = Source(source_type=SourceType.INFERRED)
    assert s.source_ref == ""
    assert s.claim_supported == ""


def test_source_rejects_unknown_source_type() -> None:
    with pytest.raises(ValidationError):
        Source(source_type="not_a_real_type")  # type: ignore[arg-type]


def test_claim_with_no_evidence_is_inferred_only() -> None:
    c = Claim(text="we'll grow fast")
    assert c.evidence == []
    assert c.is_inferred_only() is True


def test_claim_with_only_inferred_evidence_is_inferred_only() -> None:
    c = Claim(
        text="we'll grow fast",
        evidence=[Source(source_type=SourceType.INFERRED)],
    )
    assert c.is_inferred_only() is True


def test_claim_with_any_concrete_source_is_not_inferred_only() -> None:
    c = Claim(
        text="cash covers 5 days at burn ceiling",
        evidence=[
            Source(source_type=SourceType.LEDGER_ENTRY, source_ref="lg-42"),
            Source(source_type=SourceType.INFERRED, source_ref=""),
        ],
    )
    assert c.is_inferred_only() is False


# ---------------------------------------------------------------------------
# AgentPosition — new claims field + legacy fallback
# ---------------------------------------------------------------------------

def _claim(text: str, *, sourced: bool) -> Claim:
    evidence = (
        [Source(source_type=SourceType.USER_INPUT, source_ref="revenue_target")]
        if sourced
        else []
    )
    return Claim(text=text, evidence=evidence)


def test_agent_position_accepts_claims_only() -> None:
    pos = AgentPosition(
        agent_role="cfo",
        agent_name="CFO",
        squad="finance",
        round=DebateRound.POSITION,
        claims=[_claim("burn covers 5 days", sourced=True)],
        recommendation="hold spend",
        confidence="high",
    )
    assert len(pos.claims) == 1
    assert pos.analysis == ""
    eff = pos.effective_claims()
    assert len(eff) == 1
    assert eff[0].text == "burn covers 5 days"


def test_agent_position_accepts_legacy_analysis_only() -> None:
    pos = AgentPosition(
        agent_role="cfo",
        agent_name="CFO",
        squad="finance",
        round=DebateRound.POSITION,
        analysis="At current cash, runway is 5 days.",
        recommendation="hold spend",
        confidence="high",
    )
    assert pos.claims == []
    eff = pos.effective_claims()
    # Legacy fallback wraps analysis in a single inferred Claim.
    assert len(eff) == 1
    assert eff[0].text == "At current cash, runway is 5 days."
    assert eff[0].is_inferred_only() is True


def test_agent_position_claims_win_when_both_present() -> None:
    pos = AgentPosition(
        agent_role="cfo",
        agent_name="CFO",
        squad="finance",
        round=DebateRound.POSITION,
        claims=[_claim("c1", sourced=True), _claim("c2", sourced=False)],
        analysis="legacy text should be ignored when claims present",
        recommendation="hold spend",
        confidence="high",
    )
    eff = pos.effective_claims()
    assert [c.text for c in eff] == ["c1", "c2"]


def test_agent_position_empty_position_returns_no_claims() -> None:
    pos = AgentPosition(
        agent_role="cfo",
        agent_name="CFO",
        squad="finance",
        round=DebateRound.POSITION,
        recommendation="hold spend",
        confidence="high",
    )
    assert pos.effective_claims() == []


def test_agent_position_roundtrips_via_model_dump() -> None:
    """A model_dump → model_validate roundtrip preserves claims + evidence."""
    pos = AgentPosition(
        agent_role="cfo",
        agent_name="CFO",
        squad="finance",
        round=DebateRound.POSITION,
        claims=[
            Claim(
                text="cash $50 covers 5 days at burn ceiling",
                evidence=[
                    Source(
                        source_type=SourceType.LEDGER_ENTRY,
                        source_ref="ledger.balance",
                        claim_supported="burn-rate",
                    ),
                ],
            ),
        ],
        recommendation="hold spend",
        confidence="high",
    )
    blob = pos.model_dump(mode="json")
    restored = AgentPosition.model_validate(blob)
    assert restored.claims[0].text.startswith("cash $50")
    assert restored.claims[0].evidence[0].source_type == SourceType.LEDGER_ENTRY
    assert restored.claims[0].evidence[0].source_ref == "ledger.balance"


# ---------------------------------------------------------------------------
# DebateSynthesis + CEODecision analogues
# ---------------------------------------------------------------------------

def test_debate_synthesis_accepts_consensus_claims() -> None:
    syn = DebateSynthesis(
        consensus_claims=[_claim("ship by Q3", sourced=True)],
        key_tensions=["cost vs. speed"],
        recommended_option="ship",
        risk_flags=["timeline"],
        decision_required="approve",
    )
    assert syn.consensus_position == ""
    eff = syn.effective_consensus_claims()
    assert len(eff) == 1
    assert eff[0].text == "ship by Q3"


def test_debate_synthesis_falls_back_to_consensus_position() -> None:
    syn = DebateSynthesis(
        consensus_position="ship by Q3",
        key_tensions=["cost vs. speed"],
        recommended_option="ship",
        risk_flags=["timeline"],
        decision_required="approve",
    )
    eff = syn.effective_consensus_claims()
    assert len(eff) == 1
    assert eff[0].text == "ship by Q3"
    assert eff[0].is_inferred_only() is True


def test_ceo_decision_accepts_rationale_claims() -> None:
    dec = CEODecision(
        decision="ship",
        rationale_claims=[_claim("market window closes Q4", sourced=True)],
        tradeoffs_weighed=["cost vs window"],
        next_steps=["execute"],
        confidence_score=0.7,
        reversibility="partially_reversible",
    )
    assert dec.rationale == ""
    assert dec.effective_rationale_claims()[0].text == "market window closes Q4"


def test_ceo_decision_falls_back_to_rationale_string() -> None:
    dec = CEODecision(
        decision="ship",
        rationale="market window closes Q4",
        tradeoffs_weighed=["cost vs window"],
        next_steps=["execute"],
        confidence_score=0.7,
        reversibility="partially_reversible",
    )
    eff = dec.effective_rationale_claims()
    assert len(eff) == 1
    assert eff[0].text == "market window closes Q4"
    assert eff[0].is_inferred_only() is True
