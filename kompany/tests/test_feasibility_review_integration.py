"""End-to-end integration test for the feasibility-review-debate task (05-19).

Walks the full flow:

* Apply a starter template so founder targets exist.
* ``run_target_feasibility_review`` → see 3 columns of claims + a single
  ``rounds`` entry with ``generation=1``.
* ``request_approval_revision`` once → see a second approval with
  ``generation=2``, both ``revision_hint`` recorded and a fresh trio of
  claims.
* Revise 3 more times → see iteration 4+ collapse to CEO-only.
* Approve the latest approval → agreed targets land.

This test exercises the same call sites the UI does but at the engine
layer; UI rendering is covered by feasibility_review.js itself (no JS
test framework wired in this repo).
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


def _resp(text: str) -> Any:
    return SimpleNamespace(
        text=text,
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        model="claude-sonnet-4-20250514",
        parsed=ClaimList(claims=[
            Claim(
                text=text,
                evidence=[
                    Source(
                        source_type=SourceType.USER_INPUT,
                        source_ref="t",
                        claim_supported="t",
                    )
                ],
            )
        ]),
    )


class _SeqRegistry:
    """Returns deterministic role-tagged claims indexed by call count.

    Lets us assert that consecutive rounds produce DIFFERENT claim text
    so the diff renderer has something to work with.
    """

    def __init__(self) -> None:
        self.counts = {"cfo": 0, "cos": 0, "ceo": 0}

    def get(self, role: str, company_state: Any = None) -> Any:
        counts = self.counts

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
                counts[role] += 1
                return _resp(f"{role.upper()}_R{counts[role]}")

        return _Agent()


def test_full_review_revise_iteration_then_approve(engine: Any) -> None:
    reg = _SeqRegistry()
    engine.registry = reg
    engine.apply_template(
        "saas-startup",
        override_budget=1000.0,
        override_revenue_target=10000.0,
    )

    # 1. Initial review.
    current = engine.run_target_feasibility_review(skip_llm=False)
    assert current is not None
    assert current["payload"]["generation"] == 1
    assert current["payload"]["ceo_only"] is False
    assert len(current["payload"]["rounds"]) == 1

    # 2. First revise.
    revision = engine.request_approval_revision(
        request_id=current["id"], counter="treat $50 as project budget", by_type="user"
    )
    current = revision["successor"]
    assert current["payload"]["generation"] == 2
    assert current["payload"]["ceo_only"] is False
    assert len(current["payload"]["rounds"]) == 2
    # Round 2 claim text differs from round 1 — diff renderer has signal.
    r1 = current["payload"]["rounds"][0]
    r2 = current["payload"]["rounds"][1]
    assert r1["cfo_claims"] != r2["cfo_claims"]

    # 3. Two more revises to drive into iteration 4.
    for _ in range(2):
        rev = engine.request_approval_revision(
            request_id=current["id"], counter="please reconsider", by_type="user"
        )
        current = rev["successor"]
    assert current["payload"]["generation"] == 4
    assert current["payload"]["ceo_only"] is True
    # CFO/CoS frozen from round 3 — same text in round 4.
    rounds = current["payload"]["rounds"]
    r3 = rounds[2]
    r4 = rounds[3]
    assert r4["ceo_only"] is True
    assert r4["cfo_claims"] == r3["cfo_claims"]
    assert r4["cos_claims"] == r3["cos_claims"]
    assert r4["ceo_claims"] != r3["ceo_claims"]

    # 4. Approve the latest round → agreed targets land.
    out = engine.approve_request(current["id"], approved_by="master")
    assert out is not None
    bundle = engine.get_targets_bundle()
    assert bundle.agreed is not None
    # Recommended_targets propagates through every successor's payload,
    # so the agreed numbers match the recommendation that the team
    # carried forward.
    assert bundle.agreed.source == "agreed"
