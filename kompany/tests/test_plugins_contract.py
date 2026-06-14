"""Smoke tests for the plugin contract surface.

The contract is a stable public API — these tests freeze the surface so
accidental renames / signature drift break CI before they break Pro
plugins in the wild.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

from kompany.plugins import (
    ENTRY_POINT_GROUPS,
    AgentSoul,
    AutonomyTier,
    CostEstimate,
    Integration,
    SideEffect,
    Template,
    Tool,
    ToolContext,
    Workflow,
    __contract_version__,
    discover,
    registered,
)


def test_contract_version_pinned():
    assert __contract_version__ == "1.0.0"


def test_entry_point_groups_are_the_plugin_kinds():
    # ADR-0008 added the outward-executor kind alongside the original five.
    assert ENTRY_POINT_GROUPS == (
        "kompany.workflows",
        "kompany.souls",
        "kompany.integrations",
        "kompany.templates",
        "kompany.tools",
        "kompany.outward",
    )


def test_side_effect_enum_members():
    assert {s.value for s in SideEffect} == {
        "read",
        "write_local",
        "external_action",
        "spend",
    }


def test_autonomy_tier_enum_members():
    assert {t.value for t in AutonomyTier} == {"auto", "approval", "human_only"}


def test_cost_estimate_total():
    ce = CostEstimate(llm_usd=0.10, external_usd=2.50)
    assert ce.total_usd == pytest.approx(2.60)
    assert ce.confidence == 1.0


def test_tool_context_required_fields():
    fields = {f for f in ToolContext.__dataclass_fields__}
    assert fields == {"run_id", "ledger", "audit", "credentials", "settings"}


@pytest.mark.parametrize(
    "abc, abstract_methods",
    [
        (Tool, {"estimate_cost", "execute"}),
        (Workflow, {"estimate_cost"}),
        (Integration, {"tools"}),
    ],
)
def test_abc_abstract_methods(abc, abstract_methods):
    assert abc.__abstractmethods__ == abstract_methods


def test_agent_soul_default_run_raises():
    class MySoul(AgentSoul):
        role = "test_role"

    with pytest.raises(NotImplementedError):
        MySoul().run()


def test_template_has_pro_bundle_fields():
    class T(Template):
        template_id = "x"

    t = T()
    assert t.bundled_workflow_ids == ()
    assert t.enabled_pro_soul_ids == ()
    assert t.required_integration_ids == ()


def test_tool_subclass_can_implement():
    class Inp(BaseModel):
        x: int

    class Out(BaseModel):
        y: int

    class DoublerTool(Tool):
        name = "doubler"
        description = "Multiply x by 2"
        input_schema = Inp
        output_schema = Out
        side_effect = SideEffect.READ
        autonomy_tier = AutonomyTier.AUTO

        def estimate_cost(self, inputs):
            return CostEstimate()

        def execute(self, inputs, ctx):
            return Out(y=inputs.x * 2)

    t = DoublerTool()
    assert t.estimate_cost(Inp(x=5)).total_usd == 0
    assert t.execute(Inp(x=5), ctx=None).y == 10  # type: ignore[arg-type]


def test_discover_returns_all_kinds_even_when_empty():
    result = discover()
    for kind in ("workflow", "soul", "integration", "template", "tool"):
        assert kind in result
        assert isinstance(result[kind], list)


def test_registered_returns_list():
    assert isinstance(registered("workflow"), list)
    assert isinstance(registered("nonexistent_kind"), list)
