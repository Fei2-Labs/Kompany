"""Tests for the de-AI / quality pre-flight gate (ADR-0008 #3, step 2).

Rules layer is pure-code tested. The LLM-judge layer runs against a fake
engine whose ``llm.call`` returns scripted text (or raises, to exercise the
pure-code fallback) — no provider is ever hit.
"""

from __future__ import annotations

from types import SimpleNamespace

from kompany.core.deai_gate import HOLD, PASS, REWRITE, DeAIGate


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeLLM:
    def __init__(self, text: str | None = None, raise_exc: bool = False):
        self._text = text
        self._raise = raise_exc
        self.calls = 0

    def call(self, **kwargs):
        self.calls += 1
        if self._raise:
            raise RuntimeError("provider boom")
        return SimpleNamespace(text=self._text, cost_usd=0.0)


class FakeEngine:
    def __init__(self, llm: FakeLLM):
        self.llm = llm
        self.settings = SimpleNamespace(
            get_model_for_tier=lambda tier: "fake-economy"
        )


CLEAN = "We shipped the export feature. Two customers asked for it last week."
SMELLY = (
    "In today's fast-paced world, we leverage robust, seamless solutions to "
    "delve into a tapestry of growth — it's not a tool, it's a movement."
)


# ---------------------------------------------------------------------------
# Rules layer (pure code, outward=False)
# ---------------------------------------------------------------------------


def test_rules_pass_on_clean_copy():
    result = DeAIGate(engine=None).check(CLEAN, outward=False)
    assert result.verdict == PASS
    assert result.findings == []


def test_rules_catch_known_tells():
    result = DeAIGate(engine=None).check(SMELLY, outward=False)
    assert result.verdict == REWRITE
    joined = " ".join(result.findings).lower()
    assert "leverage" in joined
    assert "delve" in joined
    assert "in today's fast-paced" in joined
    assert any("negation pivot" in f for f in result.findings)


def test_rules_catch_em_dash_overuse():
    text = "A — b — c — d things happen here."
    result = DeAIGate(engine=None).check(text, outward=False)
    assert any("em-dash" in f for f in result.findings)


def test_rules_catch_triad_listicle():
    text = (
        "We plan, debate, and execute. It is faster, cheaper, and simpler "
        "for teams everywhere now."
    )
    result = DeAIGate(engine=None).check(text, outward=False)
    assert any("rule-of-three" in f for f in result.findings)


# ---------------------------------------------------------------------------
# LLM-judge layer (outward=True)
# ---------------------------------------------------------------------------


def test_llm_judge_escalates_to_hold():
    llm = FakeLLM(text="- reads like a brochure\n- no author voice\nHOLD")
    result = DeAIGate(FakeEngine(llm)).check(CLEAN, outward=True)
    assert llm.calls == 1
    assert result.verdict == HOLD
    assert any("LLM-judge" in f for f in result.findings)


def test_llm_judge_pass_keeps_rules_verdict():
    llm = FakeLLM(text="No tells.\nPASS")
    result = DeAIGate(FakeEngine(llm)).check(CLEAN, outward=True)
    assert result.verdict == PASS


def test_llm_judge_cannot_downgrade_rules_rewrite():
    # Rules say REWRITE (smelly); judge says PASS → most-restrictive wins.
    llm = FakeLLM(text="No tells.\nPASS")
    result = DeAIGate(FakeEngine(llm)).check(SMELLY, outward=True)
    assert result.verdict == REWRITE


def test_llm_failure_falls_back_to_rules_verdict():
    llm = FakeLLM(raise_exc=True)
    result = DeAIGate(FakeEngine(llm)).check(SMELLY, outward=True)
    # Rules verdict stands; the gate never crashes.
    assert result.verdict == REWRITE
    assert result.findings  # rules findings preserved


def test_outward_false_skips_llm_even_with_engine():
    llm = FakeLLM(text="HOLD")
    result = DeAIGate(FakeEngine(llm)).check(CLEAN, outward=False)
    assert llm.calls == 0
    assert result.verdict == PASS
