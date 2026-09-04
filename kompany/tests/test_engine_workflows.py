"""Engine-level workflow ops + plugin binding + approval-effect registry (1.1.0)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from kompany.core.engine import KompanyEngine
from kompany.core.workflows_registry import WorkflowNotFound
from kompany.plugins.contract import CostEstimate, ToolContext, Workflow
from kompany.state.models import ApprovalRequest


@dataclass
class _Resp:
    text: str
    cost_usd: float = 0.01


class _FakeAgent:
    def __init__(self, role: str):
        self.role = role

    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        return _Resp(text=f"{self.role}:{prompt[:20]}")


class _FakeRegistry:
    def get(self, role, company_state=None):
        return _FakeAgent(role)


def _seen_ctx(prior, ctx):
    tc = ctx.tool_context
    return {
        "has_documents": tc is not None and tc.documents is not None,
        "has_approvals": tc is not None and tc.approvals is not None,
        "run_id": tc.run_id if tc else None,
        "prior_keys": sorted(prior),
    }


class _PluginWorkflow(Workflow):
    workflow_id = "test-plugin-wf"
    display_name = "Plugin workflow"
    python_callables = {"seen_ctx": _seen_ctx}
    bound_to = None

    def __init__(self):
        self.yaml_path = None
        self._data = {
            "workflow_id": self.workflow_id,
            "display_name": self.display_name,
            "steps": [
                {"id": "ask", "agent_role": "cmo", "prompt_template": "Brief: {brief}",
                 "cost_estimate_usd": 0.1},
                {"id": "persist", "agent_role": "coo", "python_callable": "seen_ctx",
                 "cost_estimate_usd": 0.0},
            ],
        }

    def estimate_cost(self):
        return CostEstimate(llm_usd=0.1)

    def bind(self, engine):
        _PluginWorkflow.bound_to = engine
        engine.register_approval_effect(
            "test_gate",
            on_approve=lambda eng, req: {"status": "plugin_approved", "id": req.id},
            on_reject=lambda eng, req: {"status": "plugin_rejected"},
        )


@pytest.fixture()
def engine(monkeypatch, tmp_path):
    plugin = _PluginWorkflow()
    # The registry resolves a plugin's YAML via ``yaml_path``; write it out.
    import yaml

    path = tmp_path / "wf.yaml"
    path.write_text(yaml.safe_dump(plugin._data))
    plugin.yaml_path = path
    monkeypatch.setattr(
        "kompany.plugins.loader.discover", lambda: {"workflow": [plugin]}
    )
    eng = KompanyEngine()
    eng.registry = _FakeRegistry()
    return eng


def test_bind_called_at_boot_and_registers_effect(engine):
    assert _PluginWorkflow.bound_to is engine
    assert engine.plugin_bind_errors == []
    assert "test_gate" in engine._approval_effects


def test_workflows_list_includes_plugin_with_cost_preview(engine):
    rows = {r["workflow_id"]: r for r in engine.workflows_list()}
    assert rows["test-plugin-wf"]["source"] == "plugin"
    assert rows["test-plugin-wf"]["estimated_cost_usd"] == pytest.approx(0.1)
    assert rows["idea-validation"]["source"] == "builtin"
    assert [s["id"] for s in rows["test-plugin-wf"]["steps"]] == ["ask", "persist"]


def test_run_workflow_wires_tool_context_and_audits(engine):
    result = engine.run_workflow("test-plugin-wf", {"brief": "hello"}, project_id="p1")
    assert result["ok"] is True and result["run_id"]
    outputs = {s["step_id"]: s["output"] for s in result["steps"]}
    assert outputs["ask"].startswith("cmo:Brief: hello")
    assert outputs["persist"]["has_documents"] is True
    assert outputs["persist"]["has_approvals"] is True
    assert outputs["persist"]["run_id"] == result["run_id"]
    assert result["total_cost_usd"] == pytest.approx(0.01)
    events = [r["event_type"] for r in engine.audit.recent(limit=20)]
    assert "workflow.started" in events and "workflow.completed" in events


def test_run_workflow_unknown_id_raises(engine):
    with pytest.raises(WorkflowNotFound):
        engine.run_workflow("nope")


def test_plugin_effect_runs_on_approve_and_reject(engine):
    req = engine.approvals.create(
        ApprovalRequest(action_type="test_gate", summary="gate", payload={})
    )
    out = engine.approve_request(req.id)
    assert out["effect"] == {"status": "plugin_approved", "id": req.id}
    req2 = engine.approvals.create(
        ApprovalRequest(action_type="test_gate", summary="gate", payload={})
    )
    out2 = engine.reject_request(req2.id, reason="no")
    assert out2["effect"] == {"status": "plugin_rejected"}


def test_plugin_effect_exception_is_contained_and_audited(engine):
    def boom(eng, req):
        raise RuntimeError("kaboom")

    engine.register_approval_effect("boom_gate", on_approve=boom)
    req = engine.approvals.create(
        ApprovalRequest(action_type="boom_gate", summary="x", payload={})
    )
    out = engine.approve_request(req.id)
    assert out["status"] == "approved"
    assert out["effect"]["status"] == "effect_failed"
    assert any(
        r["event_type"] == "approval_effect.failed" for r in engine.audit.recent(limit=20)
    )


def test_bind_failure_does_not_block_boot(monkeypatch):
    class Broken(Workflow):
        workflow_id = "broken"
        display_name = "Broken"

        def estimate_cost(self):
            return CostEstimate()

        def bind(self, engine):
            raise RuntimeError("no")

    monkeypatch.setattr(
        "kompany.plugins.loader.discover", lambda: {"workflow": [Broken()]}
    )
    eng = KompanyEngine()
    assert eng.plugin_bind_errors and eng.plugin_bind_errors[0][0] == "broken"


def test_tool_context_new_fields_default_none_for_old_callers():
    ctx = ToolContext(run_id="r", ledger=None, audit=None, credentials=None, settings=None)
    assert ctx.documents is None and ctx.artifacts is None and ctx.approvals is None
    assert ctx.project_id is None and ctx.company_id is None


def test_workflow_bind_default_is_noop():
    class W(Workflow):
        def estimate_cost(self):
            return CostEstimate()

    assert W().bind(object()) is None
