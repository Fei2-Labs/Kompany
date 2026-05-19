"""Verify the company glossary block is wired into agent prompts.

Glossary-and-drift-detection task 05-19. The engine helper
``_compose_glossary_summary`` is the single seam every prompt path uses;
this file pins three contracts:

1. Empty glossary → empty string (callers can splice unconditionally).
2. Populated glossary → multi-line block that names canonical terms and
   their forbidden synonyms.
3. CEO classify prompt actually splices the block in front of the
   directive (caught by a captured-prompt LLM fake).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from kompany.agents.ceo import CEOAgent, DirectiveClassification
from kompany.llm.client import LLMResponse
from kompany.state.glossary import (
    CompanyGlossary,
    GlossaryEntry,
    GlossaryService,
)


# ---------------------------------------------------------------------------
# Compose-summary unit (no engine)
# ---------------------------------------------------------------------------


def _entry(term: str, synonyms: list[str]) -> GlossaryEntry:
    return GlossaryEntry(
        term=term,
        definition=f"canonical: {term}",
        forbidden_synonyms=synonyms,
        added_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        added_by="founder",
    )


def test_compose_summary_empty_glossary_returns_blank_string() -> None:
    g = CompanyGlossary()
    assert g.compose_summary() == ""


def test_compose_summary_includes_canonical_terms_and_forbidden_synonyms() -> None:
    g = CompanyGlossary()
    g.add(_entry("customer", ["user", "lead"]))
    g.add(_entry("revenue", ["income", "sales"]))
    block = g.compose_summary()
    assert "COMPANY GLOSSARY" in block
    assert "customer" in block
    assert "user" in block
    assert "lead" in block
    assert "revenue" in block
    assert "NOT" in block  # NOT marker for forbidden synonyms


def test_compose_summary_truncates_when_over_cap() -> None:
    g = CompanyGlossary()
    for i in range(25):
        g.add(_entry(f"term{i}", [f"syn{i}"]))
    block = g.compose_summary(max_entries=5)
    # 5 entries rendered + the "more" footer
    lines = block.splitlines()
    assert any("+20 more" in line for line in lines)


# ---------------------------------------------------------------------------
# Engine-level wiring
# ---------------------------------------------------------------------------


def test_service_load_then_compose_summary_round_trip(tmp_path) -> None:
    """End-to-end on the persistence path the engine uses.

    Loads the glossary through the same ``load_from_config`` seam the
    engine's ``_compose_glossary_summary`` calls. Catches regressions
    where the JSON round-trip drops a forbidden synonym before it ever
    reaches the prompt.
    """
    from kompany.state.database import Database
    from kompany.state.glossary import load_from_config

    db = Database(tmp_path)
    svc = GlossaryService(db)
    svc.add(
        term="customer",
        definition="paying account",
        forbidden_synonyms=["user", "lead"],
    )
    glossary = load_from_config(db)
    block = glossary.compose_summary()
    assert "COMPANY GLOSSARY" in block
    assert "customer" in block
    assert "user" in block


# ---------------------------------------------------------------------------
# CEO classify prompt actually splices the block
# ---------------------------------------------------------------------------


class _CapturingLLM:
    """Fake LLM client that records the prompt text without calling out."""

    def __init__(self):
        self.last_prompt: str | None = None

    def call_structured(
        self,
        *,
        model,
        system,
        prompt,
        output_schema,
        agent_name=None,
        max_tokens=4096,
        directive_id=None,
    ) -> LLMResponse:
        self.last_prompt = prompt
        parsed = DirectiveClassification(
            directive_type="informational",
            reasoning="status",
            primary_squad="strategy",
            approval_tier="auto",
        )
        resp = LLMResponse(
            text=parsed.model_dump_json(),
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.0,
            model="claude-test",
        )
        resp.parsed = parsed
        return resp


def _make_ceo(tmp_path) -> tuple[CEOAgent, _CapturingLLM]:
    from kompany.config.settings import KompanySettings

    llm = _CapturingLLM()
    settings = KompanySettings()
    ceo = CEOAgent(
        llm=llm,
        settings=settings,
        company_state={"name": "TestCo", "stage": "solo"},
    )
    return ceo, llm


def test_ceo_classify_splices_glossary_block_into_prompt(tmp_path) -> None:
    ceo, llm = _make_ceo(tmp_path)
    glossary_block = (
        "COMPANY GLOSSARY (use canonical terms, avoid forbidden synonyms):\n"
        "- customer: paying account (NOT user, lead)"
    )
    ceo.classify(
        "What is our balance?",
        glossary_summary=glossary_block,
    )
    assert llm.last_prompt is not None
    assert "COMPANY GLOSSARY" in llm.last_prompt
    assert "customer" in llm.last_prompt
    # Block must come before the directive text so the LLM sees the rules
    # before the user question.
    glossary_pos = llm.last_prompt.find("COMPANY GLOSSARY")
    directive_pos = llm.last_prompt.find("What is our balance?")
    assert glossary_pos != -1 and directive_pos != -1
    assert glossary_pos < directive_pos


def test_ceo_classify_without_glossary_omits_block(tmp_path) -> None:
    ceo, llm = _make_ceo(tmp_path)
    ceo.classify("What is our balance?", glossary_summary=None)
    assert llm.last_prompt is not None
    assert "COMPANY GLOSSARY" not in llm.last_prompt
