"""Tests for default step executor — agent-call + python_callable paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kompany.core.step_executor import (
    ExecutorContext,
    default_step_executor,
)
from kompany.core.workflow_runner import StepResult, WorkflowRunner


@dataclass
class FakeLLMResponse:
    text: str
    cost_usd: float = 0.05


class FakeAgent:
    def __init__(self, response_text: str = "ok"):
        self._text = response_text
        self.last_prompt: str | None = None
        self.last_action_type: str | None = None
        self.last_directive_id: str | None = None

    def call(self, prompt: str, directive_id=None, max_tokens=4096, action_type=None):
        self.last_prompt = prompt
        self.last_action_type = action_type
        self.last_directive_id = directive_id
        return FakeLLMResponse(text=self._text, cost_usd=0.05)


class FakeRegistry:
    def __init__(self):
        self.agents: dict[str, FakeAgent] = {}

    def get(self, role: str, company_state=None):
        if role not in self.agents:
            self.agents[role] = FakeAgent(response_text=f"<{role} reply>")
        return self.agents[role]


def _workflow_dict():
    return {
        "workflow_id": "test-wf",
        "display_name": "Test",
        "steps": [
            {
                "id": "s1",
                "agent_role": "cpo",
                "prompt_template": "Hello {name}, prior: {missing}",
                "autonomy_tier": "auto",
                "cost_estimate_usd": 0.10,
            },
            {
                "id": "s2",
                "agent_role": "ceo",
                "prompt_template": "s1 said: {s1}",
                "autonomy_tier": "auto",
                "cost_estimate_usd": 0.10,
            },
        ],
    }


def test_executor_agent_call_substitutes_inputs_and_prior():
    registry = FakeRegistry()
    runner = WorkflowRunner(_workflow_dict(), step_executor=lambda *a: None)
    ctx = ExecutorContext(
        registry=registry,  # type: ignore[arg-type]
        runner=runner,
        initial_inputs={"name": "Founder"},
    )

    step = _workflow_dict()["steps"][0]
    result = default_step_executor(step, {}, ctx)

    assert result.error is None
    assert result.cost_usd == pytest.approx(0.05)
    assert result.output == "<cpo reply>"
    # missing var stays as placeholder
    assert "Hello Founder, prior: {missing}" == registry.agents["cpo"].last_prompt
    assert registry.agents["cpo"].last_action_type == "workflow.test-wf.s1"


def test_executor_prior_outputs_visible_in_template():
    registry = FakeRegistry()
    runner = WorkflowRunner(_workflow_dict(), step_executor=lambda *a: None)
    ctx = ExecutorContext(registry=registry, runner=runner)  # type: ignore[arg-type]
    step = _workflow_dict()["steps"][1]
    result = default_step_executor(step, {"s1": "previous output"}, ctx)
    assert result.error is None
    assert registry.agents["ceo"].last_prompt == "s1 said: previous output"


def test_executor_approval_tier_halts():
    registry = FakeRegistry()
    runner = WorkflowRunner(_workflow_dict(), step_executor=lambda *a: None)
    ctx = ExecutorContext(registry=registry, runner=runner)  # type: ignore[arg-type]
    step = dict(_workflow_dict()["steps"][0])
    step["autonomy_tier"] = "approval"

    result = default_step_executor(step, {}, ctx)
    assert result.error and "needs_approval" in result.error
    assert result.output is None


def test_executor_human_only_returns_prompt_without_calling():
    registry = FakeRegistry()
    runner = WorkflowRunner(_workflow_dict(), step_executor=lambda *a: None)
    ctx = ExecutorContext(
        registry=registry,  # type: ignore[arg-type]
        runner=runner,
        initial_inputs={"name": "X"},
    )
    step = dict(_workflow_dict()["steps"][0])
    step["autonomy_tier"] = "human_only"

    result = default_step_executor(step, {}, ctx)
    assert result.error is None
    assert result.output["kind"] == "human_only"
    assert "Hello X" in result.output["suggested_prompt"]
    # No LLM call
    assert "cpo" not in registry.agents


def test_executor_python_callable_path():
    runner_dict = _workflow_dict()
    runner_dict["steps"][0]["python_callable"] = "ship_landing"
    del runner_dict["steps"][0]["prompt_template"]

    captured = {}

    def ship_landing(prior, ctx):
        captured["prior"] = dict(prior)
        return {"shipped": True}

    runner = WorkflowRunner(
        runner_dict,
        python_callables={"ship_landing": ship_landing},
        step_executor=lambda *a: None,
    )
    ctx = ExecutorContext(registry=FakeRegistry(), runner=runner)  # type: ignore[arg-type]
    step = runner_dict["steps"][0]
    result = default_step_executor(step, {"upstream": "data"}, ctx)

    assert result.error is None
    assert result.output == {"shipped": True}
    assert captured["prior"] == {"upstream": "data"}


def test_executor_python_callable_missing():
    runner_dict = _workflow_dict()
    runner_dict["steps"][0]["python_callable"] = "ship_landing"
    del runner_dict["steps"][0]["prompt_template"]

    runner = WorkflowRunner(
        runner_dict,
        python_callables={},  # not registered
        step_executor=lambda *a: None,
    )
    ctx = ExecutorContext(registry=FakeRegistry(), runner=runner)  # type: ignore[arg-type]
    step = runner_dict["steps"][0]
    result = default_step_executor(step, {}, ctx)
    assert result.error and "not registered" in result.error


def test_executor_missing_prompt_template_errors():
    runner_dict = _workflow_dict()
    del runner_dict["steps"][0]["prompt_template"]
    runner = WorkflowRunner(runner_dict, step_executor=lambda *a: None)
    ctx = ExecutorContext(registry=FakeRegistry(), runner=runner)  # type: ignore[arg-type]
    result = default_step_executor(runner_dict["steps"][0], {}, ctx)
    assert result.error and "prompt_template missing" in result.error


def test_executor_propagates_llm_failure_as_step_error():
    class BoomAgent(FakeAgent):
        def call(self, **kwargs):
            raise RuntimeError("provider down")

    class BoomRegistry:
        def get(self, role, company_state=None):
            return BoomAgent()

    runner = WorkflowRunner(_workflow_dict(), step_executor=lambda *a: None)
    ctx = ExecutorContext(registry=BoomRegistry(), runner=runner)  # type: ignore[arg-type]
    result = default_step_executor(_workflow_dict()["steps"][0], {}, ctx)
    assert result.error and "llm_call_failed" in result.error


def test_workflow_runner_runs_with_default_executor_end_to_end():
    """Integration: full 2-step run through the production executor.

    Validates that cost accumulates, prior outputs propagate, and the
    runner halts at the first error (none in this happy path).
    """
    registry = FakeRegistry()
    runner = WorkflowRunner(_workflow_dict())

    def exec_wrapper(step, prior, ctx_obj):
        return default_step_executor(step, prior, ctx_obj)

    runner._step_executor = exec_wrapper  # noqa: SLF001
    ctx = ExecutorContext(
        registry=registry,  # type: ignore[arg-type]
        runner=runner,
        initial_inputs={"name": "X"},
    )
    result = runner.run(ctx)
    assert result.ok
    assert len(result.steps) == 2
    assert result.total_cost_usd == pytest.approx(0.10)
    # Step 2 saw step 1's output
    assert registry.agents["ceo"].last_prompt == "s1 said: <cpo reply>"
