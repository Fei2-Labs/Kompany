"""End-to-end: seed three projects → run distill → assert experiential memories.

Mirrors the integration shape of ``test_episode_materialization.py``:
hollow-engine + fake CoS agent so the test never touches a real LLM
provider but still exercises ``record_or_update`` → ``distill`` → UPSERT.
"""

from __future__ import annotations

import json
from typing import Any

from kompany.agents.cos_distillation import DistillationOutput, DistilledPattern
from kompany.core.engine import KompanyEngine
from kompany.llm.client import LLMResponse
from kompany.state.audit import AuditLog
from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database
from kompany.state.debates import Debates
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


class _FakeCoS:
    role = "cos"
    display_name = "CoS"

    def __init__(self, patterns: list[DistilledPattern], cost: float = 0.05):
        self._patterns = patterns
        self._cost = cost
        self.calls = 0
        self.last_summaries: list[dict[str, Any]] | None = None

    def distill(self, summaries, max_tokens: int = 4096):
        self.calls += 1
        self.last_summaries = summaries
        parsed = DistillationOutput(
            patterns=self._patterns,
            meta={"episodes_consumed": len(summaries)},
        )
        resp = LLMResponse(
            text=parsed.model_dump_json(),
            input_tokens=200,
            output_tokens=80,
            cost_usd=self._cost,
            model="claude-test",
        )
        resp.parsed = parsed
        return resp


def _make_engine(tmp_path, cos: _FakeCoS) -> KompanyEngine:
    """Hollow engine: real DB, no LLM, fake CoS agent on the registry."""

    class TestSettings:
        company_name = "TestCo"
        company_goal = "test"
        company_stage = "solo"
        company_time_horizon = ""
        company_exclusions = ""
        data_dir = tmp_path
        anthropic_api_key = "test"
        currency = "EUR"
        model_apex = "x"
        model_primary = "x"
        model_economy = "x"

        def get_model_for_tier(self, tier):
            return "x"

    from kompany.core.autonomy import AutonomyGate
    from kompany.llm.cost_tracker import CostTracker
    from kompany.state.agent_status import AgentStatusStore
    from kompany.state.backup import BackupManager
    from kompany.state.checkpoints import CheckpointStore
    from kompany.state.credentials import CredentialVaultStore
    from kompany.state.remote_replay import RemoteReplayStore
    from kompany.state.runtime import RuntimeStateStore
    from kompany.state.tool_authorization import ToolAuthorizationStore

    db = Database(tmp_path)
    ledger = Ledger(db)
    audit = AuditLog(db)
    journal = Journal(db)
    projects = Projects(db)
    memory = AgentMemory(db)
    episodes = Episodes(db)
    debates = Debates(db)
    approvals = ApprovalRequests(db)
    cost_tracker = CostTracker(ledger)

    engine = KompanyEngine.__new__(KompanyEngine)
    engine.settings = TestSettings()
    engine.db = db
    engine.ledger = ledger
    engine.journal = journal
    engine.projects = projects
    engine.memory = memory
    engine.audit = audit
    engine.episodes = episodes
    engine.debates = debates
    engine.approvals = approvals
    engine.cost_tracker = cost_tracker
    engine.agent_status = AgentStatusStore(db)
    engine.checkpoints = CheckpointStore(db)
    engine.runtime = RuntimeStateStore(db)
    engine.remote_replay = RemoteReplayStore(db)
    engine.credentials = CredentialVaultStore(db, "")
    engine.tool_authorization = ToolAuthorizationStore(db)
    engine.backups = BackupManager(tmp_path)
    engine.autonomy = AutonomyGate()
    engine.llm = None

    class _Registry:
        def __init__(self, agent):
            self._agent = agent

        def get(self, role, company_state=None):
            assert role == "cos"
            return self._agent

    engine.registry = _Registry(cos)
    return engine


def _seed_project_with_episode(
    engine: KompanyEngine,
    project_id: str,
    *,
    n_tasks: int = 2,
) -> None:
    """Create a project, run its tasks, then materialize the episode row."""
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
        engine.projects.update_task_status(
            tid, TaskStatus.COMPLETED, result={"ok": True},
        )
    engine.ledger.record(
        amount=20.0,
        description=f"{project_id} invoice",
        category=LedgerCategory.INCOME,
        project_id=project_id,
    )
    # Add a reflection so the distillation payload has signal to chew on.
    engine.memory.remember(
        agent_role="cfo",
        content=f"{project_id}: invoice cleared at $20.",
        category="reflection",
        context=f"project:{project_id}",
    )
    engine.audit.record("project.created", "seeded", project_id=project_id)
    # Materialize the episode payload (P0 contract: episode row exists).
    engine.episodes.record_or_update(project_id)


def test_distill_writes_experiential_memories_across_projects(tmp_path):
    """Three seeded projects + canned CoS output ⇒ three memories land."""
    patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="invoice-clearance",
            pattern_summary="Invoices cleared at $20 across recent projects.",
            confidence=0.7,
            evidence_episode_ids=["alpha", "beta", "gamma"],
        ),
        DistilledPattern(
            target_agent_role="coo",
            pattern_key="task-cadence",
            pattern_summary="Two-task projects close inside a week.",
            confidence=0.6,
            evidence_episode_ids=["alpha", "beta"],
        ),
    ]
    cos = _FakeCoS(patterns)
    engine = _make_engine(tmp_path, cos)
    for pid in ["alpha", "beta", "gamma"]:
        _seed_project_with_episode(engine, pid)

    result = engine.distill()

    assert result["status"] == "completed"
    assert result["episodes_in"] == 3
    assert result["patterns_out"] == 2
    assert cos.calls == 1

    cfo_rows = engine.memory.recall("cfo", category="experiential")
    coo_rows = engine.memory.recall("coo", category="experiential")
    assert len(cfo_rows) == 1
    assert len(coo_rows) == 1


def test_distill_rerun_is_idempotent(tmp_path):
    """Re-running ``distill`` with the same pattern_key updates, not appends."""
    patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="invoice-clearance",
            pattern_summary="Invoices cleared at $20 across recent projects.",
            confidence=0.7,
            evidence_episode_ids=["alpha", "beta"],
        )
    ]
    cos = _FakeCoS(patterns)
    engine = _make_engine(tmp_path, cos)
    for pid in ["alpha", "beta"]:
        _seed_project_with_episode(engine, pid)

    engine.distill()
    first = engine.memory.recall("cfo", category="experiential")
    assert len(first) == 1

    # Second run with the same key but a refreshed summary.
    cos._patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="invoice-clearance",
            pattern_summary="Invoices reliably clear at $20 (n=3 projects).",
            confidence=0.9,
            evidence_episode_ids=["alpha", "beta", "gamma"],
        )
    ]
    _seed_project_with_episode(engine, "gamma")
    engine.distill()
    second = engine.memory.recall("cfo", category="experiential")
    assert len(second) == 1
    assert second[0]["content"].endswith("(n=3 projects).")


def test_distill_dry_run_writes_audit_only(tmp_path):
    patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="dry-key",
            pattern_summary="phantom",
            confidence=0.5,
            evidence_episode_ids=["alpha"],
        )
    ]
    cos = _FakeCoS(patterns)
    engine = _make_engine(tmp_path, cos)
    _seed_project_with_episode(engine, "alpha")

    pre_count = engine.memory.count("cfo")
    result = engine.distill(dry_run=True)
    post_count = engine.memory.count("cfo")

    assert result["dry_run"] is True
    assert pre_count == post_count

    audit = engine.db.execute(
        "SELECT event_type, detail FROM audit_log "
        "WHERE event_type = 'learning.distillation_run'"
    ).fetchall()
    assert len(audit) == 1
    detail = json.loads(audit[0]["detail"])
    assert detail["dry_run"] is True
    assert detail["patterns_out"] == 1


def test_distill_audit_event_carries_run_id(tmp_path):
    patterns = [
        DistilledPattern(
            target_agent_role="ceo",
            pattern_key="player-prefs",
            pattern_summary="Player accepts revenue projects citing Discord.",
            confidence=0.8,
            evidence_episode_ids=["alpha"],
        )
    ]
    cos = _FakeCoS(patterns)
    engine = _make_engine(tmp_path, cos)
    _seed_project_with_episode(engine, "alpha")
    engine.distill()

    audit = engine.db.execute(
        "SELECT event_type, run_id, detail FROM audit_log "
        "WHERE event_type = 'learning.distillation_run'"
    ).fetchone()
    assert audit is not None
    assert audit["run_id"] is not None
    # The memory row also carries the same run_id.
    mem_row = engine.db.execute(
        "SELECT run_id FROM agent_memories "
        "WHERE agent_role = 'ceo' AND pattern_key = 'player-prefs'"
    ).fetchone()
    assert mem_row["run_id"] == audit["run_id"]
