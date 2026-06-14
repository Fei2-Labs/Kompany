"""Tests for the no-fabrication pre-flight gate (ADR-0008 #3, step 3).

The "6-month" failure mode is the canonical case: an ungrounded duration
holds, the same claim grounded in company state passes. LLM-assisted
extraction is mocked; the regex fallback is exercised by passing engine=None.
"""

from __future__ import annotations

from types import SimpleNamespace

from kompany.core.fabrication_check import FabricationCheck


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


SIX_MONTH = "We will ship the full platform within a 6-month timeline."


# ---------------------------------------------------------------------------
# Regex fallback (engine=None)
# ---------------------------------------------------------------------------


def test_ungrounded_six_month_claim_holds():
    fc = FabricationCheck(engine=None)
    result = fc.check(SIX_MONTH, company_state={"goal": "grow"}, episodes=[])
    assert result.verdict == "hold"
    assert any("6-month" in f for f in result.findings)


def test_grounded_six_month_claim_passes():
    fc = FabricationCheck(engine=None)
    state = {"time_horizon": "a 6-month plan to reach $5k MRR"}
    result = fc.check(SIX_MONTH, company_state=state, episodes=[])
    assert result.verdict == "pass"
    assert result.findings == []


def test_grounded_via_episodes_evidence():
    fc = FabricationCheck(engine=None)
    episodes = [{"summary": "Closed first 6-month enterprise contract."}]
    result = fc.check(SIX_MONTH, company_state={}, episodes=episodes)
    assert result.verdict == "pass"


def test_soft_copy_with_no_hard_claims_passes():
    fc = FabricationCheck(engine=None)
    result = fc.check(
        "We help small teams move faster.", company_state={}, episodes=[]
    )
    assert result.verdict == "pass"


def test_superlative_unsupported_holds():
    fc = FabricationCheck(engine=None)
    result = fc.check(
        "We are the fastest logistics platform in the world.",
        company_state={"goal": "logistics"},
        episodes=[],
    )
    assert result.verdict == "hold"


# ---------------------------------------------------------------------------
# LLM-assisted extraction + fallback
# ---------------------------------------------------------------------------


def test_llm_extraction_used_when_available():
    llm = FakeLLM(text="- 6-month timeline")
    fc = FabricationCheck(FakeEngine(llm))
    result = fc.check(SIX_MONTH, company_state={"goal": "grow"}, episodes=[])
    assert llm.calls == 1
    assert result.verdict == "hold"


def test_llm_failure_falls_back_to_regex():
    llm = FakeLLM(raise_exc=True)
    fc = FabricationCheck(FakeEngine(llm))
    result = fc.check(SIX_MONTH, company_state={"goal": "grow"}, episodes=[])
    # Regex fallback still catches the ungrounded duration.
    assert result.verdict == "hold"
