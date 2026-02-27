"""Pydantic models for all persistent state objects."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _short_id() -> str:
    return uuid4().hex[:8]


class LedgerCategory(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"
    AI_COST = "ai_cost"
    ALLOCATION = "allocation"
    REFUND = "refund"


class LedgerEntry(BaseModel):
    id: int | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    amount: float  # positive = income, negative = expense
    balance_after: float = 0.0
    description: str
    category: LedgerCategory
    directive_id: str | None = None
    project_id: str | None = None
    approved_by: str | None = None  # "auto" | "ceo" | "master"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class ProjectType(str, Enum):
    REVENUE = "revenue"
    OPERATIONAL = "operational"
    STRATEGIC = "strategic"


class Project(BaseModel):
    id: str = Field(default_factory=_short_id)
    name: str
    type: ProjectType
    status: ProjectStatus = ProjectStatus.ACTIVE
    target_amount: float | None = None
    funded_amount: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    triggers_directive_id: str | None = None
    plan: dict[str, Any] = Field(default_factory=dict)
    assigned_agents: list[str] = Field(default_factory=list)


class TaskStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    id: str = Field(default_factory=_short_id)
    project_id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    parent_task_id: str | None = None


class Decision(BaseModel):
    id: str = Field(default_factory=_short_id)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    directive_id: str
    directive_type: str
    raw_input: str
    classification: dict[str, Any]
    result: dict[str, Any]
    agents_involved: list[str]
    total_ai_cost: float = 0.0
    duration_seconds: float | None = None


class CompanySnapshot(BaseModel):
    """Current state of the company for agent context."""
    name: str
    product: str
    stage: str
    balance: float
    active_project_count: int
    total_revenue: float = 0.0
    total_expenses: float = 0.0
    total_ai_costs: float = 0.0
