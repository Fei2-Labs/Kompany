"""Studio PR1: per-role activity replay on four surfaces + skin preference."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kompany.core.engine import KompanyEngine
from kompany.interfaces import api
from kompany.state.models import LedgerCategory


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    return KompanyEngine()


def test_replay_merges_audit_and_spend_for_one_role(engine):
    engine.audit.record("tool_action.inline", "Executed read-only tool inline: web.search", detail={"tool_name": "web.search"}, agent_role="cmo")
    engine.audit.record("workflow.step", "drafting bio variants", detail={"summary": "3 variants"}, agent_role="CMO")
    engine.audit.record("tool_action.inline", "cfo did something", agent_role="cfo")
    engine.ledger.record_ai_cost(amount_usd=0.031, description="cmo: economy call 2.1k tokens", run_id=None) if "amount_usd" in engine.ledger.record_ai_cost.__code__.co_varnames else engine.ledger.record(amount=-0.031, description="AI: cmo economy", category=LedgerCategory("ai_cost"))
    out = engine.activity_recent("CMO")
    assert out["role"] == "cmo" and out["count"] == 3
    kinds = [ln["kind"] for ln in out["lines"]]
    assert set(kinds) == {"tool", "turn", "spend"}
    tool = next(ln for ln in out["lines"] if ln["kind"] == "tool")
    assert tool["detail"] == "tool_name=web.search" and tool["source"] == "audit"
    spend = next(ln for ln in out["lines"] if ln["kind"] == "spend")
    assert spend["text"].startswith("$0.03") and spend["event_type"] == "llm.spend"
    assert all("cfo" not in ln["text"] for ln in out["lines"])
    assert out["lines"] == sorted(out["lines"], key=lambda x: (str(x["ts"]), x["source"]))
    assert engine.activity_recent("cmo", limit=1)["count"] == 1
    with pytest.raises(ValueError):
        engine.activity_recent("  ")


def test_surfaces_parity_and_skin_pref(engine, monkeypatch):
    engine.audit.record("tool_action.inline", "x", agent_role="ceo")
    monkeypatch.setattr(api, "_engine", engine)
    c = TestClient(api.app)
    rest = c.get("/activity/ceo?limit=5").json()
    assert rest["count"] == 1 and rest == engine.activity_recent("ceo", 5)
    assert c.get("/activity/%20").status_code == 422
    from kompany.interfaces.mcp_dispatch import dispatch_tool
    from kompany.interfaces.mcp_server import TOOLS
    assert dispatch_tool(engine, "kompany_activity_recent", {"role": "ceo"})["count"] == 1
    assert any(t.name == "kompany_activity_recent" for t in TOOLS)
    from kompany.interfaces.sdk import Kompany
    sdk = Kompany.__new__(Kompany); sdk._engine = engine
    assert sdk.activity_recent("ceo")["count"] == 1
    from typer.testing import CliRunner
    from kompany.interfaces.cli import app
    monkeypatch.setattr("kompany.interfaces.cli_parts.control._get_engine", lambda config=None: engine)
    res = CliRunner().invoke(app, ["activity", "ceo", "--json"])
    assert res.exit_code == 0 and '"count": 1' in res.output
    # skin preference: default indigo, switchable, validated
    assert c.get("/preferences").json()["skin"] == "indigo"
    assert c.patch("/preferences", json={"skin": "paper"}).json()["skin"] == "paper"
    assert c.patch("/preferences", json={"skin": "neon"}).status_code == 422
    assert engine.get_ui_preferences().skin == "paper" and engine.get_ui_preferences().start_page == "board"
