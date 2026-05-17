"""Tests for agent prompt behavior."""

from __future__ import annotations

from kompany.agents.registry import AgentRegistry


class DummySettings:
    def get_model_for_tier(self, tier):
        return tier


class DummyLedger:
    def get_balance(self):
        return 50.0

    def get_totals(self):
        return {}


def test_c_level_prompts_include_soul_context():
    registry = AgentRegistry(None, DummySettings(), DummyLedger())
    company_state = {
        "name": "TestCo",
        "balance": 50.0,
        "active_projects": 0,
        "stage": "solo",
    }

    for role in [
        "ceo",
        "cfo",
        "coo",
        "cos",
        "cto",
        "cpo",
        "csa",
        "ciso",
        "cmo",
        "cro",
        "cv",
    ]:
        prompt = registry.get(role, company_state).system_prompt()
        assert "Agent soul:" in prompt, role
        assert "Tone:" in prompt, role
