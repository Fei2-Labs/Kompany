"""08-29 self-evolution R2: propose → validate → commit → doctor → auto-revert."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kompany.core.artifact_evolution import pipeline as pl
from kompany.core.artifact_evolution.tiers import ArtifactRejected, privilege_flags, validate_soul, validate_workflow
from kompany.core.artifact_evolution.workspace import ArtifactWorkspace
from kompany.core.engine import KompanyEngine

SOUL_YAML = """role: growth-hacker
display_name: Growth Hacker
squad: growth
model_tier: economy
personality:
  tone: scrappy
allowed_tools:
  - web.search
"""
WF_YAML = """workflow_id: cold-outreach
display_name: Cold outreach
steps:
  - id: draft
    agent_role: cmo
    cost_estimate_usd: 0.2
    autonomy_tier: auto
    prompt_template: Draft outreach for {segment}
"""


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    return KompanyEngine()


def _llm(engine, monkeypatch, yaml_text, summary="evolve", cost=0.01, calls=None):
    def call_structured(**kw):
        if calls is not None:
            calls.append(kw)
        return SimpleNamespace(parsed=pl.EvolvedArtifact(yaml_text=yaml_text, summary=summary, rationale="r"), cost_usd=cost)
    monkeypatch.setattr(engine.llm, "call_structured", call_structured)


# ---------------------------------------------------------------------------
# validation + flags (pure)
# ---------------------------------------------------------------------------

def test_validate_soul_rules():
    data = {"role": "growth-hacker", "display_name": "GH"}
    validate_soul(data, "growth-hacker.yaml", taken_roles=set(), existing=None)
    with pytest.raises(ArtifactRejected, match="reserved"):
        validate_soul({"role": "ceo", "display_name": "x"}, "ceo.yaml", taken_roles=set(), existing=None)
    with pytest.raises(ArtifactRejected, match="already provided"):
        validate_soul(data, "growth-hacker.yaml", taken_roles={"growth-hacker"}, existing=None)
    validate_soul(data, "growth-hacker.yaml", taken_roles={"growth-hacker"}, existing={"role": "growth-hacker"})
    with pytest.raises(ArtifactRejected, match="change its role"):
        validate_soul(data, "growth-hacker.yaml", taken_roles=set(), existing={"role": "other"})
    with pytest.raises(ArtifactRejected, match="file name"):
        validate_soul(data, "hacker.yaml", taken_roles=set(), existing=None)
    with pytest.raises(ArtifactRejected, match="allowed_tools"):
        validate_soul({**data, "allowed_tools": "web.*"}, "growth-hacker.yaml", taken_roles=set(), existing=None)


def test_validate_workflow_rules():
    import yaml
    data = yaml.safe_load(WF_YAML)
    validate_workflow(data, "cold-outreach.yaml", taken_ids=set(), existing=None)
    with pytest.raises(ArtifactRejected, match="already provided"):
        validate_workflow(data, "cold-outreach.yaml", taken_ids={"cold-outreach"}, existing=None)
    with pytest.raises(ArtifactRejected, match="python_callable"):
        validate_workflow({**data, "steps": [{**data["steps"][0], "python_callable": "x"}]}, "cold-outreach.yaml", taken_ids=set(), existing=None)
    with pytest.raises(ArtifactRejected, match="invalid workflow"):
        validate_workflow({"workflow_id": "cold-outreach", "steps": []}, "cold-outreach.yaml", taken_ids=set(), existing=None)


def test_privilege_flags():
    old = {"allowed_tools": ["web.search"]}
    new = {"allowed_tools": ["web.search", "stripe.*"], "model_tier": "apex"}
    flags = privilege_flags("soul", old, new)
    kinds = {f["kind"]: f for f in flags}
    assert kinds["allowed_tools_expansion"]["added"] == ["stripe.*"] and kinds["allowed_tools_expansion"]["severity"] == "high"
    assert "model_tier_apex" in kinds
    assert privilege_flags("soul", {"allowed_tools": ["web.*"]}, {"allowed_tools": ["web.search"]}) == []
    assert [f["kind"] for f in privilege_flags("soul", None, {"allowed_tools": ["web.search"]})] == ["new_role_tools"]
    wf_old = {"steps": [{"id": "a", "cost_estimate_usd": 0.2}]}
    wf_new = {"steps": [{"id": "a", "cost_estimate_usd": 3.0, "autonomy_tier": "approval", "tools": ["email.send"]}]}
    kinds = {f["kind"] for f in privilege_flags("workflow", wf_old, wf_new)}
    assert kinds == {"non_auto_step", "cost_estimate_jump", "new_step_tool"}


# ---------------------------------------------------------------------------
# the lane end to end (mocked LLM, real git, real doctor)
# ---------------------------------------------------------------------------

def test_soul_proposal_applied_and_discoverable(engine, monkeypatch):
    calls = []
    _llm(engine, monkeypatch, SOUL_YAML, summary="add growth hacker", calls=calls)
    row = engine.evolution_propose("soul", "growth-hacker", "Create a scrappy growth hacker role")
    assert row["status"] == "applied" and row["doctor_status"] == "ok" and row["commit_sha"]
    assert calls[0]["action_type"] == "artifact_evolution" and "No current file" in calls[0]["prompt"]
    ws = ArtifactWorkspace(engine.settings.data_dir)
    assert (ws.souls / "growth-hacker.yaml").is_file() and ws.log()[0]["message"].startswith("evolve(soul)")
    from kompany.plugins.loader import discover
    assert "growth-hacker" in {getattr(s, "role", "") for s in discover(engine.settings.data_dir)["soul"]}
    kinds = [e["event_type"] for e in engine.audit.recent(limit=30)] if hasattr(engine.audit, "recent") else []
    assert [f["kind"] for f in row["flags"]] == ["new_role_tools"]
    # evolving the existing soul: the LLM sees the current YAML; widening tools is flagged, not blocked
    _llm(engine, monkeypatch, SOUL_YAML.replace("  - web.search\n", "  - web.search\n  - stripe.*\n"), summary="add stripe", calls=calls)
    row2 = engine.evolution_propose("soul", "growth-hacker", "Let it use Stripe")
    assert row2["status"] == "applied" and "Current YAML" in calls[-1]["prompt"]
    assert any(f["kind"] == "allowed_tools_expansion" and f["added"] == ["stripe.*"] for f in row2["flags"])
    rows = engine.evolution_list()
    assert [r["id"] for r in rows] == [row2["id"], row["id"]]


def test_reserved_role_is_rejected_without_commit(engine, monkeypatch):
    _llm(engine, monkeypatch, "role: ceo\ndisplay_name: Boss\n")
    row = engine.evolution_propose("soul", "ceo", "Replace the CEO")
    assert row["status"] == "rejected" and "reserved" in row["error"] and row["commit_sha"] is None
    ws = ArtifactWorkspace(engine.settings.data_dir)
    assert not (ws.souls / "ceo.yaml").exists() and len(ws.log()) == 1
    assert row["cost_usd"] == 0.01  # the call still cost money and is recorded


def test_workflow_proposal_applied_and_runnable(engine, monkeypatch):
    _llm(engine, monkeypatch, WF_YAML, summary="cold outreach")
    row = engine.evolution_propose("workflow", "cold-outreach", "A one-step cold outreach drafting workflow")
    assert row["status"] == "applied"
    from kompany.core import workflows_registry as reg
    assert "cold-outreach" in reg.list_workflows()
    out = engine.run_workflow("cold-outreach", {"segment": "dentists"}, dry_run=True)
    assert out["status"] == "dry_run"
    # shadowing a builtin id is rejected
    _llm(engine, monkeypatch, WF_YAML.replace("cold-outreach", "idea-validation"))
    bad = engine.evolution_propose("workflow", "idea-validation", "override builtin")
    assert bad["status"] == "rejected" and "already provided" in bad["error"]


def test_doctor_failure_auto_reverts(engine, monkeypatch):
    _llm(engine, monkeypatch, SOUL_YAML)
    real_doctor = engine.doctor
    state = {"n": 0}

    def flaky_doctor():
        state["n"] += 1
        rep = real_doctor()
        if state["n"] == 1:  # first post-apply run fails on a non-llm node
            rep["children"].append({"id": "souls", "label": "Souls", "status": "fail", "detail": "boom", "fix": None, "children": []})
            rep["summary"]["status"] = "fail"
        return rep
    monkeypatch.setattr(engine, "doctor", flaky_doctor)
    row = engine.evolution_propose("soul", "growth-hacker", "x")
    assert row["status"] == "reverted" and row["revert_sha"] and "souls" in row["error"]
    ws = ArtifactWorkspace(engine.settings.data_dir)
    assert not (ws.souls / "growth-hacker.yaml").exists()
    assert ws.log()[0]["message"].startswith("Revert") and state["n"] == 2  # second run clears the event
    events = [e["event_type"] for e in engine.audit.recent(limit=30)] if hasattr(engine.audit, "recent") else None
    if events is not None:
        assert "artifact_evolution.reverted" in events


def test_llm_only_failure_does_not_revert(engine, monkeypatch):
    """No API key on a dev box fails the llm node; that must never undo an artifact."""
    _llm(engine, monkeypatch, SOUL_YAML)
    for k in ("anthropic_api_key", "openai_api_key", "gemini_api_key", "glm_api_key", "kimi_api_key", "custom_api_key"):
        monkeypatch.setattr(engine.settings, k, "", raising=False)
    monkeypatch.setattr("kompany.core.model_source_ops.get_model_source", lambda eng: None)
    row = engine.evolution_propose("soul", "growth-hacker", "x")
    assert row["status"] == "applied"


def test_daily_cap_halts_and_disabled_flag(engine, monkeypatch):
    _llm(engine, monkeypatch, SOUL_YAML, cost=1.5)
    assert engine.evolution_propose("soul", "growth-hacker", "x")["status"] == "applied"
    _llm(engine, monkeypatch, WF_YAML, cost=1.0)
    ok = engine.evolution_propose("workflow", "cold-outreach", "x")
    assert ok["status"] == "applied"  # 1.5 < 2.0 before this call
    halted = engine.evolution_propose("workflow", "cold-outreach", "again")
    assert halted["status"] == "failed" and "daily cap" in halted["error"]
    st = engine.evolution_status()
    assert st["spent_today_usd"] == 2.5 and st["remaining_today_usd"] == 0.0
    monkeypatch.setattr(engine.settings, "artifact_evolution_enabled", False, raising=False)
    assert "disabled" in engine.evolution_propose("soul", "x-role", "x")["error"]


def test_founder_revert_and_bad_inputs(engine, monkeypatch):
    _llm(engine, monkeypatch, SOUL_YAML)
    row = engine.evolution_propose("soul", "growth-hacker", "x")
    undone = engine.evolution_revert(row["id"], reason="did not like it")
    assert undone["status"] == "reverted" and undone["revert_sha"]
    assert not (ArtifactWorkspace(engine.settings.data_dir).souls / "growth-hacker.yaml").exists()
    assert engine.evolution_revert(row["id"])["status"] == "reverted"  # idempotent
    assert engine.evolution_revert("nope") is None
    with pytest.raises(ValueError):
        engine.evolution_propose("plugin", "x", "y")
    with pytest.raises(ValueError):
        engine.evolution_propose("soul", "growth-hacker", "   ")
    with pytest.raises(ValueError):
        engine.evolution_propose("soul", "../etc", "x")


def test_llm_error_recorded_not_raised(engine, monkeypatch):
    def boom(**kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(engine.llm, "call_structured", boom)
    row = engine.evolution_propose("soul", "growth-hacker", "x")
    assert row["status"] == "failed" and "provider down" in row["error"]


def test_surfaces_parity(engine, monkeypatch):
    _llm(engine, monkeypatch, SOUL_YAML)
    from fastapi.testclient import TestClient
    from kompany.interfaces import api
    from kompany.interfaces.mcp_dispatch import dispatch_tool
    from kompany.interfaces.mcp_server import TOOLS
    from kompany.interfaces.sdk import Kompany
    monkeypatch.setattr(api, "_engine", engine)
    c = TestClient(api.app)
    row = c.post("/evolution/propose", json={"kind": "soul", "target": "growth-hacker", "instruction": "x"}).json()
    assert row["status"] == "applied"
    assert c.post("/evolution/propose", json={"kind": "plugin", "target": "x", "instruction": "x"}).status_code == 422
    assert c.get("/evolution/proposals").json()[0]["id"] == row["id"]
    assert c.get(f"/evolution/proposals/{row['id']}").json()["commit_sha"] == row["commit_sha"]
    assert c.get("/evolution").json()["workspace"]["souls"] == ["growth-hacker.yaml"]
    assert dispatch_tool(engine, "kompany_evolution_status", {}) == engine.evolution_status()
    assert dispatch_tool(engine, "kompany_evolution_show", {"proposal_id": row["id"]})["id"] == row["id"]
    assert {t.name for t in TOOLS} >= {"kompany_evolution_propose", "kompany_evolution_list", "kompany_evolution_show",
                                       "kompany_evolution_revert", "kompany_evolution_status"}
    sdk = Kompany.__new__(Kompany); sdk._engine = engine
    assert sdk.evolution_list() == engine.evolution_list()
    assert c.post(f"/evolution/proposals/{row['id']}/revert", json={"reason": "no"}).json()["status"] == "reverted"
    from typer.testing import CliRunner
    from kompany.interfaces.cli import app
    monkeypatch.setattr("kompany.interfaces.cli_evolution._engine", lambda config=None: engine)
    res = CliRunner().invoke(app, ["evolve", "list", "--json"]); assert res.exit_code == 0 and row["id"] in res.output
    res = CliRunner().invoke(app, ["evolve", "status"]); assert res.exit_code == 0 and "Budget today" in res.output
