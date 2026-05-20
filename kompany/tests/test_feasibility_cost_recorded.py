"""Cost-recording discipline tests for the feasibility-review flow.

Cross-cutting requirement (see ``engineering-cost-visibility-discipline``
memory + ``05-19-cost-visibility-discipline`` task): every LLM-spending
action must funnel through ``CostTracker.record`` so a ledger row +
``llm.spend`` SSE event are emitted with the right ``action_type``
label.

For the feasibility review:

* Initial review → 3 LLM calls, all tagged ``"target_feasibility"``.
* Revise pass → 3 LLM calls, all tagged ``"feasibility_revise"``.
* Iteration 4+ → 1 LLM call (CEO-only), tagged ``"feasibility_revise"``.

These tests use a captured ``CostTracker.record`` shim instead of the
real LLM client because the unit-test agents don't go through
``LLMClient`` — they're recorded fakes. The discipline requirement is
that the call sites pass the right ``action_type`` kwarg, which we
verify on the agent fakes directly.
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


def _make_resp() -> Any:
    claim = Claim(
        text="ok",
        evidence=[
            Source(
                source_type=SourceType.USER_INPUT,
                source_ref="t",
                claim_supported="t",
            )
        ],
    )
    return SimpleNamespace(
        text="ok",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0123,
        model="claude-sonnet-4-20250514",
        parsed=ClaimList(claims=[claim]),
    )


class _RecordingRegistry:
    def __init__(self) -> None:
        self.action_types: list[tuple[str, str | None]] = []  # (role, action_type)

    def get(self, role: str, company_state: Any = None) -> Any:
        sink = self.action_types

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
                sink.append((role, action_type))
                return _make_resp()

        return _Agent()


def test_initial_review_tags_every_call_target_feasibility(engine: Any) -> None:
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    engine.run_target_feasibility_review(skip_llm=False)

    # 3 calls — one per role — all tagged 'target_feasibility'.
    assert len(reg.action_types) == 3
    assert {(r, t) for r, t in reg.action_types} == {
        ("cfo", "target_feasibility"),
        ("cos", "target_feasibility"),
        ("ceo", "target_feasibility"),
    }


def test_revise_pass_tags_every_call_feasibility_revise(engine: Any) -> None:
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    initial = engine.run_target_feasibility_review(skip_llm=False)
    reg.action_types.clear()
    engine.request_approval_revision(
        request_id=initial["id"], counter="hint", by_type="user"
    )

    # The revise pass must produce another full trio with the
    # 'feasibility_revise' label so the cost meter splits it from the
    # initial spend.
    assert len(reg.action_types) == 3
    for role, label in reg.action_types:
        assert label == "feasibility_revise", (
            f"{role} revise call missed feasibility_revise tag (got {label})"
        )


def test_ceo_only_round_tags_feasibility_revise(engine: Any) -> None:
    """Iteration 4+ collapses to one CEO call; it must still be tagged."""
    reg = _RecordingRegistry()
    engine.registry = reg
    engine.apply_template("saas-startup")

    current = engine.run_target_feasibility_review(skip_llm=False)
    # Three revises -> rounds 2, 3, 4.
    for _ in range(3):
        revision = engine.request_approval_revision(
            request_id=current["id"], counter="hint", by_type="user"
        )
        current = revision["successor"]

    # Last 3 entries belong to round 4. Round 4 is CEO-only, so only one
    # entry should land for that revise. Trim everything except the
    # round-4 batch by counting backwards from the end.
    # Total calls = 3 (round1) + 3 (round2) + 3 (round3) + 1 (round4) = 10.
    assert len(reg.action_types) == 10
    role, label = reg.action_types[-1]
    assert role == "ceo"
    assert label == "feasibility_revise"


def test_cost_tracker_records_one_spend_per_call(engine: Any, monkeypatch) -> None:
    """When an LLM call goes through the real ``LLMClient`` path, the
    cost tracker must observe one spend per call. We stub
    ``LLMClient.call_structured`` to a deterministic response so the
    tracker sees the spend without needing a network.
    """
    # Replace the LLM client's call_structured with one that funnels
    # through the real CostTracker.record. The fake registry path used
    # by other tests bypasses LLMClient; here we want the end-to-end
    # tracker behaviour.
    recorded: list[dict[str, Any]] = []
    real_record = engine.cost_tracker.record

    def _capture_record(*args, **kwargs):
        recorded.append({"args": args, "kwargs": dict(kwargs)})
        return real_record(*args, **kwargs)

    monkeypatch.setattr(engine.cost_tracker, "record", _capture_record)

    # Stub LLMClient.call_structured so it routes through CostTracker.
    class _FakeLLM:
        def call_structured(
            self,
            *,
            model: str,
            system: str,
            prompt: str,
            output_schema: Any,
            agent_name: str | None = None,
            directive_id: str | None = None,
            max_tokens: int = 4096,
            action_type: str | None = None,
        ) -> Any:
            from kompany.llm.client import LLMResponse

            resp = LLMResponse(
                text="ok",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.0,
                model=model,
                parsed=ClaimList(
                    claims=[
                        Claim(
                            text=f"{agent_name} ok",
                            evidence=[
                                Source(
                                    source_type=SourceType.USER_INPUT,
                                    source_ref="t",
                                    claim_supported="t",
                                )
                            ],
                        )
                    ]
                ),
            )
            # Mimic the real client: route through CostTracker.
            engine.cost_tracker.record(
                model=model,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                description=f"{agent_name}: {action_type}",
                directive_id=directive_id,
                action_type=action_type,
            )
            return resp

    engine.llm_client = _FakeLLM()

    # Build agents that route through the engine's fake LLMClient via
    # BaseAgent.call_structured.
    from kompany.agents.base import BaseAgent

    class _Agent(BaseAgent):
        role = ""
        display_name = ""

        def system_prompt(self) -> str:
            return ""

    class _Registry:
        def get(self_inner, role: str, company_state: Any = None) -> Any:
            a = _Agent(engine.llm_client, engine.settings)
            a.role = role
            a.display_name = role.upper()
            return a

    engine.registry = _Registry()
    engine.apply_template("saas-startup")
    engine.run_target_feasibility_review(skip_llm=False)

    # Exactly 3 record() invocations, one per role, all with the
    # target_feasibility action_type.
    feasibility_calls = [
        r for r in recorded if r["kwargs"].get("action_type") == "target_feasibility"
    ]
    assert len(feasibility_calls) == 3
