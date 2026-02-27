"""Tests for KompanyEngine — integration tests for the directive flow."""

from __future__ import annotations

import pytest

from kompany.core.engine import KompanyEngine
from kompany.state.models import LedgerCategory


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Create an engine with a temp data dir and no API key needed for mechanical tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))

    class TestSettings:
        company_name = "TestCo"
        company_product = "AI tools"
        company_stage = "solo"
        data_dir = tmp_path
        anthropic_api_key = "test-key"
        openai_api_key = ""
        gemini_api_key = ""
        glm_api_key = ""
        kimi_api_key = ""
        custom_api_key = ""
        custom_base_url = ""
        currency = "EUR"
        model_apex = "claude-opus-4-20250514"
        model_primary = "claude-sonnet-4-20250514"
        model_economy = "claude-haiku-4-20250414"

        def get_model_for_tier(self, tier):
            return {
                "apex": self.model_apex,
                "primary": self.model_primary,
                "economy": self.model_economy,
            }.get(tier, self.model_primary)

        def get_api_key_for_provider(self, provider):
            return {
                "anthropic": self.anthropic_api_key,
                "openai": self.openai_api_key,
                "gemini": self.gemini_api_key,
                "glm": self.glm_api_key,
                "kimi": self.kimi_api_key,
                "custom": self.custom_api_key,
            }.get(provider, "")

    from kompany.state.database import Database
    from kompany.state.ledger import Ledger
    from kompany.state.journal import Journal
    from kompany.state.projects import Projects
    from kompany.llm.cost_tracker import CostTracker
    from kompany.agents.registry import AgentRegistry

    settings = TestSettings()
    db = Database(tmp_path)
    ledger = Ledger(db)
    journal = Journal(db)
    projects = Projects(db)
    cost_tracker = CostTracker(ledger)

    engine = KompanyEngine.__new__(KompanyEngine)
    engine.settings = settings
    engine.db = db
    engine.ledger = ledger
    engine.journal = journal
    engine.projects = projects
    engine.cost_tracker = cost_tracker
    engine.autonomy = __import__(
        "kompany.core.autonomy", fromlist=["AutonomyGate"]
    ).AutonomyGate()
    engine.llm = None
    engine.registry = AgentRegistry(None, settings, ledger)

    return engine


def test_initialize_company(engine):
    engine.initialize_company(
        name="TestCo", product="AI tools", balance=50.0, stage="solo"
    )
    assert engine.ledger.get_balance() == 50.0


def test_initialize_company_zero_balance(engine):
    engine.initialize_company(
        name="TestCo", product="AI tools", balance=0.0, stage="solo"
    )
    assert engine.ledger.get_balance() == 0.0


def test_get_company_state(engine):
    engine.initialize_company(
        name="TestCo", product="AI tools", balance=100.0
    )
    state = engine.get_company_state()
    assert state["name"] == "TestCo"
    assert state["balance"] == 100.0
    assert state["active_projects"] == 0


def test_informational_directive(engine):
    """Informational directives should work without LLM calls."""
    engine.initialize_company(
        name="TestCo", product="AI tools", balance=50.0
    )
    result = engine._handle_informational(
        directive=__import__(
            "kompany.core.directive", fromlist=["Directive"]
        ).Directive(raw_input="What's our balance?"),
        classification=None,
        ceo=None,
    )
    assert result.status == "completed"
    assert "50.00" in result.message
    assert result.total_ai_cost == 0
