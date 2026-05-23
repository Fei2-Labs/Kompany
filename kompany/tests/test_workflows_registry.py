"""Tests for workflow registry — Core built-in + Pro plugin discovery."""

from __future__ import annotations

import pytest

from kompany.core.workflow_runner import WorkflowRunner
from kompany.core.workflows_registry import (
    WorkflowNotFound,
    get,
    list_workflows,
)


def test_three_reference_workflows_present():
    ids = list_workflows()
    assert "idea-validation" in ids
    assert "weekly-exec-review" in ids
    assert "landing-page-launch" in ids


def test_get_returns_configured_runner():
    runner = get("idea-validation")
    assert isinstance(runner, WorkflowRunner)
    assert runner.workflow_id == "idea-validation"
    assert len(runner.steps) == 3
    assert {s["agent_role"] for s in runner.steps} == {"cv", "cpo", "ceo"}


def test_weekly_exec_review_shape():
    runner = get("weekly-exec-review")
    assert runner.workflow_id == "weekly-exec-review"
    assert len(runner.steps) == 4
    # First step uses founder-supplied burn / budget inputs
    assert "{budget_remaining_usd}" in runner.steps[0]["prompt_template"]


def test_landing_page_launch_has_approval_step():
    runner = get("landing-page-launch")
    last = runner.steps[-1]
    assert last["id"] == "ship_authorization"
    assert last["autonomy_tier"] == "approval"


def test_get_passes_python_callables_and_executor():
    def fake_exec(step, prior, ctx):
        from kompany.core.workflow_runner import StepResult

        return StepResult(step_id=step["id"])

    runner = get(
        "idea-validation",
        python_callables={"x": lambda: None},
        step_executor=fake_exec,
    )
    assert "x" in runner.python_callables
    # Runner picks up the executor
    assert runner._step_executor is fake_exec  # noqa: SLF001


def test_get_unknown_workflow_raises():
    with pytest.raises(WorkflowNotFound, match="workflow not found"):
        get("does-not-exist")


def test_pro_workflow_discovery(monkeypatch, tmp_path):
    """Pro Workflow plugin discovered via entry-point loader."""
    from kompany.plugins.contract import Workflow

    yaml_path = tmp_path / "pro.yaml"
    yaml_path.write_text(
        """
workflow_id: saas-pro-onboarding
display_name: SaaS Pro Onboarding
steps:
  - id: s1
    agent_role: cpo
    prompt_template: hi
    autonomy_tier: auto
""".strip()
    )

    class ProWF(Workflow):
        workflow_id = "saas-pro-onboarding"
        display_name = "SaaS Pro Onboarding"

        def __init__(self):
            self.yaml_path = yaml_path

        def estimate_cost(self):
            from kompany.plugins.contract import CostEstimate

            return CostEstimate()

    monkeypatch.setattr(
        "kompany.plugins.loader.discover",
        lambda: {
            "workflow": [ProWF()],
            "soul": [], "integration": [], "template": [], "tool": [],
        },
    )

    ids = list_workflows()
    assert "saas-pro-onboarding" in ids
    # Built-ins still discovered
    assert "idea-validation" in ids

    runner = get("saas-pro-onboarding")
    assert runner.workflow_id == "saas-pro-onboarding"


def test_pro_workflow_without_yaml_path_raises(monkeypatch):
    from kompany.plugins.contract import Workflow

    class BadPro(Workflow):
        workflow_id = "bad-pro"
        display_name = "Bad Pro"

        def __init__(self):
            self.yaml_path = None

        def estimate_cost(self):
            from kompany.plugins.contract import CostEstimate
            return CostEstimate()

    monkeypatch.setattr(
        "kompany.plugins.loader.discover",
        lambda: {
            "workflow": [BadPro()],
            "soul": [], "integration": [], "template": [], "tool": [],
        },
    )
    with pytest.raises(WorkflowNotFound, match="yaml_path is unset"):
        get("bad-pro")
