"""Iteration-cap tests for the feasibility-review chain.

PR3 of the feasibility-review-debate task (05-19): once the founder
has revised 3 times (i.e. the chain holds rounds 1-3 with a full
trio + rebuttal), the 4th revise must drop to a CEO-only response —
CFO and CoS claims get frozen at round 3 to keep input-token cost
bounded.

Tests:
* Chain of three successive revises still calls the full trio.
* The fourth revise calls CEO once and CFO/CoS zero times.
* Frozen CFO/CoS claims in the round-4 payload match round-3 values.
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


def _make_resp(label: str) -> Any:
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
                return _make_resp(f"{role.upper()}_GEN{idx + 1}")

        return _Agent()


def test_first_three_revises_still_call_full_trio(engine: Any) -> None:
    """Rounds 1-3 do the full trio + rebuttal."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    current = engine.run_target_feasibility_review(skip_llm=False)
    # Initial review = round 1. Then revise twice to reach round 3.
    for _ in range(2):
        revision = engine.request_approval_revision(
            request_id=current["id"], counter="please reconsider", by_type="user"
        )
        current = revision["successor"]

    # Rounds 1, 2, 3 all ran the full trio.
    assert len(reg.log["cfo"]) == 3
    assert len(reg.log["cos"]) == 3
    assert len(reg.log["ceo"]) == 3


def test_fourth_revise_is_ceo_only(engine: Any) -> None:
    """The 4th approval (generation == 4) calls CEO once, CFO/CoS zero times."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    current = engine.run_target_feasibility_review(skip_llm=False)
    # Three revises: rounds 2, 3, 4.
    for _ in range(3):
        revision = engine.request_approval_revision(
            request_id=current["id"], counter="please reconsider", by_type="user"
        )
        current = revision["successor"]

    # Initial + 2 trio revises = 3 trio rounds. Round 4 should be CEO-only.
    assert len(reg.log["cfo"]) == 3
    assert len(reg.log["cos"]) == 3
    assert len(reg.log["ceo"]) == 4  # +1 for the CEO-only round 4.

    # And the payload of the latest approval must mark itself ceo_only.
    assert current["payload"]["ceo_only"] is True
    assert current["payload"]["generation"] == 4


def test_round_four_freezes_cfo_cos_claims_from_round_three(engine: Any) -> None:
    """CFO/CoS arrays in round 4 must be copies of round 3 — not new LLM output."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    current = engine.run_target_feasibility_review(skip_llm=False)
    payloads = [current["payload"]]
    for _ in range(3):
        revision = engine.request_approval_revision(
            request_id=current["id"], counter="please reconsider", by_type="user"
        )
        current = revision["successor"]
        payloads.append(current["payload"])

    round3 = payloads[2]["rounds"][-1]
    round4 = payloads[3]["rounds"][-1]
    assert round4["generation"] == 4
    assert round4["ceo_only"] is True

    # Frozen CFO/CoS claim texts in round 4 == round 3.
    def _texts(claims: list[dict]) -> list[str]:
        return [c.get("text", "") for c in claims]

    assert _texts(round4["cfo_claims"]) == _texts(round3["cfo_claims"])
    assert _texts(round4["cos_claims"]) == _texts(round3["cos_claims"])
    # CEO claims in round 4 are new (CEO-only LLM output).
    assert _texts(round4["ceo_claims"]) != _texts(round3["ceo_claims"])


def test_round_five_still_ceo_only(engine: Any) -> None:
    """Once the cap kicks in, every further revise stays CEO-only."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    current = engine.run_target_feasibility_review(skip_llm=False)
    for _ in range(4):
        revision = engine.request_approval_revision(
            request_id=current["id"], counter="please reconsider", by_type="user"
        )
        current = revision["successor"]

    # CFO/CoS only called during rounds 1-3.
    assert len(reg.log["cfo"]) == 3
    assert len(reg.log["cos"]) == 3
    # CEO called rounds 1-3 (trio) + rounds 4-5 (ceo-only) = 5.
    assert len(reg.log["ceo"]) == 5
    assert current["payload"]["generation"] == 5
    assert current["payload"]["ceo_only"] is True


def test_predecessor_chain_intact_through_iteration_cap(engine: Any) -> None:
    """``list_thread`` must return 5 linked approvals after 4 revises."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    current = engine.run_target_feasibility_review(skip_llm=False)
    root_id = current["id"]
    for _ in range(4):
        revision = engine.request_approval_revision(
            request_id=current["id"], counter="please reconsider", by_type="user"
        )
        current = revision["successor"]

    chain = engine.approvals.list_thread(root_id)
    feasibility_chain = [a for a in chain if a.action_type == "target_feasibility"]
    assert len(feasibility_chain) == 5
    # Generations 1..5 ordered.
    gens = [a.payload.get("generation") for a in feasibility_chain if a.payload]
    assert gens == [1, 2, 3, 4, 5]
