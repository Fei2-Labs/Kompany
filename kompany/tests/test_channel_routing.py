from __future__ import annotations

from kompany.agents.ceo import DirectiveClassification
from kompany.agents.registry import AgentRegistry
from kompany.channels.routing import plan_agent_route, resolve_project
from kompany.state.models import Project, ProjectType


def test_agent_registry_exposes_public_capability_descriptors():
    registry = AgentRegistry(None, None, None)

    descriptor = registry.descriptor("cmo")

    assert descriptor.role == "cmo"
    assert descriptor.squad == "growth"
    assert {"marketing", "content", "campaigns"} <= set(
        descriptor.capabilities
    )
    assert registry.candidates_for({"campaigns"}) == ["cmo"]


def test_project_resolver_marks_multiple_named_projects_as_ambiguous():
    projects = [
        Project(id="vinted", name="Vinted", type=ProjectType.REVENUE),
        Project(id="depop", name="Depop", type=ProjectType.REVENUE),
    ]

    decision = resolve_project(
        "Compare the Vinted and Depop campaigns",
        projects,
    )

    assert decision.status == "ambiguous"
    assert decision.project_id is None
    assert decision.candidate_project_ids == ("vinted", "depop")
    assert decision.requires_confirmation is True


def test_agent_router_plans_single_specialist_handoff_in_shadow_mode():
    classification = DirectiveClassification(
        directive_type="informational",
        reasoning="marketing campaign question",
        primary_squad="growth",
        agents_needed=["cmo"],
        approval_tier="auto",
        route="answer",
    )

    decision = plan_agent_route(
        classification,
        AgentRegistry(None, None, None),
        active_agent_id="ceo",
    )

    assert decision.action == "handoff"
    assert decision.destination_agent_ids == ("cmo",)
    assert decision.shadow is True
    assert decision.requires_confirmation is False


def test_agent_router_returns_out_of_scope_specialist_to_ceo():
    classification = DirectiveClassification(
        directive_type="informational",
        reasoning="finance question",
        primary_squad="finance",
        agents_needed=[],
        approval_tier="auto",
        route="answer",
    )

    decision = plan_agent_route(
        classification,
        AgentRegistry(None, None, None),
        active_agent_id="cmo",
    )

    assert decision.action == "return_to_ceo"
    assert decision.destination_agent_ids == ("ceo",)
    assert decision.confidence >= 0.85
