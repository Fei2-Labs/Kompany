"""Tests for SoulAgent (YAML-driven AgentSoul runtime)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kompany.agents.soul_agent import (
    SoulAgent,
    SoulInvalid,
    SoulNotFound,
    _build_system_prompt,
)


class DummySettings:
    def get_model_for_tier(self, tier):
        return tier


class DummyLLM:
    pass


@pytest.fixture
def saas_compliance_soul(tmp_path: Path) -> Path:
    path = tmp_path / "saas_compliance_officer.yaml"
    path.write_text(
        """
role: saas_compliance_officer
display_name: SaaS Compliance Officer
tier: c_level
squad: governance
model_tier: primary

personality:
  tone: cautious, precise
  decision_style: risk-averse, evidence-led
  risk_tolerance: low
  communication: structured, citation-heavy
  priorities:
    - PCI scope minimization
    - SOC2 audit readiness

traits:
  - Cites prior incidents before approving novel actions
  - Refuses to ship if logging would lose PII

debate_behavior:
  participates: true
  style: Evidence-first, will block on missing audit trail

allowed_tools:
  - stripe.*
  - audit.*
  - "!*.spend"
""".strip()
    )
    return path


def test_soul_agent_from_yaml_path(saas_compliance_soul):
    agent = SoulAgent(saas_compliance_soul, llm=DummyLLM(), settings=DummySettings())
    assert agent.role == "saas_compliance_officer"
    assert agent.display_name == "SaaS Compliance Officer"
    assert agent.squad == "governance"
    assert agent.tier == "c_level"
    assert agent.model_tier == "primary"


def test_soul_agent_system_prompt_includes_persona(saas_compliance_soul):
    agent = SoulAgent(saas_compliance_soul, llm=DummyLLM(), settings=DummySettings())
    sp = agent.system_prompt()
    assert "SaaS Compliance Officer" in sp
    assert "governance squad" in sp
    assert "cautious, precise" in sp
    assert "PCI scope minimization" in sp
    assert "Cites prior incidents" in sp
    assert "Debate style:" in sp


def test_soul_agent_allowed_tools(saas_compliance_soul):
    agent = SoulAgent(saas_compliance_soul, llm=DummyLLM(), settings=DummySettings())
    assert agent.allowed_tools == ("stripe.*", "audit.*", "!*.spend")


def test_soul_agent_from_agent_soul_instance(saas_compliance_soul):
    from kompany.plugins.contract import AgentSoul

    class MySoul(AgentSoul):
        role = "saas_compliance_officer"
        soul_yaml = saas_compliance_soul

    agent = SoulAgent(MySoul(), llm=DummyLLM(), settings=DummySettings())
    assert agent.role == "saas_compliance_officer"


def test_soul_agent_from_dict():
    soul = {"role": "x_role", "display_name": "X"}
    agent = SoulAgent(soul, llm=DummyLLM(), settings=DummySettings())
    assert agent.role == "x_role"
    assert agent.display_name == "X"


def test_missing_yaml_raises():
    with pytest.raises(SoulNotFound):
        SoulAgent("/nonexistent/path.yaml", llm=DummyLLM(), settings=DummySettings())


def test_yaml_missing_required_fields(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("personality:\n  tone: x\n")
    with pytest.raises(SoulInvalid, match="missing required field 'role'"):
        SoulAgent(path, llm=DummyLLM(), settings=DummySettings())


def test_yaml_collides_with_core_role(tmp_path: Path):
    path = tmp_path / "fake_ceo.yaml"
    path.write_text("role: ceo\ndisplay_name: Fake CEO\n")
    with pytest.raises(SoulInvalid, match="collides with a Core"):
        SoulAgent(path, llm=DummyLLM(), settings=DummySettings())


def test_yaml_must_be_mapping(tmp_path: Path):
    path = tmp_path / "scalar.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(SoulInvalid, match="must be a mapping"):
        SoulAgent(path, llm=DummyLLM(), settings=DummySettings())


def test_subagent_tier_phrasing():
    soul = {
        "role": "ecom_inventory_manager",
        "display_name": "Ecom Inventory Manager",
        "tier": "subagent",
    }
    sp = _build_system_prompt(soul)
    assert "subagent" in sp.lower()


def test_non_participating_debate_phrasing():
    soul = {
        "role": "test_role",
        "display_name": "Test Role",
        "debate_behavior": {"participates": False},
    }
    sp = _build_system_prompt(soul)
    assert "do NOT participate" in sp


def test_invalid_source_type_raises():
    with pytest.raises(TypeError):
        SoulAgent(12345, llm=DummyLLM(), settings=DummySettings())  # type: ignore[arg-type]
