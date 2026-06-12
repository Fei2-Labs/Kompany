"""Tests for the kompany_permission_gate flow (PR5, PRD D5).

No real sleeping: the gate takes injectable ``sleep``/``clock`` — the
fake sleep advances a fake clock and optionally resolves the approval
mid-poll, exactly like a founder acting while the session blocks.
"""

from __future__ import annotations

import json
import sys

from kompany.core.harness_execution.permission_gate import (
    ACTION_HARNESS_PERMISSION,
    DEFAULT_TIMEOUT_SECONDS,
    GATE_ENV_AGENT,
    GATE_ENV_PROJECT_ID,
    GATE_ENV_TASK_ID,
    PERMISSION_PROMPT_TOOL,
    TIMEOUT_DENY_MESSAGE,
    build_permission_routing_args,
    enrich_gate_arguments,
    handle_permission_gate,
)
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.models import ApprovalStatus


class GateEngine:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path)
        self.approvals = ApprovalRequests(self.db)
        self.audit = AuditLog(self.db)


class FakeTimeline:
    """sleep() advances the clock; an optional action fires on the Nth poll."""

    def __init__(self, action=None, on_sleep_call=1):
        self.now = 0.0
        self.sleep_calls = 0
        self._action = action
        self._on_call = on_sleep_call

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleep_calls += 1
        self.now += seconds
        if self._action is not None and self.sleep_calls == self._on_call:
            self._action()


def _gate_args(**overrides):
    args = {
        "tool_name": "Bash",
        "input": {"command": "curl https://example.com"},
        "project_id": "proj-1",
        "task_id": "task-1",
        "agent_role": "builder",
    }
    args.update(overrides)
    return args


# ---------------------------------------------------------------------------
# CLI arg construction (executor side)
# ---------------------------------------------------------------------------


def test_build_routing_args_shape():
    args = build_permission_routing_args("proj-1", "task-1", "builder")
    assert args[args.index("--permission-prompt-tool") + 1] == (
        PERMISSION_PROMPT_TOOL
    )
    assert "--strict-mcp-config" in args  # no inherited MCP servers
    config = json.loads(args[args.index("--mcp-config") + 1])
    server = config["mcpServers"]["kompany"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "kompany.interfaces.mcp_server"]
    assert server["env"] == {
        GATE_ENV_PROJECT_ID: "proj-1",
        GATE_ENV_TASK_ID: "task-1",
        GATE_ENV_AGENT: "builder",
    }


def test_enrich_gate_arguments_reads_env(monkeypatch):
    monkeypatch.setenv(GATE_ENV_PROJECT_ID, "proj-env")
    monkeypatch.setenv(GATE_ENV_TASK_ID, "task-env")
    monkeypatch.setenv(GATE_ENV_AGENT, "writer")
    enriched = enrich_gate_arguments({"tool_name": "Bash"})
    assert enriched["project_id"] == "proj-env"
    assert enriched["task_id"] == "task-env"
    assert enriched["agent_role"] == "writer"
    # Explicit arguments win over env.
    explicit = enrich_gate_arguments(
        {"tool_name": "Bash", "project_id": "proj-explicit"}
    )
    assert explicit["project_id"] == "proj-explicit"


# ---------------------------------------------------------------------------
# Gate decisions
# ---------------------------------------------------------------------------


def test_gate_creates_approval_and_allows_on_midpoll_approve(tmp_path):
    engine = GateEngine(tmp_path)

    def founder_approves():
        pending = engine.approvals.list_pending()
        assert len(pending) == 1
        engine.approvals.approve(pending[0].id)

    timeline = FakeTimeline(action=founder_approves, on_sleep_call=2)
    result = handle_permission_gate(
        engine, _gate_args(), sleep=timeline.sleep, clock=timeline.clock
    )

    assert result == {
        "behavior": "allow",
        "updatedInput": {"command": "curl https://example.com"},
    }
    assert timeline.sleep_calls == 2  # blocked until the founder acted
    # The approval carries the audit trail the inbox renders.
    rows = engine.approvals.list_by_status(status="approved")
    assert len(rows) == 1
    row = rows[0]
    assert row.action_type == ACTION_HARNESS_PERMISSION
    assert row.severity == "high"
    assert row.project_id == "proj-1"
    assert row.payload["tool_name"] == "Bash"
    assert row.payload["task_id"] == "task-1"
    assert "curl" in row.payload["input_digest"]
    events = [e["event_type"] for e in engine.audit.recent(limit=5)]
    assert "harness.permission_requested" in events


def test_gate_denies_on_reject_with_reason(tmp_path):
    engine = GateEngine(tmp_path)

    def founder_rejects():
        pending = engine.approvals.list_pending()
        engine.approvals.reject(pending[0].id, reason="no paid calls")

    timeline = FakeTimeline(action=founder_rejects, on_sleep_call=1)
    result = handle_permission_gate(
        engine, _gate_args(), sleep=timeline.sleep, clock=timeline.clock
    )

    assert result["behavior"] == "deny"
    assert "no paid calls" in result["message"]


def test_gate_times_out_to_deny_and_request_stays_pending(tmp_path):
    engine = GateEngine(tmp_path)
    timeline = FakeTimeline()  # nobody acts
    result = handle_permission_gate(
        engine, _gate_args(), sleep=timeline.sleep, clock=timeline.clock
    )

    assert result == {"behavior": "deny", "message": TIMEOUT_DENY_MESSAGE}
    # Default 120s / 2s interval → the loop polled until the deadline.
    assert timeline.sleep_calls == int(DEFAULT_TIMEOUT_SECONDS / 2.0)
    # The request stays in the inbox: a re-run re-asks and an approval
    # given later resolves the next gate call instantly.
    pending = engine.approvals.list_pending()
    assert len(pending) == 1
    assert pending[0].status == ApprovalStatus.PENDING


def test_gate_custom_timeout_and_interval(tmp_path):
    engine = GateEngine(tmp_path)
    timeline = FakeTimeline()
    result = handle_permission_gate(
        engine,
        _gate_args(timeout_seconds=10, poll_interval_seconds=5),
        sleep=timeline.sleep,
        clock=timeline.clock,
    )
    assert result["behavior"] == "deny"
    assert timeline.sleep_calls == 2  # 10s / 5s


def test_gate_tolerates_non_dict_input(tmp_path):
    """claude could send a string/list input; the gate must not crash."""
    engine = GateEngine(tmp_path)

    def founder_approves():
        engine.approvals.approve(engine.approvals.list_pending()[0].id)

    timeline = FakeTimeline(action=founder_approves, on_sleep_call=1)
    result = handle_permission_gate(
        engine,
        _gate_args(input="rm -rf /tmp/x"),
        sleep=timeline.sleep,
        clock=timeline.clock,
    )
    assert result["behavior"] == "allow"
    assert result["updatedInput"] == {"value": "rm -rf /tmp/x"}


# ---------------------------------------------------------------------------
# Re-ask after timeout — adopt the pending request, redeem a late approval
# ---------------------------------------------------------------------------


def _gate_until_timeout(engine, **overrides):
    timeline = FakeTimeline()  # nobody acts → the gate times out
    return handle_permission_gate(
        engine, _gate_args(**overrides),
        sleep=timeline.sleep, clock=timeline.clock,
    )


def test_reask_adopts_pending_request_instead_of_duplicating(tmp_path):
    """Timeout → deny → re-run re-asks: the gate polls the SAME pending
    request, never spams the inbox with duplicates."""
    engine = GateEngine(tmp_path)
    first = _gate_until_timeout(engine)
    assert first["behavior"] == "deny"  # nobody acted → timeout

    timeline = FakeTimeline()
    second = handle_permission_gate(
        engine, _gate_args(), sleep=timeline.sleep, clock=timeline.clock
    )
    assert second["behavior"] == "deny"
    assert len(engine.approvals.list_pending()) == 1  # no duplicate
    # Only ONE audit entry — the adopted request was not re-announced.
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert events.count("harness.permission_requested") == 1


def test_reask_redeems_post_timeout_approval_instantly(tmp_path):
    """Founder approves AFTER the timeout deny → the next ask for the
    same action allows instantly (no poll), one-shot."""
    engine = GateEngine(tmp_path)
    _gate_until_timeout(engine)  # times out, request stays pending
    pending = engine.approvals.list_pending()
    engine.approvals.approve(pending[0].id)  # founder acts later

    timeline = FakeTimeline()
    result = handle_permission_gate(
        engine, _gate_args(), sleep=timeline.sleep, clock=timeline.clock
    )
    assert result["behavior"] == "allow"
    assert timeline.sleep_calls == 0  # instant, no blocking
    # One-shot: the redeemed stamp prevents a third identical ask from
    # silently reusing this approval — it files a fresh request.
    row = engine.approvals.get(pending[0].id)
    assert row.payload["redeemed"] is True
    third = _gate_until_timeout(engine)
    assert third["behavior"] == "deny"  # new pending request, timed out
    assert len(engine.approvals.list_pending()) == 1


def test_inloop_approval_is_stamped_redeemed(tmp_path):
    """An approval consumed during the poll is one-shot too."""
    engine = GateEngine(tmp_path)

    def founder_approves():
        engine.approvals.approve(engine.approvals.list_pending()[0].id)

    timeline = FakeTimeline(action=founder_approves, on_sleep_call=1)
    result = handle_permission_gate(
        engine, _gate_args(), sleep=timeline.sleep, clock=timeline.clock
    )
    assert result["behavior"] == "allow"
    rows = engine.approvals.list_by_status(status="approved")
    assert rows[0].payload["redeemed"] is True


def test_reask_after_rejection_files_fresh_request(tmp_path):
    """A rejected request is never adopted — the founder can change
    their mind on a re-run instead of getting a replayed denial."""
    engine = GateEngine(tmp_path)

    def founder_rejects():
        engine.approvals.reject(
            engine.approvals.list_pending()[0].id, reason="not now"
        )

    timeline = FakeTimeline(action=founder_rejects, on_sleep_call=1)
    first = handle_permission_gate(
        engine, _gate_args(), sleep=timeline.sleep, clock=timeline.clock
    )
    assert first["behavior"] == "deny"

    second = _gate_until_timeout(engine)
    assert second["behavior"] == "deny"
    assert second["message"] == TIMEOUT_DENY_MESSAGE  # fresh ask, not replay
    assert len(engine.approvals.list_pending()) == 1  # new request filed


# ---------------------------------------------------------------------------
# Dispatch parity — both transports reach the gate through dispatch_tool
# ---------------------------------------------------------------------------


def test_gate_is_dispatchable_via_shared_dispatch(tmp_path, monkeypatch):
    from kompany.interfaces.mcp_dispatch import dispatcher

    engine = GateEngine(tmp_path)

    captured = {}

    def fake_handle(eng, arguments):
        captured["engine"] = eng
        captured["arguments"] = arguments
        return {"behavior": "deny", "message": "stub"}

    monkeypatch.setattr(
        "kompany.interfaces.mcp_dispatch.governance.handle_permission_gate",
        fake_handle,
    )
    result = dispatcher.dispatch_tool(
        engine, "kompany_permission_gate", {"tool_name": "Bash"}
    )
    assert result == {"behavior": "deny", "message": "stub"}
    assert captured["engine"] is engine
    assert captured["arguments"] == {"tool_name": "Bash"}


def test_gate_tool_registered_in_mcp_tools():
    mcp = __import__("importlib").import_module("kompany.interfaces.mcp_server")
    names = [tool.name for tool in mcp.TOOLS]
    assert "kompany_permission_gate" in names
