"""08-29 self-evolution PR1: workspace artifact layer + doctor self-test gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kompany.core.artifact_evolution.workspace import ArtifactWorkspace, workspace_root
from kompany.core.doctor import KIND_DOCTOR_FAILED, run_doctor
from kompany.core.engine import KompanyEngine
from kompany.plugins.loader import discover

SOUL = """role: growth-hacker
display_name: Growth Hacker
squad: growth
personality:
  tone: scrappy
"""
WORKFLOW = """workflow_id: evolved-check
display_name: Evolved check
inputs:
  - name: topic
    required: true
    example: "x"
steps:
  - id: one
    agent_role: cv
    cost_estimate_usd: 0.10
    autonomy_tier: auto
    prompt_template: |
      Look at {topic}.
"""


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    return KompanyEngine()


def test_workspace_init_commit_revert_log(tmp_path):
    ws = ArtifactWorkspace(tmp_path)
    assert workspace_root(tmp_path) == tmp_path / "artifacts" and not ws.exists()
    ws.ensure(); ws.ensure()
    assert ws.exists() and (ws.souls / ".gitkeep").exists() and len(ws.log()) == 1
    assert ws.commit("nothing") is None
    (ws.souls / "growth-hacker.yaml").write_text(SOUL)
    assert ws.is_dirty()
    sha = ws.commit("Add growth hacker soul")
    assert sha and ws.head() == sha and not ws.is_dirty()
    assert ws.status()["souls"] == ["growth-hacker.yaml"]
    rev = ws.revert(sha, reason="doctor failed")
    assert rev != sha and not (ws.souls / "growth-hacker.yaml").exists()
    assert ws.log()[0]["message"].startswith("Revert") and "doctor failed" in ws.log()[0]["message"]


def test_loader_merges_workspace_after_builtin_and_pro(tmp_path):
    ws = ArtifactWorkspace(tmp_path); ws.ensure()
    (ws.souls / "growth-hacker.yaml").write_text(SOUL)
    (ws.souls / "ceo.yaml").write_text("role: ceo\ndisplay_name: Fake CEO\n")  # reserved → refused
    (ws.workflows / "evolved-check.yaml").write_text(WORKFLOW)
    (ws.workflows / "dup.yaml").write_text(WORKFLOW.replace("evolved-check", "idea-validation"))  # builtin id → refused
    found = discover(tmp_path)
    roles = {getattr(s, "role", "") for s in found["soul"]}
    assert "growth-hacker" in roles and not any(getattr(s, "display_name", "") == "Fake CEO" for s in found["soul"])
    wids = [getattr(w, "workflow_id", "") for w in found["workflow"]]
    assert "evolved-check" in wids and wids.count("idea-validation") == 0
    errs = {(g, n) for g, n, _ in found["_errors"]}
    assert ("workspace.souls", "ceo.yaml") in errs and ("workspace.workflows", "dup.yaml") in errs
    assert not any(getattr(w, "origin", "") == "workspace" for w in discover(tmp_path, include_workspace=False)["workflow"])
    # no workspace dir → nothing added, no error
    assert not any(g.startswith("workspace") for g, _, _ in discover(tmp_path / "elsewhere").get("_errors", []))


def test_workspace_workflow_resolves_in_registry_and_engine(engine):
    ws = ArtifactWorkspace(engine.settings.data_dir); ws.ensure()
    (ws.workflows / "evolved-check.yaml").write_text(WORKFLOW); ws.commit("add workflow")
    from kompany.core import workflows_registry as reg
    assert "evolved-check" in reg.list_workflows()
    runner = reg.get("evolved-check")
    assert runner.workflow_id == "evolved-check" and len(runner.steps) == 1
    assert "evolved-check" in {w["workflow_id"] for w in engine.workflows_list()}
    out = engine.run_workflow("evolved-check", {"topic": "pricing"}, dry_run=True)
    assert out["status"] == "dry_run" and "pricing" in json.dumps(out)


def test_doctor_persists_and_mirrors_failures(engine):
    rep = engine.doctor()
    ids = {n["id"] for n in rep["children"]}
    assert {"souls", "workflows", "plugins", "ledger", "artifacts"} <= ids
    d = Path(engine.settings.data_dir) / "doctor"
    last = json.loads((d / "last.json").read_text())
    assert last["summary"]["checked_at"] == rep["summary"]["checked_at"]
    assert len((d / "history.jsonl").read_text().splitlines()) == 1
    assert engine.health_events.list(status="open", kind=KIND_DOCTOR_FAILED) == []
    # break a workspace soul → souls node fails → one doctor_failed event
    ws = ArtifactWorkspace(engine.settings.data_dir); ws.ensure()
    (ws.souls / "bad.yaml").write_text("role: ceo\ndisplay_name: Nope\n"); ws.commit("bad soul")
    rep = engine.doctor()
    souls = next(n for n in rep["children"] if n["id"] == "souls")
    assert souls["status"] == "fail" and "bad.yaml" in souls["fix"]
    ev = engine.health_events.list(status="open", kind=KIND_DOCTOR_FAILED)
    assert len(ev) == 1 and ev[0]["detail"]["nodes"][0]["id"] == "souls"
    engine.doctor()
    assert len(engine.health_events.list(status="open", kind=KIND_DOCTOR_FAILED)) == 1  # deduped
    (ws.souls / "bad.yaml").unlink(); ws.commit("fix")
    rep = engine.doctor()
    assert next(n for n in rep["children"] if n["id"] == "souls")["status"] == "ok"
    assert engine.health_events.list(status="open", kind=KIND_DOCTOR_FAILED) == []
    assert len((d / "history.jsonl").read_text().splitlines()) == 4
    # dirty workspace → warn with the commit hint
    (ws.workflows / "wip.yaml").write_text("workflow_id: wip\nsteps: []\n")
    art = next(n for n in engine.doctor()["children"] if n["id"] == "artifacts")
    assert art["status"] == "warn" and "commit" in art["fix"]
    # read-only run leaves persistence untouched
    n_before = len((d / "history.jsonl").read_text().splitlines())
    run_doctor(engine, persist=False)
    assert len((d / "history.jsonl").read_text().splitlines()) == n_before


def test_ledger_check_flags_divergence(engine):
    from kompany.state.models import LedgerCategory
    engine.ledger.record(amount=100.0, description="seed", category=LedgerCategory("income"))
    ok = next(n for n in engine.doctor()["children"] if n["id"] == "ledger")
    assert ok["status"] == "ok"
    engine.db.execute("UPDATE ledger SET balance_after = balance_after + 5 WHERE id = (SELECT MAX(id) FROM ledger)")
    engine.db.commit()
    if engine.db.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]:
        bad = next(n for n in engine.doctor()["children"] if n["id"] == "ledger")
        assert bad["status"] == "fail" and "running balance" in bad["detail"]


def test_boot_doctor_runs_off_thread(engine):
    engine._boot_doctor()
    assert (Path(engine.settings.data_dir) / "doctor" / "last.json").exists()


def test_extension_install_triggers_doctor(engine, tmp_path):
    from kompany.core.engine_parts import extensions as ext_ops
    d = tmp_path / "pkg"; d.mkdir(); (d / "main.py").write_text("def run(job, host):\n    return {}\n")
    (d / "extension.json").write_text(json.dumps({"id": "acme.x", "name": "X", "version": "1.0.0"}))
    row = engine.extension_install(d)
    assert row["doctor_status"] in ("ok", "warn", "fail")
    assert (Path(engine.settings.data_dir) / "doctor" / "last.json").exists()
