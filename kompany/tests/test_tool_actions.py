"""Action pipeline + native tools (#4/#5, 06-12-action-pipeline-tools).

Covers the universal pipeline: read-only inline; side-effect/cost →
proposed action → approval → execute → audit; PAID hard-gated never-auto;
loader builtin discovery; tools_list shape + four-interface parity.
"""

from __future__ import annotations

import threading

import pytest
from pydantic import BaseModel

from kompany.core import tool_actions
from kompany.core.autonomy import AutonomyGate
from kompany.core.engine import KompanyEngine
from kompany.plugins.contract import (
    AutonomyTier,
    CostEstimate,
    Integration,
    SideEffect,
    Tool,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _In(BaseModel):
    text: str = "x"


class _Out(BaseModel):
    ok: bool = True
    detail: str = ""
    spent_usd: float = 0.0


class _StructuredOut(BaseModel):
    status: str
    detail: str = ""
    evidence: str = ""


class _EchoTool(Tool):
    name = "test.echo"
    description = "read-only echo"
    input_schema = _In
    output_schema = _Out
    side_effect = SideEffect.READ
    autonomy_tier = AutonomyTier.AUTO

    def estimate_cost(self, inputs):
        return CostEstimate()

    def execute(self, inputs, ctx):
        return _Out(detail=f"echo:{inputs.text}")


class _SendTool(Tool):
    name = "test.send"
    description = "external action"
    input_schema = _In
    output_schema = _Out
    side_effect = SideEffect.EXTERNAL_ACTION
    autonomy_tier = AutonomyTier.APPROVAL
    calls: list[str] = []

    def estimate_cost(self, inputs):
        return CostEstimate()

    def execute(self, inputs, ctx):
        # Prove credentials are injected from the vault.
        key = ctx.credentials.get("custom_api_key") or ""
        if not key:
            return _Out(ok=False, detail="missing credential: custom_api_key")
        _SendTool.calls.append(inputs.text)
        return _Out(detail=f"sent:{inputs.text} key:{key}")


class _PayTool(Tool):
    name = "test.pay"
    description = "paid action"
    input_schema = _In
    output_schema = _Out
    side_effect = SideEffect.SPEND
    autonomy_tier = AutonomyTier.APPROVAL

    def estimate_cost(self, inputs):
        return CostEstimate(external_usd=5.0)

    def execute(self, inputs, ctx):
        return _Out(detail="charged", spent_usd=5.0)


class _FakeIntegration(Integration):
    integration_id = "test_integ"
    display_name = "Test Integration"
    required_credentials = ("custom_api_key",)

    def tools(self):
        return [_EchoTool(), _SendTool(), _PayTool()]


@pytest.fixture()
def engine(monkeypatch):
    monkeypatch.setattr(
        "kompany.plugins.loader.discover",
        lambda: {"integration": [_FakeIntegration()]},
    )
    _SendTool.calls = []
    return KompanyEngine()


# ---------------------------------------------------------------------------
# AutonomyGate hard gates
# ---------------------------------------------------------------------------


def test_gate_read_auto_passes():
    assert AutonomyGate().check_tool("read", "auto", 0.0) is True


def test_gate_side_effect_never_inline():
    assert AutonomyGate().check_tool("external_action", "auto", 0.0) is False


def test_gate_paid_never_auto_even_when_configured_auto():
    gate = AutonomyGate()
    # PAID hard gate: configured-auto policy CANNOT unlock spend.
    assert gate.check_tool("spend", "auto", 0.0, configured_auto=True) is False
    assert gate.check_tool("read", "auto", 0.01, configured_auto=True) is False


# ---------------------------------------------------------------------------
# Inline execution (read-only)
# ---------------------------------------------------------------------------


def test_read_only_tool_executes_inline_without_card(engine):
    out = engine.execute_tool("test.echo", {"text": "hi"})
    assert out["ok"] is True
    assert out["result"]["detail"] == "echo:hi"
    assert engine.inbox() == []  # no approval card filed
    events = [e for e in engine.audit.recent(20) if e["event_type"] == "tool_action.inline"]
    assert events


def test_side_effect_tool_refused_inline(engine):
    out = engine.execute_tool("test.send", {"text": "hi"})
    assert out["ok"] is False
    assert out["requires_approval"] is True
    assert _SendTool.calls == []


def test_paid_tool_refused_inline_even_with_auto_policy(engine):
    out = engine.execute_tool("test.pay", {"text": "buy"})
    assert out["ok"] is False
    assert out["requires_approval"] is True


def test_execute_unknown_tool_raises(engine):
    with pytest.raises(ValueError):
        engine.execute_tool("nope.tool", {})


# ---------------------------------------------------------------------------
# Propose → approve → execute → audit
# ---------------------------------------------------------------------------


def test_propose_approve_executes_with_credentials_and_audit(engine):
    engine.credentials.set("custom_api_key", "k123")
    card = engine.propose_action(
        "test.send", {"text": "hello"}, summary="Send hello", reason="outreach"
    )
    assert card["payload"]["side_effect"] == "external_action"
    assert card["payload"]["estimated_cost_usd"] == 0.0
    assert card["payload"]["reason"] == "outreach"
    assert _SendTool.calls == []  # proposed, NOT executed

    res = engine.approve_request(card["id"], approved_by="master")
    assert res["tool_result"]["ok"] is True
    assert "key:k123" in res["tool_result"]["detail"]
    assert _SendTool.calls == ["hello"]

    # Audit trail: proposed + executed.
    types = [e["event_type"] for e in engine.audit.recent(50)]
    assert "tool_action.proposed" in types
    assert "tool_action.executed" in types

    # Idempotent re-approve: stamp guards a second execution.
    stored = engine.approvals.get(card["id"])
    assert stored.payload["effect_applied"] is True
    res2 = engine.approve_request(card["id"], approved_by="master")
    assert res2["effect"]["status"] == "already_applied"
    assert _SendTool.calls == ["hello"]  # still exactly once


def test_propose_auto_approves_when_policy_allows_no_approval(engine):
    """Founder-tunable auto-approve (#4): a role+tool policy with
    ``allowed=True, requires_approval=False`` skips the human tap —
    ``propose_action`` files the card AND executes it inline through
    the exact same approve pipeline, still fully audited."""
    engine.credentials.set("custom_api_key", "k123")
    engine.set_tool_policy("linkedin_growth", "test.send", allowed=True, requires_approval=False)

    card = engine.propose_action(
        "test.send",
        {"text": "auto"},
        summary="Send auto",
        requested_by="linkedin_growth",
    )

    assert card["status"] == "approved"
    assert card["tool_result"]["ok"] is True
    assert _SendTool.calls == ["auto"]
    types = [e["event_type"] for e in engine.audit.recent(50)]
    assert "tool_action.proposed" in types
    assert "approval.approved" in types
    assert "tool_action.executed" in types


def test_propose_stays_pending_when_policy_requires_approval(engine):
    engine.set_tool_policy("linkedin_growth", "test.send", allowed=True, requires_approval=True)

    card = engine.propose_action(
        "test.send", {"text": "hi"}, summary="Send", requested_by="linkedin_growth"
    )

    assert card["status"] == "pending"
    assert _SendTool.calls == []


def test_propose_stays_pending_with_no_policy(engine):
    card = engine.propose_action(
        "test.send", {"text": "hi"}, summary="Send", requested_by="linkedin_growth"
    )
    assert card["status"] == "pending"
    assert _SendTool.calls == []


def test_propose_never_auto_approves_paid_tool_even_with_policy(engine):
    """PAID hard gate: an auto-approve policy CANNOT unlock a SPEND
    tool — same invariant as ``AutonomyGate.check_tool``."""
    engine.set_tool_policy("linkedin_growth", "test.pay", allowed=True, requires_approval=False)

    card = engine.propose_action(
        "test.pay", {"text": "buy"}, summary="Pay", requested_by="linkedin_growth"
    )

    assert card["status"] == "pending"


def test_reject_never_executes(engine):
    engine.credentials.set("custom_api_key", "k123")
    card = engine.propose_action("test.send", {"text": "no"}, summary="Send")
    engine.reject_request(card["id"], reason="not now")
    assert _SendTool.calls == []
    types = [e["event_type"] for e in engine.audit.recent(50)]
    assert "tool_action.rejected" in types
    assert "tool_action.executed" not in types


def test_paid_tool_books_ledger_expense_on_approved_execution(engine):
    card = engine.propose_action("test.pay", {"text": "buy"}, summary="Pay")
    assert card["payload"]["estimated_cost_usd"] == 5.0
    res = engine.approve_request(card["id"])
    assert res["tool_result"]["spent_usd"] == 5.0
    totals = engine.ledger.get_totals()
    assert totals.get("tool_cost") == -5.0


def test_missing_credentials_fail_on_card_not_crash(engine):
    # No api_key in the vault → execution reports the error on the card,
    # no stamp → fixing credentials and re-approving retries.
    card = engine.propose_action("test.send", {"text": "hi"}, summary="Send")
    res = engine.approve_request(card["id"])
    assert res["tool_result"]["ok"] is False
    assert "missing credential" in res["tool_result"]["detail"]
    stored = engine.approvals.get(card["id"])
    assert not stored.payload.get("effect_applied")
    comments = engine.approvals.list_comments(card["id"])
    assert any("missing credential" in c.body for c in comments)


@pytest.mark.parametrize("status", ["failed", "skipped", "error"])
def test_structured_failure_status_does_not_stamp_effect(engine, status):
    original_execute = _SendTool.execute
    _SendTool.execute = lambda self, inputs, ctx: _StructuredOut(
        status=status,
        detail="not sent",
    )
    try:
        card = engine.propose_action("test.send", {"text": "hi"}, summary="Send")
        result = engine.approve_request(card["id"])
    finally:
        _SendTool.execute = original_execute

    assert result["effect"]["status"] == "failed"
    assert not engine.approvals.get(card["id"]).payload.get("effect_applied")


def test_structured_confirmed_status_stamps_effect_once(engine):
    calls = []
    original_execute = _SendTool.execute
    _SendTool.execute = lambda self, inputs, ctx: calls.append(inputs.text) or _StructuredOut(
        status="confirmed",
        evidence="SENT",
    )
    try:
        card = engine.propose_action("test.send", {"text": "hi"}, summary="Send")
        first = engine.approve_request(card["id"])
        second = engine.approve_request(card["id"])
    finally:
        _SendTool.execute = original_execute

    assert first["effect"]["status"] == "executed"
    assert second["effect"]["status"] == "already_applied"
    assert calls == ["hi"]


def test_cycle_proposal_cap_blocks_second_approval(engine):
    engine.settings.soul_cycle_overrides = {
        "linkedin_growth": {
            "scheduler_mode": "native",
            "max_external_proposals_per_cycle": 1,
        }
    }
    first = engine.propose_action(
        "test.send",
        {"text": "first"},
        summary="First",
        project_id="p1",
        task_id="cycle-1",
        requested_by="linkedin_growth",
        cycle_controls={
            "scheduler_mode": "native",
            "max_external_proposals_per_cycle": 1,
            "auto_execute_tools": ["test.send"],
        },
    )

    with pytest.raises(ValueError, match="proposal_budget_exhausted"):
        engine.propose_action(
            "test.send",
            {"text": "second"},
            summary="Second",
            project_id="p1",
            task_id="cycle-1",
            requested_by="linkedin_growth",
            cycle_controls={
                "scheduler_mode": "native",
                "max_external_proposals_per_cycle": 1,
                "auto_execute_tools": ["test.send"],
            },
        )

    assert first["action_type"] == "tool_action"
    assert first["status"] == "pending"
    assert len(engine.approvals.list_pending()) == 1


def test_cycle_proposal_uses_explicit_auto_approve_policy(engine):
    engine.credentials.set("custom_api_key", "k123")
    engine.set_tool_policy(
        "linkedin_growth",
        "test.send",
        allowed=True,
        requires_approval=False,
    )

    card = engine.propose_action(
        "test.send",
        {"text": "manual"},
        summary="Manual cycle approval",
        project_id="p1",
        task_id="cycle-manual",
        requested_by="linkedin_growth",
        cycle_controls={
            "scheduler_mode": "native",
            "max_external_proposals_per_cycle": 1,
            "auto_execute_tools": ["test.send"],
        },
    )

    assert card["status"] == "approved"
    assert card["effect"]["status"] == "executed"
    assert _SendTool.calls == ["manual"]


def test_disabled_cycle_refuses_new_proposal(engine):
    engine.settings.soul_cycle_overrides = {
        "linkedin_growth": {"scheduler_mode": "disabled"}
    }

    with pytest.raises(ValueError, match="scheduler_disabled"):
        engine.propose_action(
            "test.send",
            {"text": "blocked"},
            summary="Blocked",
            project_id="p1",
            task_id="cycle-1",
            requested_by="linkedin_growth",
            cycle_controls={"scheduler_mode": "native"},
        )

    assert engine.approvals.list_pending() == []


def test_cycle_proposal_refuses_tool_outside_auto_execute_allowlist(engine):
    with pytest.raises(ValueError, match="tool_not_auto_authorized"):
        engine.propose_action(
            "test.send",
            {"text": "blocked"},
            summary="Blocked cycle action",
            project_id="p1",
            task_id="cycle-blocked-tool",
            requested_by="linkedin_growth",
            cycle_controls={
                "scheduler_mode": "native",
                "max_external_proposals_per_cycle": 1,
                "auto_execute_tools": ["linkedin.engage"],
            },
        )

    assert engine.approvals.list_pending() == []


def test_cycle_proposal_cap_is_atomic(engine):
    controls = {
        "scheduler_mode": "native",
        "max_external_proposals_per_cycle": 1,
        "auto_execute_tools": ["test.send"],
    }
    barrier = threading.Barrier(2)
    outcomes = []

    def propose(text):
        barrier.wait()
        try:
            result = engine.propose_action(
                "test.send",
                {"text": text},
                summary=text,
                project_id="p1",
                task_id="cycle-concurrent",
                requested_by="linkedin_growth",
                cycle_controls=controls,
            )
            outcomes.append(result["id"])
        except ValueError as exc:
            outcomes.append(str(exc))

    threads = [
        threading.Thread(target=propose, args=(text,))
        for text in ("a", "b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("proposal_budget_exhausted") == 1
    assert len(engine.approvals.list_pending()) == 1


def test_cycle_controls_reach_proposal_gate_through_registry(engine):
    from kompany.core.agent_tools.base import ToolContext
    from kompany.core.agent_tools.chat_registry import IntegrationToolAdapter
    from kompany.core.agent_tools.registry import ToolRegistry

    engine.settings.soul_cycle_overrides = {
        "linkedin_growth": {"scheduler_mode": "disabled"}
    }
    registry = ToolRegistry([IntegrationToolAdapter(_SendTool())])
    ctx = ToolContext(
        engine=engine,
        extra={
            "project_id": "p1",
            "task_id": "cycle-1",
            "agent_role": "linkedin_growth",
            "cycle_controls": {"scheduler_mode": "disabled"},
        },
    )

    observation = registry.dispatch("test_send", {"text": "blocked"}, ctx)

    assert "scheduler_disabled" in observation
    assert engine.approvals.list_pending() == []


def test_propose_unknown_tool_raises(engine):
    with pytest.raises(ValueError):
        engine.propose_action("nope.tool", {}, summary="x")


# ---------------------------------------------------------------------------
# Loader builtins + tools_list shape + parity
# ---------------------------------------------------------------------------


def test_loader_discovers_builtin_email_integration():
    from kompany.plugins import loader

    ids = [i.integration_id for i in loader.discover()["integration"]]
    assert "email_smtp" in ids
    assert "resend" in ids


def test_tools_list_shape(engine):
    rows = engine.tools_list()
    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"test.echo", "test.send", "test.pay"}
    pay = by_name["test.pay"]
    assert pay["side_effect"] == "spend"
    assert pay["paid"] is True
    assert pay["connected"] is False
    assert pay["providers"][0]["integration_id"] == "test_integ"
    engine.credentials.set("custom_api_key", "k")
    assert {r["name"]: r["connected"] for r in engine.tools_list()}["test.send"] is True


def test_tools_list_parity_sdk_rest_mcp(engine, monkeypatch):
    """SDK == REST == MCP (CLI renders the same engine call)."""
    import asyncio
    import json as _json

    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod
    from kompany.interfaces import mcp_proxy, mcp_server

    # SDK path == engine op by construction; assert via engine directly.
    sdk_out = engine.tools_list()

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        rest_out = client.get("/tools").json()

    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(mcp_server, "_engine", engine)
    out = asyncio.run(mcp_server.call_tool("kompany_tools_list", {}))
    mcp_out = _json.loads(out[0].text)

    assert sdk_out == rest_out == mcp_out
    assert {r["name"] for r in rest_out} == {"test.echo", "test.send", "test.pay"}


def test_integrations_list_shape_and_connected_flip(engine):
    rows = engine.integrations_list()
    assert [r["integration_id"] for r in rows] == ["test_integ"]
    row = rows[0]
    assert row["display_name"] == "Test Integration"
    assert row["required_credentials"] == ["custom_api_key"]
    assert row["connected"] is False
    assert sorted(row["tools"]) == ["test.echo", "test.pay", "test.send"]
    assert isinstance(row["description"], str)
    engine.credentials.set("custom_api_key", "k")
    assert engine.integrations_list()[0]["connected"] is True
    engine.credentials.delete("custom_api_key")
    assert engine.integrations_list()[0]["connected"] is False


def test_integrations_parity_sdk_rest_mcp(engine, monkeypatch):
    """SDK == REST == MCP (CLI renders the same engine call)."""
    import asyncio
    import json as _json

    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod
    from kompany.interfaces import mcp_proxy, mcp_server

    sdk_out = engine.integrations_list()

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        rest_out = client.get("/integrations").json()

    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(mcp_server, "_engine", engine)
    out = asyncio.run(mcp_server.call_tool("kompany_integrations", {}))
    mcp_out = _json.loads(out[0].text)

    assert sdk_out == rest_out == mcp_out
    assert rest_out[0]["integration_id"] == "test_integ"


def test_rest_credentials_set_list_delete(engine, monkeypatch):
    """Settings page contract: POST /credentials connects, DELETE
    /credentials/{name} disconnects, GET /integrations reflects both."""
    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        r = client.post("/credentials", json={"name": "custom_api_key", "value": "k1"})
        assert r.status_code == 200
        # list() returns the full catalog; presence is signalled by configured=True.
        stored = {e["name"] for e in client.get("/credentials").json() if e["configured"]}
        assert "custom_api_key" in stored
        assert client.get("/integrations").json()[0]["connected"] is True

        r = client.delete("/credentials/custom_api_key")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        stored = {e["name"] for e in client.get("/credentials").json() if e["configured"]}
        assert "custom_api_key" not in stored
        assert client.get("/integrations").json()[0]["connected"] is False


def test_rest_propose_unknown_tool_is_400(engine, monkeypatch):
    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        r = client.post("/tools/propose", json={"tool_name": "nope.tool", "inputs": {}})
        assert r.status_code == 400
        ok = client.post(
            "/tools/propose",
            json={"tool_name": "test.echo", "inputs": {"text": "x"}, "summary": "s"},
        )
        assert ok.status_code == 200
        assert ok.json()["action_type"] == "tool_action"
