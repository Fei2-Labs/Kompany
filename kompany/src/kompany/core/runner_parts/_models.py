"""Pydantic models shared between runner.py and runner_parts — extracted per ADR-0003."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskSpec(BaseModel):
    """A single task specification.

    ``budget_cap_usd``/``max_turns`` are CEO-assigned per-task caps
    (PRD 06-11 D3); ``None`` falls back to the harness defaults via
    ``resolve_caps`` at task-creation time.
    """
    title: str
    assigned_agent: str
    prompt: str
    budget_cap_usd: float | None = None
    max_turns: int | None = None


class TaskDecomposition(BaseModel):
    """CEO's breakdown of a revenue path into executable tasks."""
    tasks: list[TaskSpec] = Field(default_factory=list)


class ProjectRunResult(BaseModel):
    """Result of running a project."""
    project_id: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_ai_cost: float = 0.0
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    fully_funded: bool = False
