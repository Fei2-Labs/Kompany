"""Tests for the engine's ``glossary_review`` approval lifecycle.

Glossary-and-drift-detection task 05-19. Covers all three founder
outcomes — approve, reject, revise — by driving
``KompanyEngine.approve_request`` / ``reject_request`` /
``revise_request`` directly with a hand-built ``glossary_review`` row.

Why no LLM here: drift detection runs at retrospective time and writes
real DB rows; the approval handler then operates on those rows. We can
seed the handler's inputs deterministically without firing a CoS call.
"""

from __future__ import annotations

from typing import Any

import pytest

from kompany.state.models import ApprovalRequest


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Build a hollow engine with the real state stores wired up."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))

    class TestSettings:
        company_name = "TestCo"
        company_goal = "ship"
        company_stage = "solo"
        company_time_horizon = ""
        company_exclusions = ""
        data_dir = tmp_path
        anthropic_api_key = "test-key"
        openai_api_key = ""
        telegram_bot_token = ""
        telegram_chat_id = ""
        telegram_allowed_chat_ids = "123"
        mobile_remote_token = ""
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
            return self.model_primary

        def get_api_key_for_provider(self, _):
            return ""

    from kompany.agents.registry import AgentRegistry
    from kompany.core.engine import KompanyEngine
    from kompany.core.watchdog import Watchdog
    from kompany.llm.cost_tracker import CostTracker
    from kompany.state.agent_status import AgentStatusStore
    from kompany.state.approvals import ApprovalRequests
    from kompany.state.audit import AuditLog
    from kompany.state.backup import BackupManager
    from kompany.state.checkpoints import CheckpointStore
    from kompany.state.credentials import CredentialVaultStore
    from kompany.state.database import Database
    from kompany.state.glossary import GlossaryService
    from kompany.state.health_events import HealthEvents
    from kompany.state.journal import Journal
    from kompany.state.ledger import Ledger
    from kompany.state.memory import AgentMemory
    from kompany.state.projects import Projects
    from kompany.state.remote_replay import RemoteReplayStore
    from kompany.state.runtime import RuntimeStateStore
    from kompany.state.tool_authorization import ToolAuthorizationStore

    settings = TestSettings()
    db = Database(tmp_path)

    eng = KompanyEngine.__new__(KompanyEngine)
    eng.settings = settings
    eng.db = db
    eng.ledger = Ledger(db)
    eng.journal = Journal(db)
    eng.projects = Projects(db)
    eng.memory = AgentMemory(db)
    eng.audit = AuditLog(db)
    eng.approvals = ApprovalRequests(db)
    eng.agent_status = AgentStatusStore(db)
    eng.checkpoints = CheckpointStore(db)
    eng.cost_tracker = CostTracker(eng.ledger)
    eng.backups = BackupManager(tmp_path)
    eng.runtime = RuntimeStateStore(db)
    eng.remote_replay = RemoteReplayStore(db)
    eng.credentials = CredentialVaultStore(db, settings.vault_key)
    eng.tool_authorization = ToolAuthorizationStore(db)
    eng.health_events = HealthEvents(db)
    eng.watchdog = Watchdog(
        health_events=eng.health_events,
        projects=eng.projects,
        audit=eng.audit,
    )
    eng.glossary = GlossaryService(db)
    eng.autonomy = __import__(
        "kompany.core.autonomy", fromlist=["AutonomyGate"]
    ).AutonomyGate()
    eng.llm = None
    eng.registry = AgentRegistry(None, settings, eng.ledger)
    eng._approval_revision_handlers = {}
    eng._approval_revision_handlers["glossary_review"] = (
        eng._glossary_review_revision_handler
    )
    return eng


def _seed_glossary_review(engine, project_id: str = "p1") -> tuple[str, str]:
    """Create one open glossary_drift_alert + one matching approval row.

    Returns ``(approval_id, health_event_id)``.
    """
    drifts = [
        {
            "term": "customer",
            "drifted_synonym": "user",
            "agent_role": "cmo",
            "count": 3,
            "sample_excerpt": "... user growth ...",
            "source": "reflection",
        }
    ]
    suggestions = [
        {
            "term": "customer",
            "drifted_synonym": "user",
            "agent_role": "cmo",
            "count": 3,
            "definition": "paying account",
            "suggested_replacement": "Use 'customer' instead of 'user' (cmo used 3x)",
        }
    ]
    approval = engine.approvals.create(
        ApprovalRequest(
            action_type="glossary_review",
            summary="Glossary drift in episode p1: 1 hit",
            payload={
                "project_id": project_id,
                "drifts": drifts,
                "suggested_corrections": suggestions,
            },
            project_id=project_id,
            severity="medium",
            requested_by="cos",
        )
    )
    event = engine.watchdog.record_glossary_drift(
        episode_id=project_id,
        drifts=drifts,
        project_id=project_id,
        approval_id=approval.id,
    )
    return approval.id, event["id"]


# ---------------------------------------------------------------------------
# Approve path: health event resolves, audit records "drift_resolved"
# ---------------------------------------------------------------------------


def test_approve_glossary_review_closes_health_event(engine) -> None:
    approval_id, event_id = _seed_glossary_review(engine)

    result = engine.approve_request(approval_id, approved_by="founder")
    assert result is not None
    assert result["status"] == "approved"

    # The matching health event should be closed.
    ev = engine.health_events.get(event_id)
    assert ev["status"] in {"resolved", "dismissed"}

    # An audit row tagged ``glossary.drift_resolved`` must exist.
    rows = engine.db.execute(
        "SELECT event_type FROM audit_log "
        "WHERE event_type = 'glossary.drift_resolved'"
    ).fetchall()
    assert rows, "expected glossary.drift_resolved audit row"


# ---------------------------------------------------------------------------
# Reject path: health event closes, audit records "drift_dismissed"
# ---------------------------------------------------------------------------


def test_reject_glossary_review_dismisses_health_event(engine) -> None:
    approval_id, event_id = _seed_glossary_review(engine)

    result = engine.reject_request(
        approval_id,
        rejected_by="founder",
        reason="false positive",
    )
    assert result is not None
    assert result["status"] == "rejected"

    ev = engine.health_events.get(event_id)
    assert ev["status"] in {"resolved", "dismissed"}

    rows = engine.db.execute(
        "SELECT event_type FROM audit_log "
        "WHERE event_type = 'glossary.drift_dismissed'"
    ).fetchall()
    assert rows, "expected glossary.drift_dismissed audit row"


# ---------------------------------------------------------------------------
# Revise path: handler issues a successor approval
# ---------------------------------------------------------------------------


def test_revise_glossary_review_spawns_successor(engine) -> None:
    approval_id, _ = _seed_glossary_review(engine)

    successor = engine._glossary_review_revision_handler(
        engine.approvals.get(approval_id),
        hint="Only fix the customer drift; skip MRR.",
    )

    assert successor.predecessor_id == approval_id
    assert successor.action_type == "glossary_review"
    assert successor.status.value == "pending"
    assert successor.payload["revision_hint"] == "Only fix the customer drift; skip MRR."
    # The original drift payload survives.
    assert successor.payload["drifts"][0]["term"] == "customer"


# ---------------------------------------------------------------------------
# Approval row exposes drift payload for the inbox
# ---------------------------------------------------------------------------


def test_glossary_review_payload_carries_drift_details(engine) -> None:
    approval_id, _ = _seed_glossary_review(engine)
    request = engine.approvals.get(approval_id)
    assert request.action_type == "glossary_review"
    assert request.payload["project_id"] == "p1"
    assert len(request.payload["drifts"]) == 1
    hit = request.payload["drifts"][0]
    assert hit["term"] == "customer"
    assert hit["drifted_synonym"] == "user"
    assert hit["agent_role"] == "cmo"
    suggestions = request.payload["suggested_corrections"]
    assert suggestions[0]["term"] == "customer"
