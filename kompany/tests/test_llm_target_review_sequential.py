"""Sequential context-passing tests for ``_llm_target_review``.

Feasibility review debate task (05-19). PR1 acceptance criterion: the
CFO -> CoS -> CEO calls must be sequential and each later agent's
prompt must carry the earlier agents' claim text so the team actually
debates rather than vote in isolation.

The tests stub ``BaseAgent.call_structured`` with a per-role recorder
that captures every prompt verbatim, so we can assert against the
exact strings.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kompany.core.debate_models import Claim, ClaimList, Source, SourceType


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    # We deliberately keep KOMPANY_TEST_MODE *off* — we want the LLM
    # path to run so we can verify the sequential prompts.
    monkeypatch.delenv("KOMPANY_TEST_MODE", raising=False)


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    from kompany.core.engine import KompanyEngine

    return KompanyEngine()


def _make_resp(claim_texts: list[str]) -> Any:
    """Build a fake LLMResponse-shaped object carrying parsed ClaimList."""
    claims = [
        Claim(
            text=t,
            evidence=[
                Source(
                    source_type=SourceType.USER_INPUT,
                    source_ref="test",
                    claim_supported="test",
                )
            ],
        )
        for t in claim_texts
    ]
    return SimpleNamespace(
        text="; ".join(claim_texts),
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        model="claude-sonnet-4-20250514",
        parsed=ClaimList(claims=claims),
    )


def _install_recording_registry(engine: Any) -> dict[str, list[dict[str, Any]]]:
    """Replace the engine's registry with a fake that records every call.

    Returns the shared call-log dict keyed by role.
    """
    log: dict[str, list[dict[str, Any]]] = {"cfo": [], "cos": [], "ceo": []}

    class _Agent:
        def __init__(self, role: str) -> None:
            self.role = role
            self.display_name = role.upper()

        def call_structured(
            self,
            prompt: str,
            output_schema: Any,
            directive_id: str | None = None,
            max_tokens: int = 4096,
            action_type: str | None = None,
        ) -> Any:
            log[self.role].append(
                {
                    "prompt": prompt,
                    "action_type": action_type,
                    "output_schema": output_schema,
                    "max_tokens": max_tokens,
                }
            )
            if self.role == "cfo":
                return _make_resp([
                    "CFO_CLAIM_BURN_CEILING",
                    "CFO_CLAIM_RUNWAY_WEEKS",
                ])
            if self.role == "cos":
                return _make_resp([
                    "COS_CLAIM_BANDWIDTH",
                    "COS_CLAIM_DEADLINE_EXTEND",
                ])
            return _make_resp([
                "CEO_CLAIM_COMPROMISE",
                "CEO_CLAIM_ADOPT_CFO",
            ])

    class _Registry:
        def get(self, role: str, company_state: Any = None) -> Any:
            return _Agent(role)

    engine.registry = _Registry()
    return log


# ---------------------------------------------------------------------------
# Sequential-context tests
# ---------------------------------------------------------------------------


def test_review_calls_cfo_then_cos_then_ceo_each_once(engine: Any) -> None:
    log = _install_recording_registry(engine)
    engine.apply_template("saas-startup")

    engine.run_target_feasibility_review(skip_llm=False)

    assert len(log["cfo"]) == 1, "CFO should be called exactly once"
    assert len(log["cos"]) == 1, "CoS should be called exactly once"
    assert len(log["ceo"]) == 1, "CEO should be called exactly once"


def test_cos_prompt_contains_cfo_claims(engine: Any) -> None:
    """CoS must see CFO's claim texts in its prompt."""
    log = _install_recording_registry(engine)
    engine.apply_template("saas-startup")

    engine.run_target_feasibility_review(skip_llm=False)

    cos_prompt = log["cos"][0]["prompt"]
    assert "CFO_CLAIM_BURN_CEILING" in cos_prompt
    assert "CFO_CLAIM_RUNWAY_WEEKS" in cos_prompt
    # And the prompt must label the block as the CFO's turn.
    assert "CFO" in cos_prompt
    # CoS must NOT see CEO claims (CEO hasn't spoken yet).
    assert "CEO_CLAIM" not in cos_prompt


def test_ceo_prompt_contains_both_cfo_and_cos_claims(engine: Any) -> None:
    """CEO synthesises so it must see both peer agents' claims."""
    log = _install_recording_registry(engine)
    engine.apply_template("saas-startup")

    engine.run_target_feasibility_review(skip_llm=False)

    ceo_prompt = log["ceo"][0]["prompt"]
    assert "CFO_CLAIM_BURN_CEILING" in ceo_prompt
    assert "CFO_CLAIM_RUNWAY_WEEKS" in ceo_prompt
    assert "COS_CLAIM_BANDWIDTH" in ceo_prompt
    assert "COS_CLAIM_DEADLINE_EXTEND" in ceo_prompt


def test_cfo_prompt_is_isolated_no_peer_context(engine: Any) -> None:
    """CFO speaks first — its prompt must NOT contain peer claims."""
    log = _install_recording_registry(engine)
    engine.apply_template("saas-startup")

    engine.run_target_feasibility_review(skip_llm=False)

    cfo_prompt = log["cfo"][0]["prompt"]
    # No reference to CoS or CEO claim placeholders.
    assert "COS_CLAIM" not in cfo_prompt
    assert "CEO_CLAIM" not in cfo_prompt
    # No "Peers have just spoken" preamble in CFO's prompt.
    assert "Peers have just spoken" not in cfo_prompt


def test_review_returns_three_claim_lists_plus_rationale(engine: Any) -> None:
    """Public API shape: tuple[list[Claim], list[Claim], list[Claim], str]."""
    _install_recording_registry(engine)
    engine.apply_template("saas-startup")
    founder = engine.get_targets_bundle().founder
    assert founder is not None
    recommended = engine._heuristic_recommend(founder, cash=founder.initial_budget)

    out = engine._llm_target_review(
        founder, cash=founder.initial_budget, recommended=recommended
    )
    assert isinstance(out, tuple)
    assert len(out) == 4
    cfo_claims, cos_claims, ceo_claims, rationale = out
    assert all(isinstance(c, Claim) for c in cfo_claims)
    assert all(isinstance(c, Claim) for c in cos_claims)
    assert all(isinstance(c, Claim) for c in ceo_claims)
    assert isinstance(rationale, str)


def test_payload_carries_rounds_array_after_first_review(engine: Any) -> None:
    """``payload['rounds']`` must contain exactly one round on first review."""
    _install_recording_registry(engine)
    engine.apply_template("saas-startup")
    out = engine.run_target_feasibility_review(skip_llm=False)
    assert out is not None
    rounds = out["payload"].get("rounds")
    assert isinstance(rounds, list)
    assert len(rounds) == 1
    assert rounds[0]["generation"] == 1
    assert rounds[0]["revision_hint"] is None
    assert rounds[0]["ceo_only"] is False
    assert isinstance(rounds[0]["cfo_claims"], list)
    assert isinstance(rounds[0]["cos_claims"], list)
    assert isinstance(rounds[0]["ceo_claims"], list)


def test_action_type_label_is_target_feasibility_initially(engine: Any) -> None:
    """Initial review uses action_type='target_feasibility' (not _revise)."""
    log = _install_recording_registry(engine)
    engine.apply_template("saas-startup")
    engine.run_target_feasibility_review(skip_llm=False)
    for role in ("cfo", "cos", "ceo"):
        assert log[role][0]["action_type"] == "target_feasibility", (
            f"{role} should tag spend as target_feasibility"
        )
