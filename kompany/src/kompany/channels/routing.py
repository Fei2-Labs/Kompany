"""Deterministic project and agent routing decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal, Protocol

from kompany.agents.registry import AgentRegistry
from kompany.state.models import Project


ProjectRouteStatus = Literal["resolved", "ambiguous", "unresolved"]


@dataclass(frozen=True, slots=True)
class ProjectRouteDecision:
    status: ProjectRouteStatus
    project_id: str | None
    candidate_project_ids: tuple[str, ...]
    confidence: float
    reason: str
    requires_confirmation: bool


RouteAction = Literal[
    "continue",
    "handoff",
    "delegate",
    "clarify",
    "return_to_ceo",
]


class RouteClassification(Protocol):
    route: str
    primary_squad: str
    agents_needed: list[str]
    approval_tier: str


@dataclass(frozen=True, slots=True)
class AgentRouteDecision:
    action: RouteAction
    active_agent_id: str
    destination_agent_ids: tuple[str, ...]
    confidence: float
    reason: str
    requires_confirmation: bool
    shadow: bool = True


def _mentions(message: str, value: str) -> bool:
    if not value.strip():
        return False
    return re.search(
        rf"(?<!\w){re.escape(value.strip())}(?!\w)",
        message,
        flags=re.IGNORECASE,
    ) is not None


def resolve_project(
    message: str,
    projects: Iterable[Project],
    *,
    explicit_project_id: str | None = None,
) -> ProjectRouteDecision:
    """Resolve an explicit or named project without model inference."""
    available = list(projects)
    if explicit_project_id is not None:
        selected = next(
            (project for project in available if project.id == explicit_project_id),
            None,
        )
        if selected is not None:
            return ProjectRouteDecision(
                status="resolved",
                project_id=selected.id,
                candidate_project_ids=(selected.id,),
                confidence=1.0,
                reason="explicit_project_id",
                requires_confirmation=False,
            )
        return ProjectRouteDecision(
            status="unresolved",
            project_id=None,
            candidate_project_ids=(),
            confidence=1.0,
            reason="explicit_project_not_found",
            requires_confirmation=True,
        )

    matches = [
        project
        for project in available
        if _mentions(message, project.name) or _mentions(message, project.id)
    ]
    if len(matches) == 1:
        return ProjectRouteDecision(
            status="resolved",
            project_id=matches[0].id,
            candidate_project_ids=(matches[0].id,),
            confidence=0.95,
            reason="project_named_in_message",
            requires_confirmation=False,
        )
    if len(matches) > 1:
        return ProjectRouteDecision(
            status="ambiguous",
            project_id=None,
            candidate_project_ids=tuple(project.id for project in matches),
            confidence=1.0,
            reason="multiple_projects_named",
            requires_confirmation=True,
        )
    return ProjectRouteDecision(
        status="unresolved",
        project_id=None,
        candidate_project_ids=(),
        confidence=0.0,
        reason="no_project_signal",
        requires_confirmation=False,
    )


def plan_agent_route(
    classification: RouteClassification,
    registry: AgentRegistry,
    *,
    active_agent_id: str,
) -> AgentRouteDecision:
    """Translate a CEO classification into a policy-filtered shadow route."""

    def descriptor_for(role: str):
        descriptor_method = getattr(registry, "descriptor", None)
        if descriptor_method is not None:
            return descriptor_method(role)
        return AgentRegistry.descriptor(registry, role)

    if classification.route == "clarify":
        return AgentRouteDecision(
            action="clarify",
            active_agent_id=active_agent_id,
            destination_agent_ids=(),
            confidence=1.0,
            reason="classification_requires_clarification",
            requires_confirmation=True,
        )

    destinations: list[str] = []
    has_worker = False
    for role in classification.agents_needed:
        if role == active_agent_id or role in destinations:
            continue
        try:
            descriptor = descriptor_for(role)
        except ValueError:
            continue
        destinations.append(role)
        has_worker = has_worker or not descriptor.can_own_conversation

    requires_confirmation = classification.approval_tier != "auto"
    if len(destinations) > 1 or has_worker:
        return AgentRouteDecision(
            action="delegate",
            active_agent_id=active_agent_id,
            destination_agent_ids=tuple(destinations),
            confidence=0.9,
            reason="multiple_or_worker_agents_required",
            requires_confirmation=requires_confirmation,
        )
    if len(destinations) == 1:
        return AgentRouteDecision(
            action="handoff",
            active_agent_id=active_agent_id,
            destination_agent_ids=(destinations[0],),
            confidence=0.9,
            reason="single_conversation_specialist_required",
            requires_confirmation=requires_confirmation,
        )

    active = descriptor_for(active_agent_id)
    if (
        active_agent_id != "ceo"
        and classification.primary_squad != active.squad
    ):
        return AgentRouteDecision(
            action="return_to_ceo",
            active_agent_id=active_agent_id,
            destination_agent_ids=("ceo",),
            confidence=0.9,
            reason="intent_moved_outside_active_agent_squad",
            requires_confirmation=False,
        )
    return AgentRouteDecision(
        action="continue",
        active_agent_id=active_agent_id,
        destination_agent_ids=(),
        confidence=0.8,
        reason="active_agent_remains_suitable",
        requires_confirmation=False,
    )
