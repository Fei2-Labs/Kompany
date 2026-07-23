"""Directive model — the fundamental input to Kompany."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def _short_id() -> str:
    return uuid4().hex[:8]


class DirectiveType(str, Enum):
    ACQUISITION = "acquisition"
    STRATEGIC = "strategic"
    OPERATIONAL = "operational"
    INFORMATIONAL = "informational"


class DecisionType(str, Enum):
    """Kind of strategic decision a debate is about (ADR-0006).

    Drives ``DECISION_TYPE_ROLES`` in :mod:`kompany.core.debate_prep` —
    which executives get convened into a debate — and lets pre-debate
    knowledge retrieval scope precedents. ``general`` is the safe default
    when the CEO classifier cannot pin a more specific type.
    """
    GO_TO_MARKET = "go_to_market"
    PRICING = "pricing"
    HIRING = "hiring"
    INFRA = "infra"
    LEGAL = "legal"
    FUNDRAISING = "fundraising"
    PRODUCT_SCOPE = "product_scope"
    GENERAL = "general"


class DirectiveStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"


class Directive(BaseModel):
    """A directive from the Master to Kompany."""
    id: str = Field(default_factory=_short_id)
    raw_input: str
    directive_type: DirectiveType | None = None
    status: DirectiveStatus = DirectiveStatus.PENDING
    budget_required: float | None = None
    budget_available: float | None = None
    assigned_squad: str | None = None
    assigned_agents: list[str] = Field(default_factory=list)
    requires_approval: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DirectiveResult(BaseModel):
    """Result of processing a directive."""
    directive: Directive
    status: str
    message: str
    project_id: str | None = None
    approval_id: str | None = None
    debate_id: str | None = None
    total_ai_cost: float = 0.0
    agents_used: list[str] = Field(default_factory=list)
    # The ``run_id`` of the ``run_scope`` this directive executed under.
    # Populated by ``process_directive``; lets callers (CEO channel, SSE
    # clients) scope ``llm.spend`` / ``agent.activity`` events to this run.
    run_id: str | None = None
    # CEO-channel session this directive belongs to (06-03-ceo-channel).
    # Populated by ``process_directive`` whether the session was opened by
    # this call or continued. ``status="clarify"`` results carry the question
    # in ``message`` and the session_id here so the founder's reply continues
    # the same session.
    session_id: str | None = None
    active_agent_id: str = "ceo"
    previous_agent_id: str | None = None
    handoff_id: str | None = None
    conversation_continues: bool = False
    delegation_id: str | None = None
    delegation_status: str | None = None

    def to_dict(self) -> dict[str, object]:
        """The shared parity dict every non-web interface serializes.

        Single source of truth for the directive-result wire shape: REST
        (``/directive`` + ``/channel/*``), CLI, MCP, and SDK all flatten a
        ``DirectiveResult`` through this method so their top-level keys stay
        identical (interfaces.md equivalence rule, enforced by
        ``test_interfaces.py``). Internal objects such as ``directive`` and
        ``debate_id`` are intentionally excluded. Drift here is a bug across
        four surfaces at once, so they must not hand-roll their own dicts.
        """
        return {
            "status": self.status,
            "message": self.message,
            "project_id": self.project_id,
            "approval_id": self.approval_id,
            "total_ai_cost": self.total_ai_cost,
            "agents_used": self.agents_used,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "active_agent_id": self.active_agent_id,
            "previous_agent_id": self.previous_agent_id,
            "handoff_id": self.handoff_id,
            "conversation_continues": self.conversation_continues,
            "delegation_id": self.delegation_id,
            "delegation_status": self.delegation_status,
        }
