"""Reference workflows TTFS (09-06): declared inputs, validation before spend,
``source:`` auto-fill, dry run on all four surfaces, ``workflows show``."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from typer.testing import CliRunner

from kompany.core import workflows_registry
from kompany.core.engine import KompanyEngine
from kompany.core.workflow_inputs import (
    KNOWN_SOURCES,
    WorkflowInputsMissing,
    resolve_inputs,
    resolve_source,
)
from kompany.core.workflow_runner import WorkflowRunner, WorkflowYAMLInvalid
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory
from kompany.state.targets import CompanyTargets

BUILTIN_IDS = ("idea-validation", "weekly-exec-review", "landing-page-launch")


@dataclass
class _Resp:
    text: str
    cost_usd: float = 0.01


class _Agent:
    def __init__(self, role):
        self.role = role

    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        return _Resp(text=f"{self.role}:{prompt[:40]}")


class _CountingRegistry:
    def __init__(self):
        self.calls = 0

    def get(self, role, company_state=None):
        self.calls += 1
        return _Agent(role)


@pytest.fixture()
def engine(monkeypatch):
    monkeypatch.setattr("kompany.plugins.loader.discover", lambda: {"workflow": []})
    eng = KompanyEngine()
    eng.registry = _CountingRegistry()
    return eng


def _wf(**extra) -> dict:
    return {
        "workflow_id": "wf",
        "display_name": "WF",
        "steps": [{"id": "a", "agent_role": "ceo", "prompt_template": "Idea: {idea}"}],
        **extra,
    }


# ----- D1: YAML ``inputs:`` validation -------------------------------------


def test_inputs_block_optional_and_normalised():
    assert WorkflowRunner(_wf()).inputs == []
    r = WorkflowRunner(_wf(inputs=[{"name": "idea", "example": "x"}]))
    assert r.inputs == [
        {"name": "idea", "description": None, "required": True, "source": None,
         "default": None, "example": "x"},
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"inputs": "idea"},
        {"inputs": [{"description": "no name"}]},
        {"inputs": [{"name": "idea"}, {"name": "idea"}]},
        {"inputs": [{"name": "idea", "required": "yes"}]},
        {"inputs": [{"name": "idea", "source": 3}]},
        {"inputs": [{"name": "idea", "bogus": 1}]},
    ],
)
def test_inputs_block_invalid_shapes_raise(bad):
    with pytest.raises(WorkflowYAMLInvalid):
        WorkflowRunner(_wf(**bad))


def test_missing_inputs_ignores_optional_and_none_counts_as_absent():
    r = WorkflowRunner(_wf(inputs=[{"name": "idea"}, {"name": "tone", "required": False}]))
    assert r.missing_inputs({}) == ["idea"]
    assert r.missing_inputs({"idea": None}) == ["idea"]
    assert r.missing_inputs({"idea": "x"}) == []


def test_template_placeholders_extracts_root_names():
    r = WorkflowRunner(_wf(inputs=[{"name": "idea"}]))
    r._data["steps"].append(
        {"id": "b", "agent_role": "cfo", "prompt_template": "{a} then {idea} {{literal}}"}
    )
    assert r.template_placeholders() == {"idea", "a"}


@pytest.mark.parametrize("workflow_id", BUILTIN_IDS)
def test_builtin_placeholders_subset_of_inputs_and_step_ids(workflow_id):
    runner = workflows_registry.get(workflow_id)
    declared = {i["name"] for i in runner.inputs} | {s["id"] for s in runner.steps}
    undeclared = runner.template_placeholders() - declared
    assert not undeclared, f"{workflow_id} prompts reference undeclared {sorted(undeclared)}"
    # Every declared input has a real description and example.
    for spec in runner.inputs:
        assert spec["description"], f"{workflow_id}:{spec['name']} lacks description"
        assert spec["example"] is not None, f"{workflow_id}:{spec['name']} lacks example"
        if spec["source"]:
            assert spec["source"] in KNOWN_SOURCES


def test_workflows_list_rows_carry_inputs(engine):
    rows = {r["workflow_id"]: r for r in engine.workflows_list()}
    idea = rows["idea-validation"]["inputs"]
    assert [i["name"] for i in idea] == ["idea"] and idea[0]["required"] is True
    weekly = {i["name"]: i for i in rows["weekly-exec-review"]["inputs"]}
    assert len(weekly) == 7 and all(not i["required"] for i in weekly.values())
    assert weekly["budget_remaining_usd"]["source"] == "company.budget_remaining_usd"


# ----- D1: missing inputs fail BEFORE spend ---------------------------------


def test_missing_required_input_raises_before_any_spend(engine):
    before_audit = len(engine.audit.recent(limit=200))
    with pytest.raises(WorkflowInputsMissing) as ei:
        engine.run_workflow("idea-validation")
    exc = ei.value
    assert [m["name"] for m in exc.missing] == ["idea"]
    assert exc.example_payload == {"idea": exc.missing[0]["example"]}
    assert "--json-inputs" in str(exc) and "no cost was booked" in str(exc)
    assert exc.to_dict()["error"] == "workflow_inputs_missing"
    # Nothing ran: no registry call, no audit row, no ledger movement.
    assert engine.registry.calls == 0
    assert len(engine.audit.recent(limit=200)) == before_audit
    assert not any(
        r["event_type"].startswith("workflow.") for r in engine.audit.recent(limit=200)
    )


def test_missing_input_also_raises_for_dry_run(engine):
    with pytest.raises(WorkflowInputsMissing):
        engine.run_workflow("landing-page-launch", dry_run=True)


def test_explicit_inputs_run_and_prompts_have_no_literal_placeholder(engine):
    out = engine.run_workflow("idea-validation", {"idea": "meal kits"})
    assert out["ok"] and out["status"] == "completed"
    assert all("{idea}" not in str(s["output"]) for s in out["steps"])
    assert engine.registry.calls == 3


# ----- D2: ``source:`` auto-fill --------------------------------------------


def test_ledger_window_helpers(tmp_path):
    led = Ledger(Database(tmp_path))
    led.record(amount=100.0, description="Initial capital", category=LedgerCategory.INCOME,
               approved_by="master")
    led.record(amount=40.0, description="sale", category=LedgerCategory.INCOME)
    led.record_ai_cost(amount_usd=0.5, description="r1")
    led.record(amount=-9.5, description="domain", category=LedgerCategory.EXPENSE)
    assert led.spent_in_window(days=7) == pytest.approx(10.0)
    assert led.revenue_in_window(days=7) == pytest.approx(40.0)  # founder deposit excluded
    # Backdate everything beyond the window → zero.
    led.db.execute("UPDATE ledger SET timestamp = datetime('now', '-30 days')")
    led.db.commit()
    assert led.spent_in_window(days=7) == 0.0 and led.revenue_in_window(days=7) == 0.0
    with pytest.raises(ValueError):
        led.spent_in_window(days=0)
    with pytest.raises(ValueError):
        led.revenue_in_window(days=0)


def test_resolve_source_covers_every_known_source(engine):
    engine.initialize_company(name="Acme", capital=500.0, goal="Sell kits")
    engine.ledger.record(amount=25.0, description="sale", category=LedgerCategory.INCOME)
    engine.ledger.record_ai_cost(amount_usd=1.5, description="llm")
    engine.set_targets(CompanyTargets(
        initial_budget=500.0, revenue_target=10000.0, customer_target=50,
        deadline="2026-12-31T00:00:00+00:00", source="founder",
    ))
    got = {src: resolve_source(engine, src) for src in KNOWN_SOURCES}
    assert got["company.budget_remaining_usd"] == pytest.approx(523.5)
    assert got["company.spend_last_7d_usd"] == pytest.approx(1.5)
    assert got["company.revenue_last_7d_usd"] == pytest.approx(25.0)
    assert got["company.new_customers_last_7d"] == 0
    assert got["company.revenue_target_usd"] == 10000.0
    assert got["company.customer_target"] == 50
    assert got["company.deadline"] == "2026-12-31T00:00:00+00:00"
    assert got["company.name"] == "Acme" and got["company.goal"] == "Sell kits"
    assert resolve_source(engine, "company.unknown") is None


def test_weekly_review_autofills_and_explicit_wins(engine):
    engine.initialize_company(name="Acme", capital=800.0, goal="g")
    engine.set_targets(CompanyTargets(revenue_target=5000.0, customer_target=20, source="founder"))
    runner = workflows_registry.get("weekly-exec-review")
    scope = resolve_inputs(engine, runner, {"revenue_last_7d_usd": 999})
    assert scope["budget_remaining_usd"] == pytest.approx(800.0)
    assert scope["revenue_target_usd"] == 5000.0 and scope["customer_target"] == 20
    assert scope["revenue_last_7d_usd"] == 999  # explicit beats ledger (0.0)
    assert scope["deadline"] == "not set"  # source None → default
    assert scope["new_customers_last_7d"] == 0


def test_fresh_company_weekly_review_runs_with_defaults(engine):
    out = engine.run_workflow("weekly-exec-review", dry_run=True)
    assert out["status"] == "dry_run"
    inputs = out["inputs"]
    assert inputs["budget_remaining_usd"] == 0.0 and inputs["customer_target"] == "not set"
    cfo_prompt = out["steps"][0]["prompt"]
    assert "Deadline: not set" in cfo_prompt and "Never invent" in cfo_prompt
    assert "{" not in cfo_prompt  # every metric resolved, nothing literal
    # And a real run goes through with the fake registry.
    real = engine.run_workflow("weekly-exec-review")
    assert real["ok"] and engine.registry.calls == 4


def test_undeclared_explicit_keys_pass_through_for_plugin_yamls(engine):
    runner = WorkflowRunner(_wf())  # no inputs block
    assert resolve_inputs(engine, runner, {"brief": "x"}) == {"brief": "x"}


# ----- D3: dry run on every surface ------------------------------------------


def _assert_dry_envelope(out, workflow_id="idea-validation"):
    assert out["status"] == "dry_run" and out["ok"] is True
    assert out["total_cost_usd"] == 0.0 and out["run_id"] is None
    assert out["workflow_id"] == workflow_id
    assert out["inputs"] == {"idea": "meal kits"}
    assert out["estimated_cost_usd"] == pytest.approx(1.2)
    assert [s["step_id"] for s in out["steps"]] == ["probe_demand", "draft_one_pager", "go_no_go"]
    first = out["steps"][0]
    assert {"step_id", "agent_role", "autonomy_tier", "cost_estimate_usd", "prompt"} <= set(first)
    assert "Idea: meal kits" in first["prompt"]
    # Prior-step placeholders stay verbatim so the founder sees where output lands.
    assert "{probe_demand}" in out["steps"][1]["prompt"]


def test_dry_run_engine_no_spend_no_audit_no_card(engine):
    before = len(engine.audit.recent(limit=200))
    out = engine.run_workflow("idea-validation", {"idea": "meal kits"}, dry_run=True)
    _assert_dry_envelope(out)
    assert engine.registry.calls == 0
    assert len(engine.audit.recent(limit=200)) == before
    assert engine.ledger.get_balance() == 0.0
    gated = engine.run_workflow("landing-page-launch", {"product": "p"}, dry_run=True)
    assert gated["steps"][-1]["autonomy_tier"] == "approval"
    assert engine.approvals.list_pending() == []


def test_dry_run_sdk(engine, monkeypatch):
    from kompany.interfaces.sdk import Kompany

    k = Kompany.__new__(Kompany)
    k._engine = engine
    _assert_dry_envelope(k.run_workflow("idea-validation", {"idea": "meal kits"}, dry_run=True))
    with pytest.raises(WorkflowInputsMissing):
        k.run_workflow("idea-validation")


def test_dry_run_mcp(engine):
    from kompany.interfaces.mcp_dispatch import dispatch_tool
    from kompany.interfaces.mcp_tools.ops_status import TOOLS as _tools

    out = dispatch_tool(engine, "kompany_workflow_run",
                        {"workflow_id": "idea-validation", "inputs": {"idea": "meal kits"},
                         "dry_run": True})
    _assert_dry_envelope(out)
    err = dispatch_tool(engine, "kompany_workflow_run", {"workflow_id": "idea-validation"})
    assert err["error"] == "workflow_inputs_missing" and err["example_inputs"]["idea"]
    schema = next(t for t in _tools if t.name == "kompany_workflow_run").inputSchema
    assert schema["properties"]["dry_run"]["type"] == "boolean"


def test_dry_run_rest(engine, monkeypatch):
    from fastapi.testclient import TestClient

    from kompany.interfaces import api

    monkeypatch.setattr(api, "_engine", engine)
    client = TestClient(api.app)
    r = client.post("/workflows/idea-validation/run",
                    json={"inputs": {"idea": "meal kits"}, "dry_run": True})
    assert r.status_code == 200
    _assert_dry_envelope(r.json())
    r = client.post("/workflows/idea-validation/run", json={})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "workflow_inputs_missing"
    rows = {row["workflow_id"]: row for row in client.get("/workflows").json()}
    assert rows["idea-validation"]["inputs"][0]["name"] == "idea"


def test_dry_run_cli_and_show(engine, monkeypatch):
    from kompany.interfaces import cli

    monkeypatch.setattr(cli, "_get_engine", lambda config=None: engine)
    monkeypatch.setenv("COLUMNS", "240")  # keep rich from eliding table cells
    runner = CliRunner()
    res = runner.invoke(cli.app, ["workflows", "run", "idea-validation",
                                  "--json-inputs", '{"idea": "meal kits"}', "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "dry run" in res.output and "$0.00 spent" in res.output
    assert engine.registry.calls == 0

    res = runner.invoke(cli.app, ["workflows", "run", "idea-validation"])
    assert res.exit_code == 1
    assert "missing required input" in res.output and "--json-inputs" in res.output

    res = runner.invoke(cli.app, ["workflows", "show", "idea-validation"])
    assert res.exit_code == 0, res.output
    for needle in ("Validate a business idea", "Inputs", "idea", "Steps", "probe_demand",
                   "Total estimate", "kompany workflows run idea-validation --json-inputs"):
        assert needle in res.output, needle

    res = runner.invoke(cli.app, ["workflows", "show", "weekly-exec-review"])
    assert res.exit_code == 0 and "company.budget_remaining_usd" in res.output

    res = runner.invoke(cli.app, ["workflows", "show", "idea-validation", "--json"])
    assert res.exit_code == 0
    row = json.loads(res.output)
    assert row["workflow_id"] == "idea-validation" and row["inputs"][0]["name"] == "idea"

    res = runner.invoke(cli.app, ["workflows", "show", "nope"])
    assert res.exit_code == 1 and "not found" in res.output
