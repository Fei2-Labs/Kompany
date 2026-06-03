"""Tests for KompanyEngine — integration tests for the directive flow."""

from __future__ import annotations

import pytest

from pathlib import Path

from kompany.core.engine import KompanyEngine
from kompany.state.models import LedgerCategory


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Create an engine with a temp data dir and no API key needed for mechanical tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))

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
        telegram_allowed_chat_ids = "123,456"
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

        def get_model_for_tier(self, tier):
            return {
                "apex": self.model_apex,
                "primary": self.model_primary,
                "economy": self.model_economy,
            }.get(tier, self.model_primary)

        def get_api_key_for_provider(self, provider):
            return {
                "anthropic": self.anthropic_api_key,
                "openai": self.openai_api_key,
                "gemini": self.gemini_api_key,
                "glm": self.glm_api_key,
                "kimi": self.kimi_api_key,
                "custom": self.custom_api_key,
            }.get(provider, "")

    from kompany.state.agent_status import AgentStatusStore
    from kompany.state.approvals import ApprovalRequests
    from kompany.state.audit import AuditLog
    from kompany.state.checkpoints import CheckpointStore
    from kompany.state.database import Database
    from kompany.state.ledger import Ledger
    from kompany.state.journal import Journal
    from kompany.state.projects import Projects
    from kompany.state.memory import AgentMemory
    from kompany.llm.cost_tracker import CostTracker
    from kompany.agents.registry import AgentRegistry

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
    from kompany.state.conversation import ConversationStore
    engine.channel = ConversationStore(db)
    engine.agent_status = agent_status
    engine.checkpoints = checkpoints
    engine.cost_tracker = cost_tracker
    from kompany.state.backup import BackupManager
    from kompany.state.runtime import RuntimeStateStore
    from kompany.state.credentials import CredentialVaultStore
    from kompany.state.remote_replay import RemoteReplayStore
    from kompany.state.tool_authorization import ToolAuthorizationStore
    engine.backups = BackupManager(tmp_path)
    engine.runtime = RuntimeStateStore(db)
    engine.remote_replay = RemoteReplayStore(db)
    engine.credentials = CredentialVaultStore(db, settings.vault_key)
    engine.settings.remote_replay_ttl_seconds = 7 * 24 * 60 * 60
    engine.tool_authorization = ToolAuthorizationStore(db)
    engine.autonomy = __import__(
        "kompany.core.autonomy", fromlist=["AutonomyGate"]
    ).AutonomyGate()
    engine.llm = None
    engine.registry = AgentRegistry(None, settings, ledger)

    return engine


def test_initialize_company(engine):
    engine.initialize_company(
        name="TestCo", goal="AI tools", capital=50.0    )
    assert engine.ledger.get_balance() == 50.0


def test_initialize_company_zero_balance(engine):
    engine.initialize_company(
        name="TestCo", goal="AI tools", capital=0.0    )
    assert engine.ledger.get_balance() == 0.0


def test_get_company_state(engine):
    engine.initialize_company(
        name="TestCo", goal="AI tools", capital=100.0
    )
    state = engine.get_company_state()
    assert state["name"] == "TestCo"
    assert state["balance"] == 100.0
    assert state["active_projects"] == 0


def test_informational_directive(engine):
    """Informational directives should work without LLM calls."""
    engine.initialize_company(
        name="TestCo", goal="AI tools", capital=50.0
    )
    result = engine._handle_informational(
        directive=__import__(
            "kompany.core.directive", fromlist=["Directive"]
        ).Directive(raw_input="What's our balance?"),
        classification=None,
        ceo=None,
    )
    assert result.status == "completed"
    assert "50.00" in result.message
    assert result.total_ai_cost == 0


def test_process_directive_writes_audit_events_and_status(engine):
    """Directive processing should leave an orchestration trace."""
    from kompany.agents.ceo import DirectiveClassification
    from kompany.core.directive import DirectiveResult

    class FakeCEO:
        def classify(
            self,
            raw_input,
            directive_id=None,
            targets_summary=None,
            glossary_summary=None,
            **kwargs,
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

    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    engine.registry = FakeRegistry()

    result = engine.process_directive("What's our balance?")

    assert isinstance(result, DirectiveResult)
    assert result.status == "completed"
    event_types = [event["event_type"] for event in engine.audit.recent(limit=10)]
    assert "directive.received" in event_types
    assert "directive.classified" in event_types
    assert "directive.routed" in event_types
    assert "journal.recorded" in event_types
    assert "directive.completed" in event_types
    assert engine.agent_status.get("ceo")["status"] == "idle"


def test_process_directive_populates_run_id(engine):
    """process_directive stamps the run_scope's run_id onto the result so
    callers can scope per-run SSE events / cost reconcile."""
    from kompany.agents.ceo import DirectiveClassification
    from kompany.core.run_context import is_valid_run_id

    class FakeCEO:
        def classify(self, raw_input, directive_id=None, targets_summary=None, glossary_summary=None, **kwargs):
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

    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    engine.registry = FakeRegistry()

    result = engine.process_directive("What's our balance?")

    assert result.run_id is not None
    assert is_valid_run_id(result.run_id)


def test_process_directive_suspended_result_carries_run_id(engine):
    """Even the early suspended-return path is tagged with the run_id
    (stamped inside run_scope in process_directive)."""
    from kompany.core.run_context import is_valid_run_id

    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    engine.runtime.set("suspended", reason="test")

    result = engine.process_directive("anything")

    assert result.status == "suspended"
    assert result.run_id is not None
    assert is_valid_run_id(result.run_id)


def test_constitution_exists_with_core_rules():
    constitution = Path(__file__).parents[2] / "CONSTITUTION.md"
    text = constitution.read_text()

    assert "supreme decision maker" in text
    assert "Mission integrity" in text
    assert "Every LLM call" in text
    assert "Constitution change control" in text


def test_prepare_decision_packet_creates_full_chain_and_approval(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)

    packet = engine.prepare_decision_packet("Buy a Mac Studio", target_amount=5000.0)

    assert packet["status"] == "awaiting_approval"
    assert packet["approval_id"]
    assert packet["revenue_proposal"]["owner"] == "cro"
    assert packet["financial_evaluation"]["owner"] == "cfo"
    assert packet["synthesis"]["owner"] == "cos"
    assert packet["ceo_approval"]["owner"] == "ceo"
    assert packet["execution_plan"]["owner"] == "coo"
    assert engine.list_approvals()[0]["action_type"] == "decision_chain_execution"
    event_types = [event["event_type"] for event in engine.audit.recent(limit=10)]
    assert "decision_chain.cro_proposed" in event_types
    assert "decision_chain.cfo_evaluated" in event_types
    assert "decision_chain.cos_synthesized" in event_types
    assert "decision_chain.ceo_approved_direction" in event_types
    assert "decision_chain.coo_planned" in event_types
    assert "decision_chain.autonomy_requested" in event_types


def test_process_override_creates_risk_briefing_and_approval(engine):
    result = engine.process_override("Stop the current project immediately")

    assert result["status"] == "awaiting_approval"
    assert result["approval_id"]
    assert result["briefing"]["will_execute_immediately"] is False
    assert engine.list_approvals()[0]["action_type"] == "override"
    event_types = [event["event_type"] for event in engine.audit.recent(limit=5)]
    assert "override.risk_briefing_created" in event_types


def _wire_packet_execution(engine):
    """Attach memory and a fake registry so ProjectRunner can run without LLMs."""
    from types import SimpleNamespace
    from kompany.state.memory import AgentMemory

    engine.memory = AgentMemory(engine.db)

    class _FakeAgent:
        def __init__(self, fail=False):
            self.fail = fail

        def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
            if self.fail:
                raise RuntimeError("forced task failure")
            return SimpleNamespace(text="output", cost_usd=0.0)

    class _FakeRegistry:
        def __init__(self, fail_role=None):
            self.fail_role = fail_role

        def get(self, role, company_state=None):
            return _FakeAgent(fail=(role == self.fail_role))

    engine._FakeRegistry = _FakeRegistry  # for tests to swap
    engine.registry = _FakeRegistry()


def _approve_packet(engine, raw_input="Buy a Mac Studio", target_amount=5000.0):
    packet = engine.prepare_decision_packet(raw_input, target_amount=target_amount)
    approval_id = packet["approval_id"]
    engine.approve_request(approval_id)
    return approval_id, packet


def test_execute_decision_packet_rejects_unapproved(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    packet = engine.prepare_decision_packet("Buy laptop", target_amount=2000.0)

    import pytest as _pytest
    with _pytest.raises(ValueError, match="not approved"):
        engine.execute_decision_packet(packet["approval_id"])


def test_execute_decision_packet_rejects_wrong_action_type(engine):
    from kompany.state.models import ApprovalRequest
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    request = engine.approvals.create(ApprovalRequest(
        action_type="override",
        summary="not a packet",
        payload={},
    ))
    engine.approvals.approve(request.id)

    import pytest as _pytest
    with _pytest.raises(ValueError, match="decision_chain_execution"):
        engine.execute_decision_packet(request.id)


def test_execute_decision_packet_runs_full_pipeline(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    _wire_packet_execution(engine)
    approval_id, _packet = _approve_packet(engine)

    report = engine.execute_decision_packet(approval_id)

    assert report["status"] == "awaiting_delivery_approval"
    assert report["project_id"]
    assert report["delivery_approval_id"]
    assert report["tasks_failed"] == 0
    assert {r["owner"] for r in report["reviews"]} == {"cro", "cfo", "cos", "ceo"}
    assert all(r["verdict"] == "approved" for r in report["reviews"])

    # Project + tasks materialized
    project = engine.projects.get(report["project_id"])
    assert project is not None
    assert "coo" in project.assigned_agents
    tasks = engine.projects.list_tasks(project.id)
    assert len(tasks) >= 1

    # Delivery approval pending
    pending = engine.list_approvals()
    delivery = [r for r in pending if r["id"] == report["delivery_approval_id"]]
    assert delivery and delivery[0]["action_type"] == "delivery_approval"

    event_types = [e["event_type"] for e in engine.audit.recent(limit=40)]
    assert "governed_execution.materialized" in event_types
    assert "governed_execution.dispatched" in event_types
    assert "governed_execution.reviewed" in event_types
    assert "governed_execution.delivery_requested" in event_types


def test_execute_decision_packet_needs_revision_when_tasks_fail(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    _wire_packet_execution(engine)
    # Force every task to fail by failing the most-used assigned agent.
    engine.registry = engine._FakeRegistry(fail_role="researcher")
    # All tasks are assigned to researcher (first in execution_plan.assigned_agents
    # round-robin), so all will fail.
    approval_id, _packet = _approve_packet(engine)

    # Override packet's assigned_agents to force a single failing role.
    # Easier: monkey-patch every role to fail.
    class AllFail:
        def get(self, role, company_state=None):
            from types import SimpleNamespace
            class _A:
                def call(self_inner, prompt, directive_id=None, max_tokens=4096, action_type=None):
                    raise RuntimeError("forced")
            return _A()
    engine.registry = AllFail()

    report = engine.execute_decision_packet(approval_id)

    assert report["status"] == "needs_revision"
    assert report["tasks_failed"] >= 1
    assert all(r["verdict"] == "needs_revision" for r in report["reviews"])


def _execute_packet_and_approve_delivery(engine):
    """Helper: run the full pipeline and approve the resulting delivery_approval."""
    _wire_packet_execution(engine)
    approval_id, _packet = _approve_packet(engine)
    report = engine.execute_decision_packet(approval_id)
    delivery_id = report["delivery_approval_id"]
    engine.approve_request(delivery_id)
    return delivery_id, report


def test_release_delivery_rejects_unknown_approval(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)

    import pytest as _pytest
    with _pytest.raises(ValueError, match="not a delivery_approval"):
        engine.release_delivery("no-such-id")
    event_types = [e["event_type"] for e in engine.audit.recent(limit=5)]
    assert "governed_execution.release_blocked" in event_types


def test_release_delivery_rejects_wrong_action_type(engine):
    from kompany.state.models import ApprovalRequest
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    request = engine.approvals.create(ApprovalRequest(
        action_type="override",
        summary="not a delivery",
        payload={},
    ))

    import pytest as _pytest
    with _pytest.raises(ValueError, match="not a delivery_approval"):
        engine.release_delivery(request.id)


def test_release_delivery_rejects_unapproved(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    _wire_packet_execution(engine)
    approval_id, _ = _approve_packet(engine)
    report = engine.execute_decision_packet(approval_id)

    # delivery_approval is still pending
    import pytest as _pytest
    with _pytest.raises(ValueError, match="not approved"):
        engine.release_delivery(report["delivery_approval_id"])


def test_release_delivery_full_path_and_idempotent(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    delivery_id, report = _execute_packet_and_approve_delivery(engine)

    package = engine.release_delivery(delivery_id)

    assert package["status"] == "delivered"
    assert package["project_id"] == report["project_id"]
    assert package["released_at"]
    assert package["released_by"] == "master"
    # Project marked completed
    project = engine.projects.get(report["project_id"])
    assert project.status.value == "completed"
    # Audit
    event_types = [e["event_type"] for e in engine.audit.recent(limit=40)]
    assert "governed_execution.released" in event_types

    # Idempotent: second call returns already_delivered, no new released event
    released_count_before = sum(
        1 for e in engine.audit.recent(limit=80)
        if e["event_type"] == "governed_execution.released"
    )
    package2 = engine.release_delivery(delivery_id)
    released_count_after = sum(
        1 for e in engine.audit.recent(limit=80)
        if e["event_type"] == "governed_execution.released"
    )

    assert package2["status"] == "already_delivered"
    assert package2["released_at"] == package["released_at"]
    assert released_count_after == released_count_before


def test_release_delivery_returns_needs_revision_when_rejected(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    _wire_packet_execution(engine)
    approval_id, _ = _approve_packet(engine)
    report = engine.execute_decision_packet(approval_id)
    delivery_id = report["delivery_approval_id"]
    engine.reject_request(delivery_id, reason="outputs incomplete")

    package = engine.release_delivery(delivery_id)

    assert package["status"] == "needs_revision"
    assert "incomplete" in package["notes"]
    # Project should NOT be completed
    project = engine.projects.get(report["project_id"])
    assert project.status.value != "completed"
    event_types = [e["event_type"] for e in engine.audit.recent(limit=40)]
    assert "governed_execution.release_blocked" in event_types


def test_run_retrospective_skips_unknown_project(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    _wire_packet_execution(engine)

    result = engine.run_retrospective("no-such-project")

    assert result["status"] == "skipped_no_project"
    assert result["reflections"] == []
    event_types = [e["event_type"] for e in engine.audit.recent(limit=5)]
    assert "learning.retrospective_skipped" in event_types


def test_run_retrospective_records_one_reflection_per_agent(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    delivery_id, report = _execute_packet_and_approve_delivery(engine)
    project_id = report["project_id"]

    # Auto-fired during release_delivery; calling explicitly should be idempotent.
    engine.release_delivery(delivery_id)
    result = engine.run_retrospective(project_id)

    assert result["status"] == "already_recorded"
    assert result["reflections"], "expected reflections to be present"

    # One reflection per assigned agent
    project = engine.projects.get(project_id)
    assert {r["agent_role"] for r in result["reflections"]} == set(project.assigned_agents)

    # Memory entries persisted with correct category/context
    for role in project.assigned_agents:
        memos = engine.list_memories(role, category="reflection")
        assert any(m["context"] == f"project:{project_id}" for m in memos)


def test_run_retrospective_is_idempotent(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    delivery_id, report = _execute_packet_and_approve_delivery(engine)
    project_id = report["project_id"]

    # First release auto-fires the retrospective.
    engine.release_delivery(delivery_id)
    completed_before = sum(
        1 for e in engine.audit.recent(limit=80)
        if e["event_type"] == "learning.retrospective_completed"
    )

    second = engine.run_retrospective(project_id)

    completed_after = sum(
        1 for e in engine.audit.recent(limit=80)
        if e["event_type"] == "learning.retrospective_completed"
    )

    assert second["status"] == "already_recorded"
    assert completed_after == completed_before  # no new completed event
    # No duplicate reflection memories
    project = engine.projects.get(project_id)
    for role in project.assigned_agents:
        memos = engine.list_memories(role, category="reflection")
        project_memos = [m for m in memos if m["context"] == f"project:{project_id}"]
        assert len(project_memos) == 1


def test_release_delivery_auto_fires_retrospective(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    delivery_id, report = _execute_packet_and_approve_delivery(engine)

    engine.release_delivery(delivery_id)

    event_types = [e["event_type"] for e in engine.audit.recent(limit=80)]
    assert "learning.retrospective_completed" in event_types
    project = engine.projects.get(report["project_id"])
    for role in project.assigned_agents:
        memos = engine.list_memories(role, category="reflection")
        assert any(m["context"] == f"project:{report['project_id']}" for m in memos)


def test_engine_runtime_default_running(engine):
    assert engine.get_runtime_state()["state"] == "running"


def test_heartbeat_once_audits_empty_report(engine):
    report = engine.heartbeat_once()

    assert report["status"] == "ok"
    assert report["runtime"]["state"] == "running"
    assert report["pending_approvals"] == 0
    assert report["active_projects"] == 0
    assert report["notifications"] == []
    event_types = [e["event_type"] for e in engine.audit.recent(limit=5)]
    assert "heartbeat.tick" in event_types


def test_heartbeat_once_emits_suspended_and_approval_notifications(engine):
    from kompany.state.models import ApprovalRequest

    engine.suspend(reason="quota_exhausted")
    request = engine.approvals.create(ApprovalRequest(
        action_type="directive_execution",
        summary="Approve directive",
    ))

    report = engine.heartbeat_once()

    assert report["runtime"]["state"] == "suspended"
    assert report["pending_approvals"] == 1
    kinds = [n["kind"] for n in report["notifications"]]
    assert "runtime_suspended" in kinds
    assert "pending_approvals" in kinds
    approval_event = next(n for n in report["notifications"] if n["kind"] == "pending_approvals")
    assert approval_event["payload"]["approval_ids"] == [request.id]
    event_types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "heartbeat.tick" in event_types
    assert "notification.emitted" in event_types


def test_dispatch_notifications_dry_run_audits_delivery(engine):
    event = {
        "kind": "pending_approvals",
        "severity": "action_required",
        "summary": "1 approval request waiting.",
        "payload": {"approval_ids": ["app-1"]},
    }

    deliveries = engine.dispatch_notifications([event])

    assert deliveries[0]["status"] == "dry_run"
    assert deliveries[0]["adapter"] == "dry-run"
    audit = engine.audit.recent(limit=5)[0]
    assert audit["event_type"] == "notification.dispatched"
    assert "secret-token" not in (audit["detail"] or "")


def test_heartbeat_once_can_dispatch_notifications(engine):
    from kompany.state.models import ApprovalRequest

    engine.approvals.create(ApprovalRequest(
        action_type="directive_execution",
        summary="Approve directive",
    ))

    report = engine.heartbeat_once(dispatch=True, adapter="dry-run")

    assert report["notifications"][0]["kind"] == "pending_approvals"
    assert report["deliveries"][0]["status"] == "dry_run"
    event_types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "notification.dispatched" in event_types


def test_observability_snapshot_empty_engine(engine):
    snapshot = engine.observability_snapshot()

    assert snapshot["status"] == "ok"
    assert snapshot["runtime"]["state"] == "running"
    assert snapshot["finance"]["balance"] == 0.0
    assert snapshot["approvals"]["pending"] == 0
    assert snapshot["projects"]["active"] == 0
    assert snapshot["agents"]["total"] >= 11
    assert snapshot["office"]["theme"] == "virtual_company_floor"
    event_types = [e["event_type"] for e in engine.audit.recent(limit=5)]
    assert "observability.snapshot" in event_types


def test_observability_snapshot_reports_blockers_and_agent_activity(engine):
    from kompany.state.models import ApprovalRequest

    engine.suspend(reason="quota_exhausted")
    engine.approvals.create(ApprovalRequest(
        action_type="directive_execution",
        summary="Approve directive",
    ))
    engine.agent_status.set("coo", "dispatching", "Run project")

    snapshot = engine.observability_snapshot()

    assert snapshot["runtime"]["state"] == "suspended"
    assert snapshot["approvals"]["pending"] == 1
    assert {b["kind"] for b in snapshot["approvals"]["blockers"]} == {"runtime", "approval"}
    coo = next(a for a in snapshot["agents"]["items"] if a["role"] == "coo")
    assert coo["status"] == "dispatching"
    assert snapshot["agents"]["active"] == 1


def test_observability_snapshot_reports_project_task_progress(engine):
    from kompany.state.models import Project, ProjectType, Task, TaskStatus

    project = engine.projects.create(Project(
        name="Build revenue page",
        type=ProjectType.REVENUE,
        target_amount=100.0,
        assigned_agents=["writer", "builder"],
    ))
    task = engine.projects.create_task(Task(
        project_id=project.id,
        title="Draft landing page",
        assigned_agent="writer",
    ))
    engine.projects.update_task_status(task.id, TaskStatus.COMPLETED, result={"ok": True})

    snapshot = engine.observability_snapshot()

    assert snapshot["projects"]["active"] == 1
    assert snapshot["projects"]["items"][0]["name"] == "Build revenue page"
    assert snapshot["projects"]["items"][0]["tasks"]["completed"] == 1
    assert "Build revenue page" in snapshot["office"]["active_projects"]


def test_observability_snapshot_reports_tool_gate_summary(engine):
    engine.set_tool_policy("researcher", "web_search", True, reason="allowed")

    snapshot = engine.observability_snapshot()

    assert snapshot["tools"]["policies"] >= 1
    assert snapshot["tools"]["allowed"] >= 1
    assert snapshot["tools"]["denied"] >= 1


def test_engine_tool_authorization_defaults_to_denied_and_audits(engine):
    result = engine.authorize_tool("researcher", "web_search", purpose="market research")

    assert result["allowed"] is False
    assert result["status"] == "denied"
    events = engine.audit.recent(limit=5)
    assert events[0]["event_type"] == "tool_authorization.denied"


def test_engine_set_tool_policy_allows_and_audits(engine):
    policy = engine.set_tool_policy(
        "researcher",
        "web_search",
        True,
        reason="Researcher may search public docs.",
    )

    result = engine.authorize_tool("researcher", "web_search", purpose="market research")

    assert policy["allowed"] is True
    assert result["allowed"] is True
    assert result["status"] == "allowed"
    event_types = [e["event_type"] for e in engine.audit.recent(limit=5)]
    assert "tool_authorization.policy_updated" in event_types
    assert "tool_authorization.allowed" in event_types


def test_engine_use_tool_denied_does_not_execute_handler(engine):
    calls = []

    def handler(arguments):
        calls.append(arguments)
        return {"ok": True}

    result = engine.use_tool(
        "subagent",
        "external_network",
        purpose="fetch page",
        arguments={"url": "https://example.com"},
        handler=handler,
    )

    assert result["status"] == "denied"
    assert calls == []


def test_engine_use_tool_allowed_executes_handler_and_audits(engine):
    engine.set_tool_policy("researcher", "web_search", True, reason="allowed")

    result = engine.use_tool(
        "researcher",
        "web_search",
        purpose="market research",
        arguments={"query": "Kompany"},
        handler=lambda arguments: {"query": arguments["query"], "count": 1},
    )

    assert result["status"] == "executed"
    assert result["result"] == {"query": "Kompany", "count": 1}
    event_types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "tool_authorization.executed" in event_types


def test_engine_use_tool_sensitive_policy_requests_approval_without_execution(engine):
    engine.set_tool_policy(
        "ciso",
        "backup_restore",
        True,
        reason="Restore needs approval.",
        requires_approval=True,
    )
    calls = []

    result = engine.use_tool(
        "ciso",
        "backup_restore",
        purpose="restore backup",
        arguments={"backup_id": "secret-backup"},
        handler=lambda arguments: calls.append(arguments),
    )

    assert result["status"] == "approval_required"
    assert result["approval_id"]
    assert calls == []
    approval = engine.approvals.get(result["approval_id"])
    assert approval is not None
    assert approval.action_type == "tool_use"
    assert approval.payload == {
        "agent_role": "ciso",
        "tool_name": "backup_restore",
        "purpose": "restore backup",
    }
    assert "secret-backup" not in str(approval.payload)


def test_engine_use_tool_sensitive_policy_executes_after_matching_approval(engine):
    engine.set_tool_policy(
        "ciso",
        "backup_restore",
        True,
        reason="Restore needs approval.",
        requires_approval=True,
    )
    requested = engine.use_tool("ciso", "backup_restore", purpose="restore backup")
    engine.approve_request(requested["approval_id"])

    result = engine.use_tool(
        "ciso",
        "backup_restore",
        purpose="restore backup",
        approval_id=requested["approval_id"],
        handler=lambda arguments: {"restored": True},
    )

    assert result["status"] == "executed"
    assert result["approval_id"] == requested["approval_id"]
    assert result["result"] == {"restored": True}


def test_engine_use_tool_sensitive_policy_rejected_approval_does_not_execute(engine):
    engine.set_tool_policy(
        "ciso",
        "backup_restore",
        True,
        reason="Restore needs approval.",
        requires_approval=True,
    )
    requested = engine.use_tool("ciso", "backup_restore", purpose="restore backup")
    engine.reject_request(requested["approval_id"], reason="too risky")
    calls = []

    result = engine.use_tool(
        "ciso",
        "backup_restore",
        purpose="restore backup",
        approval_id=requested["approval_id"],
        handler=lambda arguments: calls.append(arguments),
    )

    assert result["status"] == "approval_required"
    assert result["approval_id"] == requested["approval_id"]
    assert calls == []


def test_engine_use_tool_sensitive_policy_mismatched_approval_does_not_execute(engine):
    engine.set_tool_policy(
        "ciso",
        "backup_restore",
        True,
        reason="Restore needs approval.",
        requires_approval=True,
    )
    requested = engine.use_tool("ciso", "backup_restore", purpose="restore backup")
    engine.approve_request(requested["approval_id"])
    calls = []

    result = engine.use_tool(
        "ciso",
        "backup_restore",
        purpose="different purpose",
        approval_id=requested["approval_id"],
        handler=lambda arguments: calls.append(arguments),
    )

    assert result["status"] == "approval_required"
    assert result["approval_id"] != requested["approval_id"]
    assert calls == []


def test_engine_use_tool_sanitizes_execution_failures(engine):
    engine.set_tool_policy("researcher", "web_search", True, reason="allowed")

    def handler(arguments):
        raise RuntimeError("secret-token leaked")

    result = engine.use_tool(
        "researcher",
        "web_search",
        purpose="market research",
        handler=handler,
    )

    assert result["status"] == "failed"
    assert "secret-token" not in result["reason"]


def test_engine_suspend_resume_audits(engine):
    snap = engine.suspend(reason="quota_exhausted")
    assert snap["state"] == "suspended"
    assert snap["status"] == "suspended"
    assert snap["reason"] == "quota_exhausted"

    # Idempotent re-suspend
    second = engine.suspend(reason="ignored")
    assert second["status"] == "already_suspended"

    resumed = engine.resume()
    assert resumed["state"] == "running"
    assert resumed["status"] == "resumed"

    # Idempotent re-resume
    second_resume = engine.resume()
    assert second_resume["status"] == "already_running"

    types = [e["event_type"] for e in engine.audit.recent(limit=20)]
    assert types.count("runtime.suspended") == 1
    assert types.count("runtime.resumed") == 1


def test_provider_error_handler_suspends_runtime_and_audits(engine):
    event = {
        "reason": "quota_exhausted",
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "agent_name": "CEO",
        "directive_id": "dir-1",
        "error_type": "RateLimitError",
        "error": "rate limit exceeded",
    }

    engine._handle_provider_error(event)

    assert engine.get_runtime_state()["state"] == "suspended"
    assert engine.get_runtime_state()["reason"] == "quota_exhausted"
    events = engine.audit.recent(limit=10)
    event_types = [e["event_type"] for e in events]
    assert "runtime.quota_exhausted" in event_types
    assert "runtime.suspended" in event_types


def test_provider_error_handler_ignores_non_quota_events(engine):
    engine._handle_provider_error({"reason": "other_failure"})

    assert engine.get_runtime_state()["state"] == "running"
    assert engine.audit.recent(limit=10) == []


def test_process_directive_short_circuits_when_suspended(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    engine.suspend(reason="quota_exhausted")

    result = engine.process_directive("do something")

    assert result.status == "suspended"
    assert "suspended" in result.message.lower()
    assert result.agents_used == []
    assert result.total_ai_cost == 0.0
    types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "directive.suspended_skip" in types
    assert "directive.classified" not in types


def test_resume_project_short_circuits_when_suspended(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    engine.checkpoints.save(
        project_id="project-1",
        task_id="task-1",
        step_index=1,
        state={"last_completed_task": "task-1"},
    )
    engine.suspend(reason="quota_exhausted")

    result = engine.resume_project("project-1")

    assert result["status"] == "suspended"
    assert result["project_id"] == "project-1"
    assert result["latest_checkpoint"]["state"]["last_completed_task"] == "task-1"
    types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "runner.resume_suspended_skip" in types


def test_resume_project_returns_checkpoint_and_result(engine):
    from types import SimpleNamespace
    from kompany.state.models import Project, ProjectType, Task, TaskStatus

    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    project = engine.projects.create(Project(
        name="Resume",
        type=ProjectType.OPERATIONAL,
        plan={},
    ))
    completed = engine.projects.create_task(Task(
        project_id=project.id,
        title="Already done",
        assigned_agent="writer",
        status=TaskStatus.COMPLETED,
    ))
    pending = engine.projects.create_task(Task(
        project_id=project.id,
        title="Continue",
        assigned_agent="writer",
        status=TaskStatus.PENDING,
    ))
    engine.checkpoints.save(
        project_id=project.id,
        task_id=completed.id,
        step_index=1,
        state={"last_completed_task": completed.id},
    )

    class FakeAgent:
        def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
            return SimpleNamespace(text="resumed", cost_usd=0.0)

    class FakeRegistry:
        def get(self, role, company_state=None):
            return FakeAgent()

    engine.registry = FakeRegistry()

    result = engine.resume_project(project.id)

    assert result["status"] == "resumed"
    assert result["project_id"] == project.id
    assert result["latest_checkpoint"]["state"]["last_completed_task"] == completed.id
    assert result["tasks_completed"] == 1
    statuses = {task.id: task.status for task in engine.projects.list_tasks(project.id)}
    assert statuses[completed.id] == TaskStatus.COMPLETED
    # Honest-status (step A): a freshly executed task with no real
    # integration resolves to DELIVERED (asset for the founder), not
    # COMPLETED.
    assert statuses[pending.id] == TaskStatus.DELIVERED


def test_execute_project_short_circuits_when_suspended(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    engine.suspend(reason="manual")

    result = engine.execute_project("project-1")

    assert result["status"] == "suspended"
    assert result["project_id"] == "project-1"
    types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "runner.suspended_skip" in types


def test_engine_credential_methods_do_not_return_or_audit_secret(engine):
    from kompany.state.credentials import CredentialVaultStore

    engine.settings.vault_key = CredentialVaultStore.generate_key()
    engine.credentials = CredentialVaultStore(engine.db, engine.settings.vault_key)

    result = engine.set_credential("telegram_bot_token", "secret-token")
    listed = engine.list_credentials()
    deleted = engine.delete_credential("telegram_bot_token")

    assert result["name"] == "telegram_bot_token"
    assert "secret-token" not in str(result)
    assert listed[0]["name"] == "telegram_bot_token"
    assert "secret-token" not in str(listed)
    assert deleted == {"name": "telegram_bot_token", "deleted": True}
    events = engine.audit.recent(limit=5)
    assert "secret-token" not in str(events)
    assert "ciphertext" not in str(events)


def test_engine_rotate_credential_key_returns_metadata_and_audits_no_secret(engine):
    from kompany.state.credentials import CredentialVaultStore

    old_key = CredentialVaultStore.generate_key()
    new_key = CredentialVaultStore.generate_key()
    engine.settings.vault_key = old_key
    engine.credentials = CredentialVaultStore(engine.db, engine.settings.vault_key)
    engine.set_credential("telegram_bot_token", "secret-token")

    result = engine.rotate_credential_key(new_key)

    assert result == {"rotated": 1, "names": ["telegram_bot_token"]}
    assert engine.settings.vault_key == new_key
    assert engine.credentials.get("telegram_bot_token") == "secret-token"
    events = engine.audit.recent(limit=5)
    assert "credential_vault.key_rotated" in [event["event_type"] for event in events]
    assert "secret-token" not in str(result)
    assert "secret-token" not in str(events)
    assert old_key not in str(result)
    assert new_key not in str(result)
    assert old_key not in str(events)
    assert new_key not in str(events)
    assert "ciphertext" not in str(events)


def test_engine_resolves_vault_key_env_wins_over_keychain(engine, monkeypatch):
    """env vault_key wins over keychain. Previous behavior silently
    masked the env with whatever keychain returned, which made
    KOMPANY_VAULT_KEY unreliable for scripted setups."""
    monkeypatch.setattr(
        "kompany.state.vault_keys.get_vault_key_from_keychain",
        lambda service, account: "keychain-key",
    )
    engine.settings.vault_key = "env-key"
    engine._resolve_vault_key()

    assert engine.settings.vault_key == "env-key"


def test_remote_command_denies_unauthorized_telegram_chat(engine):
    result = engine.handle_remote_command({
        "source": "telegram",
        "text": "/status",
        "chat_id": "999",
    })

    assert result["status"] == "denied"
    assert result["command"] == "status"
    assert result["message"] == "telegram chat is not authorized"
    event = engine.audit.recent(limit=1)[0]
    assert event["event_type"] == "remote_command.denied"
    assert "999" not in str(event["detail"])


def test_remote_command_denies_bad_mobile_token_without_leaking_it(engine):
    result = engine.handle_remote_command({
        "source": "mobile",
        "text": "status",
        "bearer_token": "wrong-secret",
    })

    assert result["status"] == "denied"
    assert result["message"] == "mobile bearer token is invalid"
    event = engine.audit.recent(limit=1)[0]
    assert event["event_type"] == "remote_command.denied"
    assert "wrong-secret" not in str(event["detail"])
    assert "mobile-secret" not in str(event["detail"])


def test_remote_command_help(engine):
    result = engine.handle_remote_command({
        "source": "mobile",
        "text": "help",
        "bearer_token": "mobile-secret",
    })

    assert result["status"] == "executed"
    assert result["command"] == "help"
    assert "approve" in result["result"]["commands"]


def test_remote_command_status(engine):
    result = engine.handle_remote_command({
        "source": "mobile",
        "text": "status",
        "bearer_token": "mobile-secret",
    })

    assert result["status"] == "executed"
    assert result["command"] == "status"
    assert result["result"]["company"]["name"] == "TestCo"


def test_remote_command_approvals_approve_and_reject(engine):
    approval_id = engine._record_autonomy_result(
        __import__("kompany.core.directive", fromlist=["Directive"]).Directive(raw_input="Spend money"),
        can_auto_proceed=False,
        estimated_cost=100.0,
    )

    approvals = engine.handle_remote_command({
        "source": "telegram",
        "text": "approvals",
        "chat_id": "123",
    })
    approved = engine.handle_remote_command({
        "source": "telegram",
        "text": f"approve {approval_id}",
        "chat_id": "123",
    })

    assert approvals["status"] == "executed"
    assert approvals["result"]["approvals"][0]["id"] == approval_id
    assert approved["status"] == "executed"
    assert approved["result"]["status"] == "approved"

    reject_id = engine._record_autonomy_result(
        __import__("kompany.core.directive", fromlist=["Directive"]).Directive(raw_input="Spend more"),
        can_auto_proceed=False,
        estimated_cost=100.0,
    )
    rejected = engine.handle_remote_command({
        "source": "mobile",
        "text": f"reject {reject_id} too risky",
        "bearer_token": "mobile-secret",
    })

    assert rejected["status"] == "executed"
    assert rejected["result"]["status"] == "rejected"
    assert rejected["result"]["resolution_reason"] == "too risky"


def test_remote_command_heartbeat(engine):
    result = engine.handle_remote_command({
        "source": "mobile",
        "text": "heartbeat",
        "bearer_token": "mobile-secret",
    })

    assert result["status"] == "executed"
    assert result["command"] == "heartbeat"
    assert result["result"]["status"] == "ok"


def test_remote_command_unknown_command(engine):
    result = engine.handle_remote_command({
        "source": "mobile",
        "text": "directive make money",
        "bearer_token": "mobile-secret",
    })

    assert result["status"] == "unknown_command"
    assert result["command"] == "directive"
    assert result["result"] is None


def test_remote_command_mobile_nonce_replays_approval_result(engine):
    approval_id = engine._record_autonomy_result(
        __import__("kompany.core.directive", fromlist=["Directive"]).Directive(raw_input="Spend money"),
        can_auto_proceed=False,
        estimated_cost=100.0,
    )
    request = {
        "source": "mobile",
        "text": f"approve {approval_id}",
        "bearer_token": "mobile-secret",
        "payload": {"nonce": "nonce-approve-1"},
    }

    first = engine.handle_remote_command(request)
    second = engine.handle_remote_command(request)

    assert first["status"] == "executed"
    assert first["replayed"] is False
    assert second["status"] == "executed"
    assert second["replayed"] is True
    assert second["result"]["status"] == "approved"
    assert engine.approvals.get(approval_id).status.value == "approved"
    event_types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "remote_command.replayed" in event_types


def test_remote_command_telegram_update_id_replays_reject_result(engine):
    approval_id = engine._record_autonomy_result(
        __import__("kompany.core.directive", fromlist=["Directive"]).Directive(raw_input="Spend more"),
        can_auto_proceed=False,
        estimated_cost=100.0,
    )
    request = {
        "source": "telegram",
        "text": f"reject {approval_id} too risky",
        "chat_id": "123",
        "payload": {"update_id": 777},
    }

    first = engine.handle_remote_command(request)
    second = engine.handle_remote_command(request)

    assert first["status"] == "executed"
    assert first["replayed"] is False
    assert second["status"] == "executed"
    assert second["replayed"] is True
    assert second["result"]["status"] == "rejected"
    assert second["result"]["resolution_reason"] == "too risky"


def test_remote_command_without_replay_key_preserves_current_behavior(engine):
    approval_id = engine._record_autonomy_result(
        __import__("kompany.core.directive", fromlist=["Directive"]).Directive(raw_input="Spend money"),
        can_auto_proceed=False,
        estimated_cost=100.0,
    )
    request = {
        "source": "mobile",
        "text": f"approve {approval_id}",
        "bearer_token": "mobile-secret",
    }

    first = engine.handle_remote_command(request)
    second = engine.handle_remote_command(request)

    assert first["status"] == "executed"
    assert first["replayed"] is False
    assert second["status"] == "executed"
    assert second["replayed"] is False
    assert engine.remote_replay.get("mobile", approval_id) is None
    event_types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "remote_command.replayed" not in event_types


def test_remote_command_replay_detection_happens_after_auth(engine):
    request = {
        "source": "mobile",
        "text": "status",
        "bearer_token": "wrong-secret",
        "payload": {"nonce": "bad-auth-nonce"},
    }

    first = engine.handle_remote_command(request)
    second = engine.handle_remote_command(request)

    assert first["status"] == "denied"
    assert first["replayed"] is False
    assert second["status"] == "denied"
    assert second["replayed"] is False
    assert engine.remote_replay.get("mobile", "bad-auth-nonce") is None


def test_remote_command_auto_cleanup_removes_expired_replay_before_check(engine):
    old_result = {
        "source": "mobile",
        "status": "executed",
        "command": "status",
        "message": "old",
        "result": {"old": True},
        "replayed": False,
    }
    engine.remote_replay.store("mobile", "expired-nonce", "status", old_result)
    engine.db.execute(
        "UPDATE remote_command_replays SET created_at = datetime('now', '-8 days') WHERE replay_key = ?",
        ("expired-nonce",),
    )
    engine.db.commit()
    engine.settings.remote_replay_ttl_seconds = 7 * 24 * 60 * 60

    result = engine.handle_remote_command({
        "source": "mobile",
        "text": "status",
        "bearer_token": "mobile-secret",
        "payload": {"nonce": "expired-nonce"},
    })

    assert result["status"] == "executed"
    assert result["message"] == "Kompany status snapshot"
    assert result["replayed"] is False
    assert engine.remote_replay.get("mobile", "expired-nonce") == result
    event_types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "remote_command.replay_cleanup" in event_types
    assert "remote_command.replayed" not in event_types


def test_cleanup_remote_replays_returns_metadata_and_audits_no_secret(engine):
    result = {
        "source": "mobile",
        "status": "executed",
        "command": "status",
        "message": "ok",
        "result": {"ok": True},
        "replayed": False,
    }
    engine.remote_replay.store("mobile", "secret-nonce", "status", result)
    engine.db.execute(
        "UPDATE remote_command_replays SET created_at = datetime('now', '-2 hours') WHERE replay_key = ?",
        ("secret-nonce",),
    )
    engine.db.commit()

    cleanup = engine.cleanup_remote_replays(ttl_seconds=3600)

    assert cleanup["deleted"] == 1
    assert cleanup["remaining"] == 0
    assert cleanup["ttl_seconds"] == 3600
    events = engine.audit.recent(limit=5)
    assert "secret-nonce" not in str(cleanup)
    assert "secret-nonce" not in str(events)
    assert "bearer_token" not in str(events)


def test_engine_create_backup_audits(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)

    meta = engine.create_backup(label="post-init")

    assert meta["label"] == "post-init"
    assert meta["size_bytes"] > 0
    listed = engine.list_backups()
    assert any(b["id"] == meta["id"] for b in listed)
    event_types = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "backup.created" in event_types


def test_engine_restore_backup_reverts_state_and_audits(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=100.0)
    snap = engine.create_backup(label="state-A")
    assert engine.ledger.get_balance() == 100.0

    # Mutate to a new state.
    engine.ledger.record(
        amount=-25.0,
        description="post-snapshot expense",
        category=LedgerCategory.EXPENSE,
    )
    assert engine.ledger.get_balance() == 75.0

    result = engine.restore_backup(snap["id"])

    assert result["id"] == snap["id"]
    assert result["auto_pre_restore_id"]
    # Live store rebound; balance is back to snapshot state.
    assert engine.ledger.get_balance() == 100.0
    # Auto pre-restore exists in backup list.
    listed = engine.list_backups()
    assert any(b["id"] == result["auto_pre_restore_id"] for b in listed)
    # backup.restored audit lives in the restored DB.
    event_types = [e["event_type"] for e in engine.audit.recent(limit=20)]
    assert "backup.restored" in event_types


def test_engine_restore_backup_unknown_raises(engine):
    engine.initialize_company(name="TestCo", goal="AI tools", capital=10.0)

    with pytest.raises(FileNotFoundError):
        engine.restore_backup("nope")


def test_approval_required_creates_pending_request(engine):
    """AutonomyGate blocks should persist pending approval requests."""
    from kompany.core.directive import Directive

    directive = Directive(raw_input="Spend money")
    directive.requires_approval = "master"
    approval_id = engine._record_autonomy_result(
        directive,
        can_auto_proceed=False,
        estimated_cost=100.0,
    )

    pending = engine.list_approvals()
    assert approval_id is not None
    assert len(pending) == 1
    assert pending[0]["id"] == approval_id
    assert pending[0]["status"] == "pending"

    approved = engine.approve_request(approval_id)
    assert approved is not None
    assert approved["status"] == "approved"
    assert engine.list_approvals() == []

    event_types = [event["event_type"] for event in engine.audit.recent(limit=10)]
    assert "autonomy.approval_required" in event_types
    assert "approval.approved" in event_types
