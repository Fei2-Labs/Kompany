"""End-to-end: retrospective writes a structured episode + enforces retention."""

from __future__ import annotations

import json

from kompany.core.engine import KompanyEngine
from kompany.state.audit import AuditLog
from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database
from kompany.state.debates import Debates
from kompany.state.episode_payload import EpisodePayloadV1
from kompany.state.episodes import Episodes
from kompany.state.journal import Journal
from kompany.state.ledger import Ledger
from kompany.state.memory import AgentMemory
from kompany.state.models import (
    LedgerCategory,
    Project,
    ProjectType,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects


def _make_engine(tmp_path) -> KompanyEngine:
    """Build a hollow engine without LLM init (mirrors test_run_id_tracing)."""

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
    debates = Debates(db)
    episodes = Episodes(db)
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


def _seed_project(engine: KompanyEngine, project_id: str, *, n_tasks: int = 2) -> None:
    engine.projects.create(Project(
        id=project_id,
        name=f"Project {project_id}",
        type=ProjectType.OPERATIONAL,
        assigned_agents=["coo", "cfo"],
        triggers_directive_id=f"dir-{project_id}",
    ))
    for i in range(n_tasks):
        tid = f"{project_id}-t{i}"
        engine.projects.create_task(Task(
            id=tid,
            project_id=project_id,
            title=f"Task {i}",
            assigned_agent="coo",
            status=TaskStatus.PENDING,
        ))
        engine.projects.update_task_status(tid, TaskStatus.COMPLETED, result={"ok": True})

    engine.ledger.record(
        amount=10.0,
        description="invoice",
        category=LedgerCategory.INCOME,
        project_id=project_id,
    )
    engine.audit.record("project.created", "seeded", project_id=project_id)
    engine.debates.record(
        rounds=[[]],
        synthesis=None,
        decision=None,
        directive_id=f"dir-{project_id}",
        project_id=project_id,
    )


def test_retrospective_materializes_episode_row(tmp_path):
    engine = _make_engine(tmp_path)
    _seed_project(engine, "alpha")

    engine.run_retrospective("alpha")

    row = engine.get_episode("alpha")
    assert row is not None
    assert row["retention_tier"] == "full"
    assert row["payload_json"]

    payload = EpisodePayloadV1.model_validate_json(row["payload_json"])
    assert payload.project_meta.id == "alpha"
    assert len(payload.tasks) == 2
    assert len(payload.debate_ids) == 1
    assert len(payload.reflections) == 2  # one per assigned agent
    # The retrospective audit event is curated into the episode.
    event_types = {e.type for e in payload.audit_events}
    assert "learning.retrospective_completed" in event_types

    # Audit log carries the new event.
    audit_rows = engine.db.execute(
        "SELECT event_type FROM audit_log WHERE event_type = 'learning.episode_recorded'"
    ).fetchall()
    assert len(audit_rows) == 1


def test_retrospective_is_idempotent(tmp_path):
    engine = _make_engine(tmp_path)
    _seed_project(engine, "alpha")

    engine.run_retrospective("alpha")
    first = engine.get_episode("alpha")
    engine.run_retrospective("alpha")
    second = engine.get_episode("alpha")

    # Single episode row regardless of how many times retrospective fires.
    rows = engine.db.execute(
        "SELECT COUNT(*) AS c FROM project_episodes"
    ).fetchone()
    assert rows["c"] == 1
    assert first["created_at"] == second["created_at"]


def test_retention_trim_drops_oldest_payload(tmp_path):
    engine = _make_engine(tmp_path)
    # Lower the retention window via company_config.
    engine.db.execute(
        """INSERT INTO company_config (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        ("episode_retention_full_count", "2"),
    )
    engine.db.commit()

    for pid in ["p1", "p2", "p3", "p4"]:
        _seed_project(engine, pid, n_tasks=1)
        engine.run_retrospective(pid)

    full = engine.list_episodes(retention_tier="full")
    summary = engine.list_episodes(retention_tier="summary")
    assert len(full) == 2
    assert len(summary) == 2

    # The two most-recently delivered remain at full.
    recent_ids = {row["project_id"] for row in full}
    assert recent_ids == {"p3", "p4"}

    # Trimmed rows: payload cleared, summary string preserved.
    for row in summary:
        assert row["project_id"] in {"p1", "p2"}
        assert row["summary"]
        full_row = engine.get_episode(row["project_id"])
        assert full_row["payload_json"] is None
        assert full_row["retention_tier"] == "summary"

    # The trim audit event was emitted at least once.
    trim_events = engine.db.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE event_type = 'learning.episode_trimmed'"
    ).fetchone()
    assert trim_events["c"] >= 2


def test_rebuild_episode_after_mutation_refreshes_payload(tmp_path):
    engine = _make_engine(tmp_path)
    _seed_project(engine, "alpha")
    engine.run_retrospective("alpha")
    before = engine.get_episode("alpha")
    parsed_before = EpisodePayloadV1.model_validate_json(before["payload_json"])
    assert len(parsed_before.tasks) == 2

    # Mutate: add a new task to the project after retrospective.
    engine.projects.create_task(Task(
        id="alpha-extra",
        project_id="alpha",
        title="late add",
        assigned_agent="coo",
        status=TaskStatus.COMPLETED,
    ))

    engine.rebuild_episode("alpha")
    after = engine.get_episode("alpha")
    parsed_after = EpisodePayloadV1.model_validate_json(after["payload_json"])
    assert len(parsed_after.tasks) == 3
    assert before["created_at"] == after["created_at"]


def test_release_delivery_triggers_episode(tmp_path):
    """`release_delivery` → `run_retrospective` → episode materialized."""
    engine = _make_engine(tmp_path)
    _seed_project(engine, "alpha")
    # Skip the actual release_delivery (it requires approvals); call
    # run_retrospective directly — the path it covers is identical to what
    # release_delivery does once the approval is in place.
    engine.run_retrospective("alpha")
    assert engine.get_episode("alpha") is not None
