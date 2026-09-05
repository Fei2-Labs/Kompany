"""kompany doctor (#41): health tree, fix hints, roll-up, four surfaces."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kompany.core.doctor import node, render_tree, run_doctor
from kompany.core.engine import KompanyEngine
from kompany.interfaces import api
from kompany.state.models import ApprovalRequest, Project, ProjectType, Task, TaskStatus


def _ids(root):
    out = {}
    def walk(n):
        out[n["id"]] = n
        for c in n.get("children", []): walk(c)
    walk(root); return out


def test_fresh_engine_tree_has_every_check_and_no_fail():
    rep = run_doctor(KompanyEngine())
    ids = _ids(rep)
    for k in ("database", "runtime", "llm", "health_events", "work", "integrations", "backups", "access", "build"):
        assert k in ids
    assert ids["database"]["status"] == "ok"
    assert ids["backups"]["status"] == "warn" and "kompany backup create" in ids["backups"]["fix"]
    # A test engine has no provider key: llm fails closed; nothing else may fail.
    failing = {k for k, n in ids.items() if n["status"] == "fail"} - {"kompany", "llm"}
    assert failing == set(), failing
    assert rep["summary"]["checked_at"]
    assert any("Backups:" in f for f in rep["summary"]["fixes"])


def test_findings_roll_up_with_fix_hints():
    e = KompanyEngine()
    e.health_events.record("runway_alert", detail={"message": "burn"})
    p = e.projects.create(Project(name="Launch", type=ProjectType.REVENUE))
    t = e.projects.create_task(Task(project_id=p.id, title="Email leads", assigned_agent="cro"))
    e.projects.update_task_status(t.id, TaskStatus.BLOCKED, result={"founder_action": "connect an email account"})
    e.approvals.create(ApprovalRequest(action_type="tool_action", summary="x"))
    e.backups.create_backup(label="fresh")
    rep = run_doctor(e); ids = _ids(rep)
    assert ids["health_events"]["status"] == "fail" and ids["health.runway_alert"]["status"] == "fail"
    assert ids["work.blocked"]["status"] == "warn" and "connect an email account" in ids["work.blocked"]["detail"]
    assert ids["work.approvals"]["detail"] == "1 waiting for you"
    assert ids["backups"]["status"] == "ok"
    assert rep["summary"]["status"] == "fail" and rep["summary"]["fail"] >= 2
    text = render_tree(rep)
    assert "✗ Watchdog events" in text and "fix: Resolve in NEEDS YOU" in text


def test_llm_check_fails_closed_without_provider(monkeypatch):
    e = KompanyEngine()
    for k in ("anthropic_api_key", "openai_api_key", "gemini_api_key", "glm_api_key", "kimi_api_key", "custom_api_key"):
        monkeypatch.setattr(e.settings, k, "", raising=False)
    monkeypatch.setattr("kompany.core.model_source_ops.get_model_source", lambda eng: None)
    ids = _ids(run_doctor(e))
    assert ids["llm"]["status"] == "fail" and "Settings" in ids["llm"]["fix"]
    monkeypatch.setattr(e.settings, "openai_api_key", "sk-x", raising=False)
    assert _ids(run_doctor(e))["llm"]["status"] == "ok"


def test_broken_check_becomes_warn_not_crash(monkeypatch):
    e = KompanyEngine()
    monkeypatch.setattr("kompany.core.doctor.check_backups", lambda eng: (_ for _ in ()).throw(RuntimeError("boom")))
    import kompany.core.doctor as d
    monkeypatch.setattr(d, "CHECKS", tuple((i, l, (d.check_backups if i == "backups" else f)) for i, l, f in d.CHECKS))
    ids = _ids(run_doctor(e))
    assert ids["backups"]["status"] == "warn" and "boom" in ids["backups"]["detail"]


def test_node_rollup_and_info_neutral():
    root = node("r", "R", "ok", children=[node("a", "A", "info"), node("b", "B", "warn")])
    assert root["status"] == "warn"
    assert node("r", "R", "ok", children=[node("a", "A", "info")])["status"] == "ok"


def test_four_surfaces_share_payload(monkeypatch):
    e = KompanyEngine(); monkeypatch.setattr(api, "_engine", e)
    rest = TestClient(api.app).get("/doctor").json()
    from kompany.interfaces import mcp_server
    mcp = asyncio.run(mcp_server.call_tool("kompany_doctor", {}))
    from kompany.interfaces.sdk import Kompany
    sdk = Kompany.__new__(Kompany); sdk._engine = e
    assert set(_ids(rest)) == set(_ids(sdk.doctor()))
    assert any(n.name == "kompany_doctor" for n in mcp_server.TOOLS)
    assert mcp  # dispatch returned content
    from kompany.interfaces.cli import app as cli_app
    monkeypatch.setattr("kompany.interfaces.cli_parts.common._get_engine", lambda config=None: e)
    monkeypatch.setattr("kompany.interfaces.cli_parts.control._get_engine", lambda config=None: e)
    res = CliRunner().invoke(cli_app, ["doctor", "--json"])
    assert res.exit_code == 0, res.output
    assert '"summary"' in res.output
    res2 = CliRunner().invoke(cli_app, ["doctor"])
    assert "Kompany doctor" in res2.output and "Backups" in res2.output
