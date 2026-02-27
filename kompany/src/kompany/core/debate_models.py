"""Debate protocol — structured multi-agent debate for strategic decisions."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DebateRound(str, Enum):
    POSITION = "position"
    REBUTTAL = "rebuttal"
    CONVERGENCE = "convergence"


class AgentPosition(BaseModel):
    """An agent's position in a debate round."""
    agent_role: str
    agent_name: str
    squad: str
    round: DebateRound
    analysis: str
    recommendation: str
    confidence: str = Field(description="low|medium|high")
    concessions: list[str] = Field(default_factory=list)
    hard_lines: list[str] = Field(default_factory=list)


class DebateSynthesis(BaseModel):
    """CoS synthesis of the debate."""
    consensus_position: str
    key_tensions: list[str]
    recommended_option: str
    risk_flags: list[str]
    decision_required: str


class CEODecision(BaseModel):
    """CEO's final decision after debate."""
    decision: str
    rationale: str
    tradeoffs_weighed: list[str]
    overrides: list[str] = Field(default_factory=list)
    next_steps: list[str]
    confidence_score: float = Field(ge=0.0, le=1.0)
    reversibility: str = Field(description="easily_reversible|partially_reversible|irreversible")


class DebateResult(BaseModel):
    """Full result of a debate."""
    question: str
    rounds: list[list[AgentPosition]]
    synthesis: DebateSynthesis | None = None
    decision: CEODecision | None = None
    total_ai_cost: float = 0.0
    agents_participated: list[str] = Field(default_factory=list)
