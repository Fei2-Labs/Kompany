"""Tests for the project-episode materializer."""

from __future__ import annotations

import json

import pytest

from kompany.core.run_context import run_scope
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.debates import Debates
from kompany.state.episode_payload import EpisodePayloadV1
from kompany.state.episodes import Episodes
from kompany.state.journal import Journal
from kompany.state.ledger import Ledger
from kompany.state.memory import AgentMemory
from kompany.state.models import (
    Decision,
    LedgerCategory,
    Project,
    ProjectType,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects


def _scaffold(tmp_path, project_id: str = "p1") -> dict:
    db = Database(tmp_path)
    projects = Projects(db)
    ledger = Ledger(db)
    audit = AuditLog(db)
    memory = AgentMemory(db)
    journal = Journal(db)
    debates = Debates(db)
    episodes = Episodes(db)

    project = Project(
        id=project_id,
        name="Test Project",
        type=ProjectType.OPERATIONAL,
        assigned_agents=["coo", "cfo"],
        triggers_directive_id="dir-1",
    )
    projects.create(project)
    projects.create_task(Task(
        id="t1",
        project_id=project_id,
        title="Build it",
        assigned_agent="coo",
        status=TaskStatus.COMPLETED,
    ))
    projects.create_task(Task(
        id="t2",
        project_id=project_id,
        title="Ship it",
        assigned_agent="cfo",
        status=TaskStatus.FAILED,
    ))
    projects.update_task_status("t1", TaskStatus.COMPLETED, result={"ok": True})
    projects.update_task_status("t2", TaskStatus.FAILED)

    ledger.record(
        amount=100.0,
        description="invoice paid",
        category=LedgerCategory.INCOME,
        project_id=project_id,
    )
    ledger.record(
        amount=-25.0,
        description="API charge",
        category=LedgerCategory.AI_COST,
        project_id=project_id,
    )

    audit.record("project.created", "started", project_id=project_id)
    audit.record(
        "directive.completed",
        "delivered",
        detail={"status": "completed"},
        project_id=project_id,
    )
    # An event that should NOT make it into the curated episode list:
    audit.record("notification.emitted", "noise", project_id=project_id)

    memory.remember(
        agent_role="coo",
        content="kept shipping",
        category="reflection",
        context=f"project:{project_id}",
    )

    journal.log(Decision(
        id="dec-1",
        directive_id="dir-1",
        directive_type="operational",
        raw_input="run project",
        classification={},
        result={"status": "completed", "message": "shipped", "project_id": project_id},
        agents_involved=["ceo", "coo"],
        total_ai_cost=0.5,
    ))

    debate_id = debates.record(
        rounds=[[]],
        synthesis=None,
        decision=None,
        directive_id="dir-1",
        project_id=project_id,
    )

    return {
        "db": db,
        "projects": projects,
        "ledger": ledger,
        "audit": audit,
        "memory": memory,
        "journal": journal,
        "debates": debates,
        "episodes": episodes,
        "debate_id": debate_id,
        "project_id": project_id,
    }


def test_materialize_returns_validated_payload(tmp_path):
    s = _scaffold(tmp_path)
    payload = s["episodes"].materialize(s["project_id"])
    assert isinstance(payload, EpisodePayloadV1)
    assert payload.schema_version == "1.0"
    assert payload.project_meta.id == s["project_id"]
    assert len(payload.tasks) == 2
    statuses = {t.status for t in payload.tasks}
    assert statuses == {"completed", "failed"}
    assert payload.ledger_summary.total_income == 100.0
    assert payload.ledger_summary.ai_cost == 25.0  # absolute
    assert payload.ledger_summary.by_category["income"] == 100.0
    assert payload.ledger_summary.by_category["ai_cost"] == -25.0
    assert s["debate_id"] in payload.debate_ids
    # Only curated event types should be present.
    event_types = {e.type for e in payload.audit_events}
    assert "project.created" in event_types
    assert "directive.completed" in event_types
    assert "notification.emitted" not in event_types
    assert len(payload.reflections) == 1
    assert payload.reflections[0].agent_role == "coo"
    decision_ids = {d.id for d in payload.decisions}
    assert "dec-1" in decision_ids


def test_materialize_missing_project_raises(tmp_path):
    s = _scaffold(tmp_path)
    with pytest.raises(LookupError):
        s["episodes"].materialize("does-not-exist")


def test_record_or_update_is_idempotent(tmp_path):
    s = _scaffold(tmp_path)
    first = s["episodes"].record_or_update(s["project_id"])
    second = s["episodes"].record_or_update(s["project_id"])

    assert first["project_id"] == second["project_id"]
    # ``created_at`` must not change on an idempotent rewrite.
    assert first["created_at"] == second["created_at"]
    # Payload validates against the v1 schema.
    parsed = EpisodePayloadV1.model_validate_json(second["payload_json"])
    assert parsed.project_meta.id == s["project_id"]

    rows = s["db"].execute(
        "SELECT COUNT(*) AS c FROM project_episodes WHERE project_id = ?",
        (s["project_id"],),
    ).fetchone()
    assert rows["c"] == 1


def test_record_or_update_writes_run_id_from_scope(tmp_path):
    s = _scaffold(tmp_path)
    with run_scope() as rid:
        row = s["episodes"].record_or_update(s["project_id"])
    assert row["run_id"] == rid


def test_get_and_list(tmp_path):
    s = _scaffold(tmp_path)
    s["episodes"].record_or_update(s["project_id"])

    row = s["episodes"].get(s["project_id"])
    assert row is not None
    assert row["retention_tier"] == "full"

    rows_all = s["episodes"].list()
    assert len(rows_all) == 1

    rows_full = s["episodes"].list(retention_tier="full")
    assert len(rows_full) == 1

    rows_summary = s["episodes"].list(retention_tier="summary")
    assert rows_summary == []


def _seed_extra_project(s, idx: int) -> str:
    pid = f"extra-{idx}"
    s["projects"].create(Project(
        id=pid,
        name=f"Extra {idx}",
        type=ProjectType.OPERATIONAL,
        assigned_agents=["coo"],
    ))
    return pid


def test_trim_to_retention_window_demotes_oldest(tmp_path):
    s = _scaffold(tmp_path)
    s["episodes"].record_or_update(s["project_id"])

    extra_ids = []
    for i in range(3):
        pid = _seed_extra_project(s, i)
        s["episodes"].record_or_update(pid)
        extra_ids.append(pid)

    # Keep most recent 2, drop the rest to ``summary``.
    trimmed = s["episodes"].trim_to_retention_window(2)
    # Oldest = the seed scaffold project + first extra.
    trimmed_ids = {entry["project_id"] for entry in trimmed}
    # The two most recently updated are extra-1 and extra-2; trim should
    # demote the two oldest: scaffold project + extra-0.
    assert len(trimmed) == 2
    assert s["project_id"] in trimmed_ids
    assert extra_ids[0] in trimmed_ids

    # Demoted rows have NULL payload_json + summary tier, summary preserved.
    demoted = s["episodes"].get(s["project_id"])
    assert demoted["retention_tier"] == "summary"
    assert demoted["payload_json"] is None
    assert demoted["summary"]  # non-empty


def test_trim_when_under_threshold_is_noop(tmp_path):
    s = _scaffold(tmp_path)
    s["episodes"].record_or_update(s["project_id"])
    assert s["episodes"].trim_to_retention_window(50) == []
    row = s["episodes"].get(s["project_id"])
    assert row["retention_tier"] == "full"


def test_trim_to_zero_demotes_all(tmp_path):
    s = _scaffold(tmp_path)
    s["episodes"].record_or_update(s["project_id"])
    trimmed = s["episodes"].trim_to_retention_window(0)
    assert len(trimmed) == 1
    row = s["episodes"].get(s["project_id"])
    assert row["retention_tier"] == "summary"


def test_trim_negative_raises(tmp_path):
    s = _scaffold(tmp_path)
    with pytest.raises(ValueError):
        s["episodes"].trim_to_retention_window(-1)


def test_rebuild_after_mutation_picks_up_new_rows(tmp_path):
    s = _scaffold(tmp_path)
    s["episodes"].record_or_update(s["project_id"])
    first = s["episodes"].get(s["project_id"])
    first_payload = EpisodePayloadV1.model_validate_json(first["payload_json"])
    assert len(first_payload.tasks) == 2

    # Add a new task after first materialization.
    s["projects"].create_task(Task(
        id="t3",
        project_id=s["project_id"],
        title="follow up",
        assigned_agent="coo",
        status=TaskStatus.PENDING,
    ))

    # Re-materialize: the new task must appear.
    s["episodes"].record_or_update(s["project_id"])
    rebuilt = s["episodes"].get(s["project_id"])
    rebuilt_payload = EpisodePayloadV1.model_validate_json(rebuilt["payload_json"])
    assert len(rebuilt_payload.tasks) == 3
    assert rebuilt["created_at"] == first["created_at"]


def test_decisions_with_debate_id_in_result_collected(tmp_path):
    s = _scaffold(tmp_path)
    # Add a decision whose result mentions a separate debate_id that isn't
    # bound to the project via the debates table.
    s["journal"].log(Decision(
        id="dec-2",
        directive_id="dir-other",
        directive_type="strategic",
        raw_input="strategic q",
        classification={},
        result={
            "status": "completed",
            "message": f"context project {s['project_id']}",
            "debate_id": "freefloat-abc",
        },
        agents_involved=["ceo"],
        total_ai_cost=0.0,
    ))
    payload = s["episodes"].materialize(s["project_id"])
    assert "freefloat-abc" in payload.debate_ids
    decision_ids = {d.id for d in payload.decisions}
    assert "dec-2" in decision_ids


def test_empty_project_materializes_without_error(tmp_path):
    db = Database(tmp_path)
    projects = Projects(db)
    episodes = Episodes(db)

    project = Project(
        id="empty",
        name="Empty Project",
        type=ProjectType.OPERATIONAL,
        assigned_agents=[],
    )
    projects.create(project)
    payload = episodes.materialize("empty")
    assert payload.tasks == []
    assert payload.decisions == []
    assert payload.debate_ids == []
    assert payload.audit_events == []
    assert payload.reflections == []
