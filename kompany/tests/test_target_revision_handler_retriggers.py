"""Tests that ``_target_feasibility_revision_handler`` actually re-runs the trio.

Earlier this handler stamped the founder's hint into a successor
approval payload's ``revision_hint`` field and did nothing else — the
agents never saw the counter-proposal. PR2 of the feasibility-debate
task (05-19) flips that: the handler must call
``run_target_feasibility_review(revision_hint=hint, prior_rounds=...)``
so the agents re-debate with the counter injected into their prompts.

These tests confirm the new behaviour by:

* Stubbing the registry so every agent call is recorded.
* Triggering a revise after an initial review.
* Asserting that a fresh batch of CFO/CoS/CEO calls happened and that
  every replay prompt carries the founder's counter-text + the prior
  round's claims.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kompany.core.debate_models import Claim, ClaimList, Source, SourceType


@pytest.fixture(autouse=True)
def _llm_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KOMPANY_TEST_MODE", raising=False)


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    from kompany.core.engine import KompanyEngine

    return KompanyEngine()


def _make_resp(role: str, suffix: str = "") -> Any:
    label = f"{role.upper()}_CLAIM{suffix}"
    claim = Claim(
        text=label,
        evidence=[
            Source(
                source_type=SourceType.USER_INPUT,
                source_ref="test",
                claim_supported="test",
            )
        ],
    )
    return SimpleNamespace(
        text=label,
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        model="claude-sonnet-4-20250514",
        parsed=ClaimList(claims=[claim]),
    )


class _RecordingRegistry:
    def __init__(self) -> None:
        self.log: dict[str, list[dict[str, Any]]] = {"cfo": [], "cos": [], "ceo": []}

    def get(self, role: str, company_state: Any = None) -> Any:
        log = self.log

        class _Agent:
            display_name = role.upper()

            def call_structured(
                self,
                prompt: str,
                output_schema: Any,
                directive_id: str | None = None,
                max_tokens: int = 4096,
                action_type: str | None = None,
            ) -> Any:
                idx = len(log[role])
                log[role].append({
                    "prompt": prompt,
                    "action_type": action_type,
                })
                return _make_resp(role, suffix=f"_R{idx}")

        return _Agent()


def test_revise_actually_calls_llm_again(engine: Any) -> None:
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    # Initial review: one full trio.
    out = engine.run_target_feasibility_review(skip_llm=False)
    assert out is not None
    initial_id = out["id"]
    assert len(reg.log["cfo"]) == 1
    assert len(reg.log["cos"]) == 1
    assert len(reg.log["ceo"]) == 1

    # Founder counter-proposes.
    revision = engine.request_approval_revision(
        request_id=initial_id,
        counter="I have outside income; treat $50 as project budget only",
        by_type="user",
    )
    assert revision is not None

    # Each agent must have been called a second time — proving the
    # handler actually re-ran the trio rather than just stamping
    # metadata.
    assert len(reg.log["cfo"]) == 2
    assert len(reg.log["cos"]) == 2
    assert len(reg.log["ceo"]) == 2


def test_revise_prompts_carry_founder_counter_text(engine: Any) -> None:
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    out = engine.run_target_feasibility_review(skip_llm=False)
    counter = "treat $50 as project budget only, keep 12 week deadline"
    engine.request_approval_revision(
        request_id=out["id"], counter=counter, by_type="user"
    )

    # Look at the *second* call to each role (the revise pass).
    for role in ("cfo", "cos", "ceo"):
        revise_prompt = reg.log[role][1]["prompt"]
        assert counter in revise_prompt, (
            f"{role} revise prompt missing founder counter text"
        )


def test_revise_prompts_carry_prior_round_claims(engine: Any) -> None:
    """Round 2 prompts must include round 1's claim texts."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    initial = engine.run_target_feasibility_review(skip_llm=False)
    # Round 1 produced labels like CFO_CLAIM_R0, COS_CLAIM_R0, CEO_CLAIM_R0.

    engine.request_approval_revision(
        request_id=initial["id"],
        counter="please reconsider",
        by_type="user",
    )

    # Round 2 prompts. Each prior_rounds section should mention the
    # prior-round claim labels.
    for role in ("cfo", "cos", "ceo"):
        revise_prompt = reg.log[role][1]["prompt"]
        assert "Prior debate rounds" in revise_prompt
        assert "CFO_CLAIM_R0" in revise_prompt
        assert "COS_CLAIM_R0" in revise_prompt
        assert "CEO_CLAIM_R0" in revise_prompt


def test_revise_creates_successor_with_predecessor_link(engine: Any) -> None:
    """Successor approval must link back via ``predecessor_id``."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    out = engine.run_target_feasibility_review(skip_llm=False)
    initial_id = out["id"]

    revision = engine.request_approval_revision(
        request_id=initial_id,
        counter="please reconsider",
        by_type="user",
    )
    successor = revision["successor"]
    assert successor["predecessor_id"] == initial_id
    assert successor["action_type"] == "target_feasibility"
    # Successor must carry the new rounds array.
    rounds = successor["payload"].get("rounds")
    assert isinstance(rounds, list)
    assert len(rounds) == 2
    assert rounds[1]["generation"] == 2
    assert rounds[1]["revision_hint"] == "please reconsider"


def test_revise_summary_is_marked_revised(engine: Any) -> None:
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    out = engine.run_target_feasibility_review(skip_llm=False)
    revision = engine.request_approval_revision(
        request_id=out["id"],
        counter="please reconsider",
        by_type="user",
    )
    assert revision["successor"]["summary"].startswith("[Revised]")


def test_revise_action_type_label_switches_to_feasibility_revise(engine: Any) -> None:
    """Cost-tracking discipline: revise calls tag spend differently."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    out = engine.run_target_feasibility_review(skip_llm=False)
    engine.request_approval_revision(
        request_id=out["id"], counter="please reconsider", by_type="user"
    )

    # First pass should be 'target_feasibility', second pass should be
    # 'feasibility_revise'.
    for role in ("cfo", "cos", "ceo"):
        assert reg.log[role][0]["action_type"] == "target_feasibility"
        assert reg.log[role][1]["action_type"] == "feasibility_revise"
