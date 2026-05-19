"""End-to-end traceability: run a directive, walk the run_id back out."""

from __future__ import annotations

import re

import pytest

from kompany.core.engine import KompanyEngine
from kompany.core.run_context import (
    RUN_ID_PATTERN,
    current_run_id,
    new_run_id,
    parent_run_id,
    run_scope,
)
from kompany.state.audit import AuditLog
from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database
from kompany.state.journal import Journal
from kompany.state.ledger import Ledger
from kompany.state.memory import AgentMemory
from kompany.state.models import (
    ApprovalRequest,
    Decision,
    LedgerCategory,
    Project,
    ProjectType,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects


_RUN_ID_RE = re.compile(RUN_ID_PATTERN)


def _make_engine(tmp_path):
    """Bypass __init__ so tests don't need an LLM key. Mirrors test_engine.py."""

    class TestSettings:
        company_name = "TestCo"
        company_goal = "AI tools"
        company_stage = "solo"
        company_time_horizon = ""
        company_exclusions = ""
        data_dir = tmp_path
        anthropic_api_key = "test-key"
        openai_api_key = ""
        telegram_bot_token = ""
        telegram_chat_id = ""
        telegram_allowed_chat_ids = ""
        mobile_remote_token = "mobile-secret"
        web_dashboard_token = ""
        vault_key = ""
        gemini_api_key = ""
        glm_api_key = ""
        kimi_api_key = ""
        custom_api_key = ""
        custom_base_url = ""
        currency = "EUR"
        model_apex = "claude-opus-4-20250514"
        model_primary = "claude-sonnet-4-20250514"
        model_economy = "claude-haiku-4-20250414"
        remote_replay_ttl_seconds = 7 * 24 * 60 * 60

        def get_model_for_tier(self, tier):
            return self.model_primary

        def get_api_key_for_provider(self, provider):
            return self.anthropic_api_key if provider == "anthropic" else ""

    from kompany.agents.registry import AgentRegistry
    from kompany.core.autonomy import AutonomyGate
    from kompany.llm.cost_tracker import CostTracker
    from kompany.state.agent_status import AgentStatusStore
    from kompany.state.backup import BackupManager
    from kompany.state.checkpoints import CheckpointStore
    from kompany.state.credentials import CredentialVaultStore
    from kompany.state.remote_replay import RemoteReplayStore
    from kompany.state.runtime import RuntimeStateStore
    from kompany.state.tool_authorization import ToolAuthorizationStore

    settings = TestSettings()
    db = Database(tmp_path)
    ledger = Ledger(db)
    journal = Journal(db)
    projects = Projects(db)
    memory = AgentMemory(db)
    audit = AuditLog(db)
    approvals = ApprovalRequests(db)
    agent_status = AgentStatusStore(db)
    checkpoints = CheckpointStore(db)
    cost_tracker = CostTracker(ledger)

    engine = KompanyEngine.__new__(KompanyEngine)
    engine.settings = settings
    engine.db = db
    engine.ledger = ledger
    engine.journal = journal
    engine.projects = projects
    engine.memory = memory
    engine.audit = audit
    engine.approvals = approvals
    engine.agent_status = agent_status
    engine.checkpoints = checkpoints
    engine.cost_tracker = cost_tracker
    engine.backups = BackupManager(tmp_path)
    engine.runtime = RuntimeStateStore(db)
    engine.remote_replay = RemoteReplayStore(db)
    engine.credentials = CredentialVaultStore(db, settings.vault_key)
    engine.tool_authorization = ToolAuthorizationStore(db)
    engine.autonomy = AutonomyGate()
    engine.llm = None
    engine.registry = AgentRegistry(None, settings, ledger)
    return engine


def test_run_id_propagates_to_all_state_writes(tmp_path):
    """All concurrent writes under one run_scope share the same run_id."""
    engine = _make_engine(tmp_path)
    engine.initialize_company(name="TestCo", goal="ship", capital=100.0)

    with run_scope() as rid:
        assert _RUN_ID_RE.match(rid)

        # Audit log
        engine.audit.record("test.event", "doing the thing", agent_role="ceo")

        # Memory
        engine.memory.remember(agent_role="cfo", content="balance is healthy")

        # Decision journal
        engine.journal.log(Decision(
            directive_id="dir-x",
            directive_type="strategic",
            raw_input="hello",
            classification={},
            result={"status": "ok"},
            agents_involved=["ceo"],
            total_ai_cost=0.0,
            duration_seconds=0.1,
        ))

        # Ledger entry (separate from initial capital, which was outside scope)
        engine.ledger.record(
            amount=-1.0,
            description="test expense",
            category=LedgerCategory.AI_COST,
        )

        # Project + task
        project = Project(
            name="trace-test",
            type=ProjectType.OPERATIONAL,
            assigned_agents=["coo"],
        )
        engine.projects.create(project)
        engine.projects.create_task(Task(
            project_id=project.id,
            title="do the thing",
            assigned_agent="coo",
            status=TaskStatus.PENDING,
        ))

        # Approval request
        engine.approvals.create(ApprovalRequest(
            action_type="test",
            summary="please approve",
            requested_by="test",
        ))

    # Outside the scope, look every row back out by run_id.
    audit_rows = engine.db.execute(
        "SELECT run_id FROM audit_log WHERE event_type = 'test.event'"
    ).fetchall()
    assert audit_rows and all(r["run_id"] == rid for r in audit_rows)

    memory_rows = engine.db.execute(
        "SELECT run_id FROM agent_memories WHERE agent_role = 'cfo'"
    ).fetchall()
    assert memory_rows and all(r["run_id"] == rid for r in memory_rows)

    decision_rows = engine.db.execute(
        "SELECT run_id FROM decisions WHERE directive_id = 'dir-x'"
    ).fetchall()
    assert decision_rows and all(r["run_id"] == rid for r in decision_rows)

    ledger_rows = engine.db.execute(
        "SELECT run_id FROM ledger WHERE description = 'test expense'"
    ).fetchall()
    assert ledger_rows and all(r["run_id"] == rid for r in ledger_rows)

    task_rows = engine.db.execute(
        "SELECT run_id FROM tasks"
    ).fetchall()
    assert task_rows and all(r["run_id"] == rid for r in task_rows)

    approval_rows = engine.db.execute(
        "SELECT run_id FROM approval_requests"
    ).fetchall()
    assert approval_rows and all(r["run_id"] == rid for r in approval_rows)


def test_run_id_is_null_outside_scope(tmp_path):
    """State writes outside a run_scope leave run_id NULL (history-friendly)."""
    engine = _make_engine(tmp_path)
    # initialize_company writes a ledger row without an active scope.
    engine.initialize_company(name="TestCo", goal="ship", capital=42.0)

    rows = engine.db.execute(
        "SELECT run_id, amount FROM ledger"
    ).fetchall()
    assert rows
    assert all(r["run_id"] is None for r in rows)


def test_parent_child_run_ids_recorded(tmp_path):
    """A child run_scope sees its parent via parent_run_id()."""
    engine = _make_engine(tmp_path)
    seen: dict[str, str | None] = {}

    with run_scope() as parent:
        seen["parent_outer"] = parent_run_id()
        engine.audit.record("outer.event", "outer")
        with run_scope() as child:
            seen["child"] = child
            seen["parent_inner"] = parent_run_id()
            engine.audit.record("inner.event", "inner")

    assert seen["parent_outer"] is None
    assert seen["parent_inner"] == parent
    assert seen["child"] != parent

    outer = engine.db.execute(
        "SELECT run_id FROM audit_log WHERE event_type = 'outer.event'"
    ).fetchone()
    inner = engine.db.execute(
        "SELECT run_id FROM audit_log WHERE event_type = 'inner.event'"
    ).fetchone()
    assert outer["run_id"] == parent
    assert inner["run_id"] == seen["child"]


def test_trace_run_reconstructs_directive_chain(tmp_path):
    """Run a full directive and verify trace_run pulls a coherent chain."""
    engine = _make_engine(tmp_path)
    engine.initialize_company(name="TestCo", goal="ship", capital=100.0)

    # Use an informational directive — no LLM needed.
    from kompany.agents.ceo import DirectiveClassification
    from kompany.core.directive import DirectiveResult

    class FakeCEO:
        def classify(
            self,
            raw_input,
            directive_id=None,
            targets_summary=None,
            glossary_summary=None,
        ):
            return DirectiveClassification(
                directive_type="informational",
                reasoning="status query",
                primary_squad="strategy",
                approval_tier="auto",
            )

    original_registry = engine.registry

    class FakeRegistry:
        def get(self, role, company_state=None):
            if role == "ceo":
                return FakeCEO()
            return original_registry.get(role, company_state)

    engine.registry = FakeRegistry()

    result = engine.process_directive("What is our balance?")
    assert isinstance(result, DirectiveResult)
    assert result.status == "completed"

    # Read the run_id back from the audit log entry written during the call.
    audit_rows = engine.db.execute(
        "SELECT run_id FROM audit_log WHERE event_type = 'directive.received'"
    ).fetchall()
    assert audit_rows, "expected directive.received audit row"
    run_ids = {r["run_id"] for r in audit_rows}
    assert len(run_ids) == 1
    rid = run_ids.pop()
    assert _RUN_ID_RE.match(rid)

    # trace_run should pick up every write tagged with that run_id.
    trace = engine.trace_run(rid)
    assert trace["run_id"] == rid
    assert trace["event_count"] > 0

    kinds = {event["kind"] for event in trace["events"]}
    # An informational directive writes audit events + a decision journal
    # entry; ledger may stay empty since no LLM call happened.
    assert "audit" in kinds
    assert "decision" in kinds

    event_types = {
        event["event_type"]
        for event in trace["events"]
        if event["kind"] == "audit"
    }
    assert "directive.received" in event_types
    assert "directive.classified" in event_types
    assert "directive.completed" in event_types


def test_trace_run_empty_for_unknown_id(tmp_path):
    engine = _make_engine(tmp_path)
    trace = engine.trace_run(new_run_id())
    assert trace["event_count"] == 0
    assert trace["events"] == []


def test_distinct_directives_get_distinct_run_ids(tmp_path):
    """Two sequential directives must not share a run_id."""
    engine = _make_engine(tmp_path)
    engine.initialize_company(name="TestCo", goal="ship", capital=100.0)

    from kompany.agents.ceo import DirectiveClassification

    class FakeCEO:
        def classify(
            self,
            raw_input,
            directive_id=None,
            targets_summary=None,
            glossary_summary=None,
        ):
            return DirectiveClassification(
                directive_type="informational",
                reasoning="status",
                primary_squad="strategy",
                approval_tier="auto",
            )

    original_registry = engine.registry

    class FakeRegistry:
        def get(self, role, company_state=None):
            if role == "ceo":
                return FakeCEO()
            return original_registry.get(role, company_state)

    engine.registry = FakeRegistry()

    engine.process_directive("first")
    engine.process_directive("second")

    rows = engine.db.execute(
        "SELECT DISTINCT run_id FROM audit_log "
        "WHERE event_type = 'directive.received' AND run_id IS NOT NULL"
    ).fetchall()
    rids = [r["run_id"] for r in rows]
    assert len(rids) == 2
    assert rids[0] != rids[1]
    assert all(_RUN_ID_RE.match(r) for r in rids)
