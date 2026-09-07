"""08-29 self-evolution R4: directive → extension scaffold → approval card → isolated run."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kompany.core.artifact_evolution import incubation as inc
from kompany.core.artifact_evolution.tiers import ArtifactRejected
from kompany.core.artifact_evolution.workspace import ArtifactWorkspace
from kompany.core.engine import KompanyEngine
from kompany.core.engine_parts import extensions as ext_ops

MAIN = '''
import json

def run(job, host):
    host.write("notes/last.json", json.dumps(job))
    host.log("counted")
    return {"count": len(job.get("items", [])), "echo": job.get("x")}
'''


def _plan(**over):
    base = dict(name="Item Counter", description="Counts items and keeps the last job.", version="0.1.0",
                core_api=">=0.1,<0.3", tools=[], paths=["notes/"], network=[], credentials=[], budget_usd=0.0,
                main_py=MAIN, soul_yaml="", summary="scaffold item counter")
    base.update(over)
    return inc.IncubatedPlugin(**base)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.1.5")
    return KompanyEngine()


def _llm(engine, monkeypatch, plan, cost=0.02):
    monkeypatch.setattr(engine.llm, "call_structured",
                        lambda **kw: SimpleNamespace(parsed=plan, cost_usd=cost))


def test_incubation_scaffolds_installs_and_runs_after_approval(engine, monkeypatch):
    _llm(engine, monkeypatch, _plan())
    row = engine.evolution_propose("plugin", "acme.counter", "Count items in a job and remember the last one")
    assert row["status"] == "applied" and row["kind"] == "plugin" and row["target"] == "acme.counter"
    assert row["extension"]["status"] == "installed" and row["extension"]["approval_id"]
    ws = ArtifactWorkspace(engine.settings.data_dir)
    scaffold = ws.plugins / "acme.counter"
    assert (scaffold / "extension.json").is_file() and (scaffold / "main.py").is_file() and (scaffold / "README.md").is_file()
    assert ws.log()[0]["message"].startswith("incubate(plugin)")
    manifest = json.loads((scaffold / "extension.json").read_text())
    assert manifest["origin"] == "incubated" and manifest["capabilities"]["paths"] == ["notes/"]
    # not runnable until the founder approves the activation card
    assert engine.extension_run("acme.counter", {"items": [1, 2]})["ok"] is False
    engine.approve_request(row["extension"]["approval_id"])
    out = engine.extension_run("acme.counter", {"items": [1, 2, 3], "x": "hi"})
    assert out["ok"] is True and out["result"] == {"count": 3, "echo": "hi"}
    assert (Path(engine.settings.data_dir) / "extensions/acme.counter/data/notes/last.json").is_file()
    assert [f["kind"] for f in row["flags"]] == []  # no tools/network/credentials/budget → nothing to flag
    assert engine.evolution_show(row["id"])["cost_usd"] == 0.02
    # evolving it again needs a version bump and flags new capabilities
    _llm(engine, monkeypatch, _plan(version="0.1.0", network=["api.example.com"]))
    same = engine.evolution_propose("plugin", "acme.counter", "add fetching")
    assert same["status"] == "rejected" and "bump the version" in same["error"]
    _llm(engine, monkeypatch, _plan(version="0.2.0", network=["api.example.com"], credentials=["example"], budget_usd=1.0))
    bumped = engine.evolution_propose("plugin", "acme.counter", "add fetching")
    assert bumped["status"] == "applied"
    assert {f["kind"] for f in bumped["flags"]} == {"capability_network", "capability_credentials", "budget_increase"}
    assert any(f["kind"] == "capability_credentials" and f["severity"] == "high" for f in bumped["flags"])
    assert engine.extensions.get("acme.counter")["version"] == "0.2.0" and engine.extensions.get("acme.counter")["status"] == "installed"


def test_source_and_manifest_guards_reject_without_writing(engine, monkeypatch):
    ws = ArtifactWorkspace(engine.settings.data_dir)
    for bad, msg in (
        (_plan(main_py="def run(job, host):\n    return (\n"), "does not parse"),
        (_plan(main_py="def start(job):\n    return 1\n"), "must define run"),
        (_plan(main_py="import subprocess\n\ndef run(job, host):\n    return 1\n"), "imports subprocess"),
        (_plan(main_py="def run(job, host):\n    return open('/etc/passwd').read()\n"), "calls open()"),
        (_plan(main_py="def run(job, host):\n    return eval('1')\n"), "calls eval()"),
        (_plan(paths=["/etc"]), "manifest invalid"),
        (_plan(soul_yaml="role: ceo\ndisplay_name: Boss\n"), "reserved"),
    ):
        _llm(engine, monkeypatch, bad)
        row = engine.evolution_propose("plugin", "acme.bad", "x")
        assert row["status"] == "rejected" and msg in row["error"], (msg, row["error"])
    assert not (ws.plugins / "acme.bad").exists() and engine.extensions.get("acme.bad") is None
    assert len(ws.log()) == 1 if ws.exists() else True


def test_incubation_with_soul_and_founder_revert(engine, monkeypatch):
    soul = "role: counter-keeper\ndisplay_name: Counter Keeper\nsquad: ops\nallowed_tools: []\n"
    _llm(engine, monkeypatch, _plan(soul_yaml=soul))
    row = engine.evolution_propose("plugin", "acme.counter", "x")
    assert row["status"] == "applied"
    ws = ArtifactWorkspace(engine.settings.data_dir)
    assert (ws.souls / "counter-keeper.yaml").is_file()
    from kompany.plugins.loader import discover
    assert "counter-keeper" in {getattr(s, "role", "") for s in discover(engine.settings.data_dir)["soul"]}
    undone = engine.evolution_revert(row["id"], reason="not needed")
    assert undone["status"] == "reverted"
    assert not (ws.plugins / "acme.counter" / "main.py").exists() and not (ws.souls / "counter-keeper.yaml").exists()
    assert engine.extensions.get("acme.counter")["status"] == "removed"


def test_incompatible_core_range_is_blocked_not_run(engine, monkeypatch):
    _llm(engine, monkeypatch, _plan(core_api=">=9.0"))
    row = engine.evolution_propose("plugin", "acme.future", "x")
    assert row["status"] == "applied" and row["extension"]["status"] == "blocked" and "core_api" in row["extension"]["block_reason"]
    engine.approve_request(row["extension"]["approval_id"])
    assert engine.extension_run("acme.future", {})["ok"] is False


def test_doctor_failure_reverts_scaffold_and_extension(engine, monkeypatch):
    _llm(engine, monkeypatch, _plan())
    real = engine.doctor; n = {"c": 0}

    def flaky():
        n["c"] += 1
        rep = real()
        if n["c"] <= 2:  # extension_install runs doctor once itself, then the lane runs it
            rep["children"].append({"id": "workflows", "label": "W", "status": "fail", "detail": "x", "fix": None, "children": []})
        return rep
    monkeypatch.setattr(engine, "doctor", flaky)
    row = engine.evolution_propose("plugin", "acme.counter", "x")
    assert row["status"] == "reverted" and row["revert_sha"]
    assert not (ArtifactWorkspace(engine.settings.data_dir).plugins / "acme.counter" / "main.py").exists()
    assert engine.extensions.get("acme.counter")["status"] == "removed"


def test_surfaces_accept_plugin_kind(engine, monkeypatch):
    _llm(engine, monkeypatch, _plan())
    from fastapi.testclient import TestClient
    from kompany.interfaces import api
    from kompany.interfaces.mcp_dispatch import dispatch_tool
    monkeypatch.setattr(api, "_engine", engine)
    row = TestClient(api.app).post("/evolution/propose", json={"kind": "plugin", "target": "acme.counter", "instruction": "x"}).json()
    assert row["status"] == "applied" and row["extension"]["id"] == "acme.counter"
    _llm(engine, monkeypatch, _plan(version="0.3.0"))
    row2 = dispatch_tool(engine, "kompany_evolution_propose", {"kind": "plugin", "target": "acme.counter", "instruction": "y"})
    assert row2["status"] == "applied"
    from typer.testing import CliRunner
    from kompany.interfaces.cli import app
    monkeypatch.setattr("kompany.interfaces.cli_evolution._engine", lambda config=None: engine)
    res = CliRunner().invoke(app, ["evolve", "list", "--json"])
    assert res.exit_code == 0 and "acme.counter" in res.output
