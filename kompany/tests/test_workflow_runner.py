"""Tests for WorkflowRunner — workflow YAML interpreter."""

from __future__ import annotations

from pathlib import Path

import pytest

from kompany.core.workflow_runner import (
    StepResult,
    WorkflowRunResult,
    WorkflowRunner,
    WorkflowYAMLInvalid,
)


def _ok_workflow() -> dict:
    return {
        "workflow_id": "14-day-saas-launch",
        "display_name": "14-day SaaS Launch",
        "steps": [
            {
                "id": "day1_define_icp",
                "agent_role": "cpo",
                "cost_estimate_usd": 0.30,
                "autonomy_tier": "auto",
            },
            {
                "id": "day3_landing_page",
                "agent_role": "cto",
                "python_callable": "ship_landing",
                "autonomy_tier": "approval",
            },
            {
                "id": "day7_stripe_setup",
                "agent_role": "cfo",
                "tool_calls": ["stripe.create_product"],
                "cost_estimate_usd": 0.45,
                "autonomy_tier": "approval",
            },
        ],
    }


def test_load_from_dict():
    runner = WorkflowRunner(_ok_workflow())
    assert runner.workflow_id == "14-day-saas-launch"
    assert runner.display_name == "14-day SaaS Launch"
    assert len(runner.steps) == 3


def test_load_from_yaml_file(tmp_path: Path):
    import yaml as _yaml

    path = tmp_path / "wf.yaml"
    path.write_text(_yaml.dump(_ok_workflow()))
    runner = WorkflowRunner(path)
    assert runner.workflow_id == "14-day-saas-launch"


def test_estimate_cost_sums_step_costs():
    runner = WorkflowRunner(_ok_workflow())
    est = runner.estimate_cost()
    assert est.llm_usd == pytest.approx(0.75)
    # 1 of 3 steps missing a cost (python_callable step) → confidence < 1
    assert est.confidence == pytest.approx(2 / 3)


def test_estimate_cost_full_confidence_when_all_steps_priced():
    wf = _ok_workflow()
    wf["steps"][1]["cost_estimate_usd"] = 0.0  # add cost to escape-hatch step
    runner = WorkflowRunner(wf)
    est = runner.estimate_cost()
    assert est.confidence == 1.0


def test_missing_workflow_id_rejected():
    wf = _ok_workflow()
    del wf["workflow_id"]
    with pytest.raises(WorkflowYAMLInvalid, match="missing required field 'workflow_id'"):
        WorkflowRunner(wf)


def test_empty_steps_rejected():
    wf = _ok_workflow()
    wf["steps"] = []
    with pytest.raises(WorkflowYAMLInvalid, match="non-empty list"):
        WorkflowRunner(wf)


def test_duplicate_step_id_rejected():
    wf = _ok_workflow()
    wf["steps"][1]["id"] = "day1_define_icp"
    with pytest.raises(WorkflowYAMLInvalid, match="Duplicate step id"):
        WorkflowRunner(wf)


def test_invalid_autonomy_tier_rejected():
    wf = _ok_workflow()
    wf["steps"][0]["autonomy_tier"] = "yolo"
    with pytest.raises(WorkflowYAMLInvalid, match="invalid autonomy_tier"):
        WorkflowRunner(wf)


def test_missing_yaml_file():
    with pytest.raises(WorkflowYAMLInvalid, match="not found"):
        WorkflowRunner("/nonexistent/wf.yaml")


def test_run_without_executor_raises():
    runner = WorkflowRunner(_ok_workflow())
    with pytest.raises(WorkflowYAMLInvalid, match="no step_executor"):
        runner.run()


def test_run_dispatches_to_executor_in_order():
    calls: list[str] = []

    def exec_(step, prior, ctx):
        calls.append(step["id"])
        return StepResult(step_id=step["id"], output={"prior_count": len(prior)})

    runner = WorkflowRunner(_ok_workflow(), step_executor=exec_)
    result = runner.run(ctx=None)
    assert calls == ["day1_define_icp", "day3_landing_page", "day7_stripe_setup"]
    assert result.ok
    assert len(result.steps) == 3
    # Each step sees outputs of all prior steps
    assert result.steps[2].output == {"prior_count": 2}


def test_run_stops_on_step_error():
    def exec_(step, prior, ctx):
        if step["id"] == "day3_landing_page":
            raise RuntimeError("simulated failure")
        return StepResult(step_id=step["id"])

    runner = WorkflowRunner(_ok_workflow(), step_executor=exec_)
    result = runner.run()
    assert not result.ok
    assert len(result.steps) == 2  # day1 + day3, no day7
    assert result.steps[1].error and "simulated failure" in result.steps[1].error


def test_workflow_run_result_total_cost():
    result = WorkflowRunResult(
        workflow_id="x",
        steps=[
            StepResult(step_id="a", cost_usd=0.10),
            StepResult(step_id="b", cost_usd=0.25),
        ],
    )
    assert result.total_cost_usd == pytest.approx(0.35)
    assert result.ok


def test_resolve_python_callable():
    def ship_landing():
        return "ok"

    runner = WorkflowRunner(
        _ok_workflow(),
        python_callables={"ship_landing": ship_landing},
    )
    assert runner.resolve_python_callable("day3_landing_page") is ship_landing
    # Step without python_callable
    assert runner.resolve_python_callable("day1_define_icp") is None
    # Unknown step
    assert runner.resolve_python_callable("nonexistent") is None
