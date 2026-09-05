"""#42: YAML steps with autonomy_tier: approval pause the run on a workflow_step
card; approving resumes at that step, rejecting stops, replay is idempotent."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import yaml

from kompany.core.engine import KompanyEngine
from kompany.core.engine_parts.workflows import ACTION_WORKFLOW_STEP
from kompany.core.workflow_runner import WorkflowRunner, WorkflowYAMLInvalid
from kompany.plugins.contract import CostEstimate, Workflow


@dataclass
class _Resp:
    text: str
    cost_usd: float = 0.02


class _Agent:
    calls: list[tuple[str, str]] = []

    def __init__(self, role):
        self.role = role

    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        _Agent.calls.append((self.role, action_type))
        return _Resp(text=f"{self.role} says: {prompt[:30]}")


class _Registry:
    def get(self, role, company_state=None):
        return _Agent(role)


class _GatedWorkflow(Workflow):
    workflow_id = "gated-wf"
    display_name = "Gated"

    def __init__(self, path):
        self.yaml_path = path

    def estimate_cost(self):
        return CostEstimate(llm_usd=0.5)


@pytest.fixture()
def engine(monkeypatch, tmp_path):
    data = {
        "workflow_id": "gated-wf", "display_name": "Gated",
        "steps": [
            {"id": "draft", "agent_role": "cmo", "prompt_template": "Draft {topic}", "cost_estimate_usd": 0.1},
            {"id": "ship", "agent_role": "ceo", "prompt_template": "Ship? {draft}", "cost_estimate_usd": 0.1,
             "autonomy_tier": "approval"},
            {"id": "announce", "agent_role": "cmo", "prompt_template": "Announce: {ship}", "cost_estimate_usd": 0.1},
        ],
    }
    path = tmp_path / "gated.yaml"; path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr("kompany.plugins.loader.discover", lambda: {"workflow": [_GatedWorkflow(path)]})
    eng = KompanyEngine(); eng.registry = _Registry(); _Agent.calls = []
    return eng


def _card(engine):
    rows = [r for r in engine.approvals.list_pending() if r.action_type == ACTION_WORKFLOW_STEP]
    assert len(rows) == 1
    return rows[0]


def test_run_pauses_on_approval_step_and_files_card(engine):
    out = engine.run_workflow("gated-wf", {"topic": "launch"}, project_id="p1")
    assert out["status"] == "paused" and out["paused_at"] == "ship"
    assert [s["step_id"] for s in out["steps"]] == ["draft"]  # nothing past the gate ran
    assert [r for r, _ in _Agent.calls] == ["cmo"]
    card = _card(engine)
    assert card.id == out["approval_id"] and card.requested_by == "ceo" and card.severity == "high"
    p = card.payload
    assert p["step_id"] == "ship" and p["remaining_steps"] == ["ship", "announce"]
    assert p["prior_outputs"]["draft"].startswith("cmo says")
    assert "Ship? cmo says" in p["prompt_preview"]
    assert p["estimated_step_cost_usd"] == pytest.approx(0.1)
    events = [r["event_type"] for r in engine.audit.recent(limit=20)]
    assert "workflow.paused" in events and "workflow.completed" not in events


def test_approve_resumes_at_gate_and_runs_the_rest(engine):
    engine.run_workflow("gated-wf", {"topic": "launch"}, project_id="p1")
    card = _card(engine)
    _Agent.calls = []
    out = engine.approve_request(card.id)
    eff = out["effect"]
    assert eff["status"] == "resumed" and eff["run"]["status"] == "completed"
    assert [s["step_id"] for s in eff["run"]["steps"]] == ["ship", "announce"]
    assert [r for r, _ in _Agent.calls] == ["ceo", "cmo"]  # draft NOT re-run
    assert eff["run"]["steps"][1]["output"].startswith("cmo says: Announce: ceo says")
    assert eff["run"]["resumed_from"] == card.id
    stamped = engine.approvals.get(card.id).payload
    assert stamped["effect_applied"] is True and stamped["resume_status"] == "completed"
    # replay guard
    assert engine.resume_workflow_step(engine.approvals.get(card.id)) == {"status": "already_applied"}
    assert len([c for c in engine.approvals.list_comments(card.id) if "Resumed at 'ship'" in c.body]) == 1


def test_reject_stops_run_and_nothing_else_executes(engine):
    engine.run_workflow("gated-wf", {"topic": "launch"})
    card = _card(engine); _Agent.calls = []
    out = engine.reject_request(card.id, reason="not now")
    assert out["effect"] == {"status": "cancelled", "stopped_at": "ship"}
    assert _Agent.calls == []
    assert any(r["event_type"] == "workflow.cancelled" for r in engine.audit.recent(limit=20))


def test_runner_resume_primitives():
    data = {"workflow_id": "w", "display_name": "W", "steps": [
        {"id": "a", "agent_role": "x"}, {"id": "b", "agent_role": "x", "autonomy_tier": "approval"},
        {"id": "c", "agent_role": "x"}]}
    seen = []

    def exec_(step, prior, ctx):
        from kompany.core.workflow_runner import StepResult
        seen.append((step["id"], step.get("autonomy_tier", "auto"), dict(prior)))
        return StepResult(step_id=step["id"], output=step["id"].upper())

    r = WorkflowRunner(data, step_executor=exec_)
    res = r.run(None, start_at="b", prior_outputs={"a": "A0"}, force_auto={"b"})
    assert [s[0] for s in seen] == ["b", "c"] and seen[0][1] == "auto" and seen[0][2] == {"a": "A0"}
    assert res.outputs == {"a": "A0", "b": "B", "c": "C"}
    with pytest.raises(WorkflowYAMLInvalid):
        r.run(None, start_at="nope")


def test_landing_page_launch_ship_authorization_is_now_reachable(monkeypatch):
    monkeypatch.setattr("kompany.plugins.loader.discover", lambda: {})
    eng = KompanyEngine(); eng.registry = _Registry(); _Agent.calls = []
    out = eng.run_workflow("landing-page-launch", {"product": "Kompany", "budget_usd": 100}, project_id="p1")
    assert out["status"] == "paused" and out["paused_at"] == "ship_authorization"
    card = _card(eng)
    eff = eng.approve_request(card.id)["effect"]
    assert eff["run"]["status"] == "completed"
    assert ("ceo", "workflow.landing-page-launch.ship_authorization") in _Agent.calls
