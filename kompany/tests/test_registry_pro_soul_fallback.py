"""Tests for the AgentRegistry Pro-soul fallback path.

Core ships 16 roles; any other role must resolve to a discovered
``AgentSoul`` plugin wrapped in :class:`SoulAgent`. These tests lock that
fallback without requiring a real LLM client (SoulAgent construction from
an AgentSoul plugin instance is the seam; the LLM is only used on
``.call()``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from kompany.agents.registry import AgentRegistry
from kompany.agents.soul_agent import SoulAgent
from kompany.plugins.contract import AgentSoul


def test_core_role_still_resolves():
    """The 16 hardcoded Core roles must keep working — the fallback only
    fires for unknown roles."""
    reg = AgentRegistry(llm=MagicMock(), settings=MagicMock(), ledger=MagicMock())
    agent = reg.get("ceo")
    assert agent is not None
    # Cached on second get.
    assert reg.get("ceo") is agent


def test_unknown_role_without_pro_soul_raises():
    reg = AgentRegistry(llm=MagicMock(), settings=MagicMock(), ledger=MagicMock())
    try:
        reg.get("nonexistent_role_xyz")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "nonexistent_role_xyz" in str(exc)


def test_pro_soul_role_resolves_to_soul_agent(monkeypatch, tmp_path):
    """A role that matches a discovered AgentSoul plugin wraps it in
    SoulAgent instead of raising."""
    reg = AgentRegistry(llm=MagicMock(), settings=MagicMock(), ledger=MagicMock())

    # A minimal soul yaml the SoulAgent loader can parse.
    yaml_file = tmp_path / "soul.yaml"
    yaml_file.write_text(
        "role: linkedin_growth\n"
        "display_name: LinkedIn Growth\n"
        "tier: subagent\n"
        "model_tier: primary\n"
    )

    class _FakeSoul(AgentSoul):
        role = "linkedin_growth"
        display_name = "LinkedIn Growth"
        tier = "subagent"
        soul_yaml = yaml_file

    monkeypatch.setattr(
        "kompany.plugins.loader.registered",
        lambda kind: [_FakeSoul()] if kind == "soul" else [],
    )
    agent = reg.get("linkedin_growth")
    assert isinstance(agent, SoulAgent)
    assert agent.role == "linkedin_growth"


def test_pro_soul_lookup_is_best_effort(monkeypatch):
    """A broken plugin scan must not block registry lookups — the fallback
    returns None and the caller raises the normal ValueError."""
    reg = AgentRegistry(llm=MagicMock(), settings=MagicMock(), ledger=MagicMock())

    def boom(kind):
        raise RuntimeError("plugin scan exploded")

    monkeypatch.setattr("kompany.plugins.loader.registered", boom)
    try:
        reg.get("broken_plugin_role")
        assert False, "expected ValueError"
    except ValueError:
        pass  # correct — broken scan degrades to "unknown role"
