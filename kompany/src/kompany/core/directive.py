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
