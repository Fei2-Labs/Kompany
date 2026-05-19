"""End-to-end approval thread + RPG inbox flow.

Covers the four critical scenarios called out by
``05-18-approval-thread-and-rpg``'s PRD:

1. Happy revise path (player counter-proposal -> successor approval with
   ``predecessor_id`` chain).
2. Snooze auto-unsnooze via watchdog scanner.
3. Episode payload materialises ``approval_events`` with comments + chain.
4. The five existing engine ``approvals.create`` call sites keep working
   with their new ``severity`` argument.
"""

from __future__ import annotations

import json

import pytest

from kompany.core.engine import KompanyEngine
from kompany.core.watchdog import Watchdog
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.backup import BackupManager
from kompany.state.checkpoints import CheckpointStore
from kompany.state.credentials import CredentialVaultStore
from kompany.state.database import Database
from kompany.state.debates import Debates
from kompany.state.episode_payload import EpisodePayloadV1
from kompany.state.episodes import Episodes
from kompany.state.health_events import HealthEvents
from kompany.state.journal import Journal
from kompany.state.ledger import Ledger
from kompany.state.memory import AgentMemory
from kompany.state.models import (
    ApprovalRequest,
    ApprovalStatus,
    LedgerCategory,
    Project,
    ProjectType,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects
from kompany.state.remote_replay import RemoteReplayStore
from kompany.state.runtime import RuntimeStateStore
from kompany.state.tool_authorization import ToolAuthorizationStore


class _TestSettings:
    """Minimal settings stub mirroring the episode-materialisation tests."""
    company_name = "TestCo"
    company_goal = "AI tools"
    company_stage = "solo"
    company_time_horizon = ""
    company_exclusions = ""
    data_dir = None  # filled in per-test
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


def _make_engine(tmp_path) -> KompanyEngine:
    """Build a hollow engine (no LLM init) sharing one DB across all stores."""
    from kompany.agents.registry import AgentRegistry
    from kompany.core.autonomy import AutonomyGate
    from kompany.llm.cost_tracker import CostTracker

    settings = _TestSettings()
    settings.data_dir = tmp_path

    db = Database(tmp_path)
    ledger = Ledger(db)
    journal = Journal(db)
    projects = Projects(db)
    memory = AgentMemory(db)
    audit = AuditLog(db)
    debates = Debates(db)
    episodes = Episodes(db)
    health = HealthEvents(db)
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
    engine.debates = debates
    engine.episodes = episodes
    engine.health_events = health
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
    engine.watchdog = Watchdog(
        health_events=health,
        projects=projects,
        audit=audit,
        approvals=approvals,
        scan_interval_seconds=1,
        stale_threshold_seconds=600,
    )
    # The approval revision-handler registry is normally set inside
    # ``__init__``; we mimic that here for the hollow engine.
    engine._revision_handlers = {}
    return engine


# ---------------------------------------------------------------------------
# 1) Happy revise path
# ---------------------------------------------------------------------------


def test_request_revision_creates_successor_with_predecessor_link(tmp_path):
    engine = _make_engine(tmp_path)
    original = engine.approvals.create(ApprovalRequest(
        action_type="decision_chain_execution",
        summary="Approve spend €500 on Anthropic API",
        payload={"cost": 500, "vendor": "anthropic"},
        directive_id="dir-1",
        project_id=None,
        requested_by="cfo",
        severity="medium",
    ))

    result = engine.request_approval_revision(
        original.id,
        counter="do half the spend (€250) only",
    )
    assert result is not None
    assert result["original"]["status"] == "revision_requested"
    assert result["successor"]["status"] == "pending"
    assert result["successor"]["predecessor_id"] == original.id
    # The hint lands inside the new payload.
    assert (
        result["successor"]["payload"]["revision_hint"]
        == "do half the spend (€250) only"
    )
    # Inherited fields preserved on the successor.
    assert result["successor"]["severity"] == "medium"
    assert result["successor"]["action_type"] == "decision_chain_execution"

    # The thread links both rows oldest-first.
    thread = engine.approvals.list_thread(original.id)
    assert [r.status for r in thread] == [
        ApprovalStatus.REVISION_REQUESTED,
        ApprovalStatus.PENDING,
    ]
    # And the comments on the original include the counter text.
    comments = engine.approvals.list_comments(original.id)
    assert any("do half" in c.body for c in comments)


def test_register_revision_handler_overrides_default(tmp_path):
    engine = _make_engine(tmp_path)
    captured = []

    def my_handler(original, hint):
        captured.append((original.id, hint))
        return engine.approvals.create(ApprovalRequest(
            action_type=original.action_type,
            summary=f"[CFO-rewritten] {original.summary}",
            payload={"strategy": "halve"},
            predecessor_id=original.id,
            severity="low",
        ))

    engine.register_revision_handler("decision_chain_execution", my_handler)
    original = engine.approvals.create(ApprovalRequest(
        action_type="decision_chain_execution",
        summary="x",
    ))

    result = engine.request_approval_revision(original.id, counter="halve it")
    assert captured == [(original.id, "halve it")]
    assert result["successor"]["payload"] == {"strategy": "halve"}
    assert result["successor"]["severity"] == "low"


# ---------------------------------------------------------------------------
# 2) Snooze + auto-unsnooze via watchdog scanner
# ---------------------------------------------------------------------------


def test_snooze_then_scanner_auto_unsnoozes_after_window(tmp_path):
    engine = _make_engine(tmp_path)
    original = engine.approvals.create(ApprovalRequest(
        action_type="delivery_approval",
        summary="approve delivery",
        severity="high",
    ))

    engine.snooze_approval(original.id, minutes=30)
    snoozed = engine.approvals.get(original.id)
    assert snoozed.status == ApprovalStatus.SNOOZED

    # First scanner tick at t+15min (no change) — emulate by leaving
    # snoozed_until in the future and just running scan_once.
    unsnoozed_now = engine.watchdog._scan_snoozed_approvals()
    assert unsnoozed_now == []
    still_snoozed = engine.approvals.get(original.id)
    assert still_snoozed.status == ApprovalStatus.SNOOZED

    # Second tick at t+31min — emulate by back-dating ``snoozed_until``
    # past ``datetime('now')``.
    engine.db.execute(
        "UPDATE approval_requests SET snoozed_until = datetime('now', '-1 minutes') "
        "WHERE id = ?",
        (original.id,),
    )
    engine.db.commit()
    flipped = engine.watchdog._scan_snoozed_approvals()
    assert len(flipped) == 1
    assert flipped[0]["id"] == original.id
    assert flipped[0]["status"] == "pending"

    # The system "auto-unsnoozed after Nm" comment is present.
    bodies = [c.body for c in engine.approvals.list_comments(original.id)]
    assert any(b.startswith("auto-unsnoozed after ") for b in bodies)
    # Original "snoozed for 30m" line preserved too.
    assert any(b == "snoozed for 30m" for b in bodies)


def test_scan_once_calls_snooze_sweep(tmp_path):
    """The watchdog's ``scan_once`` drives the snooze sweep as a side-effect."""
    engine = _make_engine(tmp_path)
    original = engine.approvals.create(ApprovalRequest(
        action_type="t", summary="s", severity="medium",
    ))
    engine.snooze_approval(original.id, minutes=10)
    engine.db.execute(
        "UPDATE approval_requests SET snoozed_until = datetime('now', '-1 minutes') "
        "WHERE id = ?",
        (original.id,),
    )
    engine.db.commit()

    engine.watchdog.scan_once()
    refreshed = engine.approvals.get(original.id)
    assert refreshed.status == ApprovalStatus.PENDING


# ---------------------------------------------------------------------------
# 3) Episode payload integration
# ---------------------------------------------------------------------------


def test_episode_payload_carries_approval_events(tmp_path):
    engine = _make_engine(tmp_path)
    # Seed a project the materializer can reach.
    project = Project(
        id="proj-x",
        name="Project X",
        type=ProjectType.OPERATIONAL,
        assigned_agents=["coo", "cfo"],
        triggers_directive_id="dir-x",
    )
    engine.projects.create(project)
    engine.projects.create_task(Task(
        id="proj-x-t0",
        project_id="proj-x",
        title="Task 0",
        assigned_agent="coo",
        status=TaskStatus.PENDING,
    ))
    engine.projects.update_task_status("proj-x-t0", TaskStatus.COMPLETED, result={"ok": True})
    engine.ledger.record(
        amount=10.0,
        description="invoice",
        category=LedgerCategory.INCOME,
        project_id="proj-x",
    )

    # Create + resolve a small approval thread on this project.
    a = engine.approvals.create(ApprovalRequest(
        action_type="decision_chain_execution",
        summary="approve task",
        project_id="proj-x",
        directive_id="dir-x",
        severity="high",
        requested_by="cfo",
    ))
    engine.request_approval_revision(a.id, counter="please scope down")
    # Find the successor pending row and approve it.
    successors = [
        r for r in engine.approvals.list_for_project("proj-x")
        if r.predecessor_id == a.id
    ]
    assert len(successors) == 1
    engine.approve_request(successors[0].id, comment_body="green light")

    # Materialize the episode.
    payload = engine.episodes.materialize("proj-x")
    assert isinstance(payload, EpisodePayloadV1)
    # Two approvals expected: the revised original + the approved successor.
    outcomes = {e.outcome for e in payload.approval_events}
    assert outcomes == {"revision_requested", "approved"}
    # Comments are present (counter proposal + the green-light note).
    all_comments = [c.text for e in payload.approval_events for c in e.comments]
    assert any("please scope down" in body for body in all_comments)
    assert any(body == "green light" for body in all_comments)

    # Round-trip through JSON contract.
    blob = payload.model_dump_json()
    EpisodePayloadV1.model_validate_json(blob)


# ---------------------------------------------------------------------------
# 4) Backward compatibility — existing engine call sites
# ---------------------------------------------------------------------------


def test_existing_engine_call_sites_remain_legal(tmp_path):
    """Each of the 5 legacy ``ApprovalRequest(...)`` shapes must still create
    a row whose ``severity`` is what the new code now sets explicitly."""
    engine = _make_engine(tmp_path)

    # 1) directive_execution (medium) — patterned after engine.py:1919.
    r1 = engine.approvals.create(ApprovalRequest(
        action_type="directive_execution",
        summary="Approve directive",
        payload={"approval_tier": "master"},
        directive_id="dir-1",
        requested_by="AutonomyGate",
        severity="medium",
    ))
    # 2) decision_chain_execution (medium) — engine.py:371.
    r2 = engine.approvals.create(ApprovalRequest(
        action_type="decision_chain_execution",
        summary="Approve decision packet",
        payload={"packet": {}},
        directive_id="dir-2",
        requested_by="AutonomyGate",
        severity="medium",
    ))
    # 3) delivery_approval (high) — engine.py:466.
    r3 = engine.approvals.create(ApprovalRequest(
        action_type="delivery_approval",
        summary="Approve delivery",
        project_id="proj-1",
        requested_by="AutonomyGate",
        severity="high",
    ))
    # 4) tool_use (critical) — engine.py:1479.
    r4 = engine.approvals.create(ApprovalRequest(
        action_type="tool_use",
        summary="Approve tool use: coo -> bash",
        requested_by="ToolAuthorizationGate",
        severity="critical",
    ))
    # 5) override (high) — engine.py:1766.
    r5 = engine.approvals.create(ApprovalRequest(
        action_type="override",
        summary="Approve override",
        directive_id="dir-3",
        requested_by="KompanyEngine",
        severity="high",
    ))

    for r in (r1, r2, r3, r4, r5):
        fetched = engine.approvals.get(r.id)
        assert fetched is not None
        assert fetched.status == ApprovalStatus.PENDING


def test_default_revision_handler_does_not_loop(tmp_path):
    """Revising the default handler's output must not auto-spawn another row."""
    engine = _make_engine(tmp_path)
    original = engine.approvals.create(ApprovalRequest(
        action_type="custom", summary="x", severity="medium",
    ))
    first = engine.request_approval_revision(original.id, counter="hint A")
    successor_id = first["successor"]["id"]
    # Revising the successor — also via the default handler — produces ONE
    # extra successor, not infinite ones, and the new payload contains the
    # NEW hint (not stacked).
    second = engine.request_approval_revision(successor_id, counter="hint B")
    assert second["successor"]["payload"]["revision_hint"] == "hint B"
    # Now the thread holds 3 rows (original, succ1, succ2) in chain.
    thread = engine.approvals.list_thread(original.id)
    assert len(thread) == 3
    assert [r.predecessor_id for r in thread] == [None, original.id, successor_id]
