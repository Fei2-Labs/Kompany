"""Legacy debate-record fallback tests.

Task 05-19 evidence-traced-debate, PR1. Verifies that legacy debate JSON
blobs (those written before the claims-schema migration) round-trip
through the new Pydantic models without error and surface via the
``effective_*`` read paths.

These payloads were captured from real pre-migration ``debates.rounds_json``
columns: only the deprecated string fields are populated.
"""

from __future__ import annotations

import json

from kompany.core.debate_models import (
    AgentPosition,
    CEODecision,
    DebateSynthesis,
)


LEGACY_AGENT_POSITION_JSON = json.dumps({
    "agent_role": "cfo",
    "agent_name": "CFO",
    "squad": "finance",
    "round": "position",
    "analysis": "At $50 cash with burn ~$10/day, we have ~5 days runway.",
    "recommendation": "hold marketing spend",
    "confidence": "high",
    "concessions": [],
    "hard_lines": [],
})

LEGACY_SYNTHESIS_JSON = json.dumps({
    "consensus_position": "ship in Q3 with reduced scope",
    "key_tensions": ["cost vs. window"],
    "recommended_option": "ship reduced",
    "risk_flags": ["timeline"],
    "decision_required": "approve",
})

LEGACY_DECISION_JSON = json.dumps({
    "decision": "ship",
    "rationale": "market window closes Q4; cost is acceptable.",
    "tradeoffs_weighed": ["cost vs. window"],
    "overrides": [],
    "next_steps": ["execute"],
    "confidence_score": 0.8,
    "reversibility": "easily_reversible",
})


def test_legacy_agent_position_parses() -> None:
    """An old AgentPosition row (no ``claims`` field) parses cleanly."""
    pos = AgentPosition.model_validate_json(LEGACY_AGENT_POSITION_JSON)
    assert pos.claims == []
    assert pos.analysis.startswith("At $50 cash")


def test_legacy_agent_position_effective_claims_falls_back() -> None:
    """Read path: effective_claims wraps legacy analysis in an inferred Claim."""
    pos = AgentPosition.model_validate_json(LEGACY_AGENT_POSITION_JSON)
    eff = pos.effective_claims()
    assert len(eff) == 1
    assert eff[0].text.startswith("At $50 cash")
    assert eff[0].is_inferred_only() is True


def test_legacy_synthesis_parses_and_falls_back() -> None:
    syn = DebateSynthesis.model_validate_json(LEGACY_SYNTHESIS_JSON)
    assert syn.consensus_claims == []
    assert syn.consensus_position.startswith("ship in Q3")
    eff = syn.effective_consensus_claims()
    assert len(eff) == 1
    assert eff[0].text == "ship in Q3 with reduced scope"


def test_legacy_decision_parses_and_falls_back() -> None:
    dec = CEODecision.model_validate_json(LEGACY_DECISION_JSON)
    assert dec.rationale_claims == []
    assert dec.rationale.startswith("market window")
    eff = dec.effective_rationale_claims()
    assert len(eff) == 1
    assert eff[0].text.startswith("market window")


def test_legacy_blob_through_debates_table_path(tmp_path) -> None:
    """End-to-end: persist a debate row whose AgentPosition has only the
    legacy ``analysis`` field, then read it back and exercise the
    fallback path.
    """
    from kompany.state.database import Database
    from kompany.state.debates import Debates

    db = Database(tmp_path)
    debates = Debates(db)

    legacy_pos = AgentPosition.model_validate_json(LEGACY_AGENT_POSITION_JSON)
    legacy_syn = DebateSynthesis.model_validate_json(LEGACY_SYNTHESIS_JSON)
    legacy_dec = CEODecision.model_validate_json(LEGACY_DECISION_JSON)

    debate_id = debates.record(
        rounds=[[legacy_pos]],
        synthesis=legacy_syn,
        decision=legacy_dec,
        directive_id="dir-legacy",
        project_id="proj-legacy",
    )

    row = debates.get(debate_id)
    assert row is not None
    pos_blob = row["rounds"][0][0]
    # On the stored blob the claims list is present (default empty) and
    # the analysis text round-tripped.
    assert pos_blob.get("claims", []) == []
    assert pos_blob["analysis"].startswith("At $50 cash")

    # Re-validate the blob back into AgentPosition and confirm the
    # effective_claims fallback still works.
    restored = AgentPosition.model_validate(pos_blob)
    eff = restored.effective_claims()
    assert len(eff) == 1
    assert eff[0].is_inferred_only() is True
