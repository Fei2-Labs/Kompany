"""Debate protocol — structured multi-agent debate for strategic decisions.

Evidence-traced debate (task 05-19): every agent claim is a discrete
``Claim`` with an explicit ``evidence: list[Source]`` list. Free-text
``analysis`` is kept as a deprecated read-side fallback so legacy debate
records and LLM responses that don't yet honour the new contract still
render.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DebateRound(str, Enum):
    POSITION = "position"
    REBUTTAL = "rebuttal"
    CONVERGENCE = "convergence"


class SourceType(str, Enum):
    """Closed enum of provenance kinds. Extending this is a schema change.

    A free-form string was rejected so distillation / UI / audit code can
    pattern-match on a known vocabulary. ``inferred`` is the explicit
    "no concrete source" marker — a claim with only ``inferred`` evidence
    (or empty evidence) is flagged in the UI and refused by distillation.
    """

    USER_INPUT = "user_input"
    TEMPLATE_DEFAULT = "template_default"
    LEDGER_ENTRY = "ledger_entry"
    AGENT_MEMORY = "agent_memory"
    AUDIT_EVENT = "audit_event"
    INFERRED = "inferred"


class Source(BaseModel):
    """One provenance hint attached to a :class:`Claim`.

    ``source_ref`` is intentionally a free string (e.g. a ledger entry id,
    a user_input field name, a memory id, an audit event id). We don't
    validate that the referenced row exists — DB integrity is the
    database's job; ``Source`` is a 1-hop hint for the UI / audit trail.
    """

    source_type: SourceType
    source_ref: str = Field(
        default="",
        description="Entry id / field name / memory id. Empty when source_type=inferred.",
    )
    claim_supported: str = Field(
        default="",
        description="Short label naming which part of the claim this source backs "
        "(e.g. 'burn-rate', 'target-ratio'). Free-form.",
    )


class Claim(BaseModel):
    """One factual statement made by an agent, with optional evidence.

    Cardinality rules (enforced by downstream filters, not by the schema):

    * ``evidence`` MAY be empty — that marks the claim as inferred-only.
    * ``evidence`` MAY contain any number of :class:`Source` entries.
    * A claim is *sourced* iff at least one ``Source`` has
      ``source_type != SourceType.INFERRED``. Otherwise it is *inferred-only*.

    Distillation refuses to write inferred-only claims into
    ``agent_memories``; the UI flags them with an amber ``⚠`` marker.
    """

    text: str = Field(
        description="One factual statement. Keep it atomic — split compound "
        "claims into multiple Claim entries so each can be cited independently.",
    )
    evidence: list[Source] = Field(default_factory=list)

    def is_inferred_only(self) -> bool:
        """True iff the claim has no non-inferred source."""
        if not self.evidence:
            return True
        return all(s.source_type == SourceType.INFERRED for s in self.evidence)


def _claims_to_analysis(claims: list[Claim]) -> str:
    """Join claim texts into a single string for legacy ``analysis`` readers."""
    return " ".join(c.text.strip() for c in claims if c.text.strip())


def _legacy_claim_from_analysis(analysis: str) -> Claim:
    """Wrap a legacy free-text ``analysis`` blob into one inferred Claim.

    Used by read-path fallback when an old debate record (claims=[] but
    analysis non-empty) needs to be rendered through the new pipeline.
    """
    return Claim(
        text=analysis,
        evidence=[Source(source_type=SourceType.INFERRED, source_ref="", claim_supported="legacy_analysis")],
    )


class AgentPosition(BaseModel):
    """An agent's position in a debate round.

    The canonical field is ``claims: list[Claim]``. ``analysis: str`` is
    kept as a **deprecated** read-side compatibility field — see the
    module docstring. New code MUST read ``claims``; ``analysis`` is
    populated from ``claims`` when claims are present and is otherwise
    treated as a single legacy inferred claim by callers via
    :meth:`effective_claims`.
    """

    agent_role: str
    agent_name: str
    squad: str
    round: DebateRound
    claims: list[Claim] = Field(default_factory=list)
    analysis: str = Field(
        default="",
        description="DEPRECATED. Pre-claim free-text analysis. Kept for "
        "backward compatibility with stored debate rounds and LLM outputs "
        "that don't yet honour the structured-claims schema. Will be "
        "removed in the next debate-schema migration.",
    )
    recommendation: str
    confidence: str = Field(description="low|medium|high")
    concessions: list[str] = Field(default_factory=list)
    hard_lines: list[str] = Field(default_factory=list)

    def effective_claims(self) -> list[Claim]:
        """Return ``claims`` if non-empty, else a single legacy claim from ``analysis``.

        This is the read path every downstream consumer (UI, distillation,
        synthesis) should use so legacy records keep rendering.
        """
        if self.claims:
            return list(self.claims)
        if self.analysis.strip():
            return [_legacy_claim_from_analysis(self.analysis)]
        return []


class DebateSynthesis(BaseModel):
    """CoS synthesis of the debate.

    ``consensus_claims`` is the canonical evidence-traced form;
    ``consensus_position`` is kept as a deprecated free-text mirror
    (auto-populated from claim texts when claims are present).
    """

    consensus_claims: list[Claim] = Field(default_factory=list)
    consensus_position: str = Field(
        default="",
        description="DEPRECATED. Free-text consensus paragraph. Mirrors "
        "``consensus_claims`` for legacy readers.",
    )
    key_tensions: list[str]
    recommended_option: str
    risk_flags: list[str]
    decision_required: str

    def effective_consensus_claims(self) -> list[Claim]:
        if self.consensus_claims:
            return list(self.consensus_claims)
        if self.consensus_position.strip():
            return [_legacy_claim_from_analysis(self.consensus_position)]
        return []


class CEODecision(BaseModel):
    """CEO's final decision after debate.

    ``rationale_claims`` is the canonical form; ``rationale`` stays as a
    deprecated mirror so legacy CEO decision rows keep reading.
    ``decision`` itself is **not** structured — it's the headline string.
    """

    decision: str
    rationale_claims: list[Claim] = Field(default_factory=list)
    rationale: str = Field(
        default="",
        description="DEPRECATED. Free-text rationale paragraph. Mirrors "
        "``rationale_claims`` for legacy readers.",
    )
    tradeoffs_weighed: list[str]
    overrides: list[str] = Field(default_factory=list)
    next_steps: list[str]
    confidence_score: float = Field(ge=0.0, le=1.0)
    reversibility: str = Field(description="easily_reversible|partially_reversible|irreversible")

    def effective_rationale_claims(self) -> list[Claim]:
        if self.rationale_claims:
            return list(self.rationale_claims)
        if self.rationale.strip():
            return [_legacy_claim_from_analysis(self.rationale)]
        return []


class DebateResult(BaseModel):
    """Full result of a debate."""
    question: str
    rounds: list[list[AgentPosition]]
    synthesis: DebateSynthesis | None = None
    decision: CEODecision | None = None
    total_ai_cost: float = 0.0
    agents_participated: list[str] = Field(default_factory=list)


__all__ = [
    "AgentPosition",
    "CEODecision",
    "Claim",
    "DebateResult",
    "DebateRound",
    "DebateSynthesis",
    "Source",
    "SourceType",
]
