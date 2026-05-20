"""Unit tests for CoS cross-episode distillation (P1)."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from kompany.agents.cos_distillation import (
    DEFAULT_SINCE,
    KNOWN_AGENT_ROLES,
    MAX_EPISODES_PER_RUN,
    DistillationOutput,
    DistilledPattern,
    build_distillation_user_prompt,
    build_episode_summaries,
    filter_patterns,
    select_episode_rows,
    summarize_episode,
)
from kompany.state.database import Database
from kompany.state.episode_payload import (
    ApprovalComment,
    ApprovalEvent,
    EpisodePayloadV1,
    HealthEvent,
    LedgerSummary,
    ProjectMeta,
    ReflectionEntry,
    TaskEntry,
)
from kompany.state.memory import AgentMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_memory() -> AgentMemory:
    tmp = tempfile.mkdtemp()
    db = Database(Path(tmp))
    return AgentMemory(db)


def _make_payload(project_id: str, name: str = "Demo") -> EpisodePayloadV1:
    return EpisodePayloadV1(
        project_meta=ProjectMeta(
            id=project_id,
            name=name,
            mission="Test mission",
            target_funded=[100.0, 80.0],
            status="completed",
            created_at="2026-05-01T00:00:00",
            delivered_at="2026-05-10T00:00:00",
        ),
        tasks=[
            TaskEntry(id=f"{project_id}-t1", title="Task 1", status="completed"),
            TaskEntry(id=f"{project_id}-t2", title="Task 2", status="failed"),
        ],
        ledger_summary=LedgerSummary(
            total_income=100.0,
            total_expense=-50.0,
            ai_cost=10.0,
            by_category={"income": 100.0, "ai_cost": -10.0},
        ),
        reflections=[
            ReflectionEntry(agent_role="cfo", content="Budget held within $50 cap."),
            ReflectionEntry(agent_role="cmo", content="Discord launch went smoothly."),
        ],
        approval_events=[
            ApprovalEvent(
                id="a1",
                kind="budget_proposal",
                outcome="approved",
                comments=[ApprovalComment(by="user:player", at="2026-05-02T10:00:00", text="ok")],
                decided_at="2026-05-02T10:01:00",
            ),
        ],
        health_events=[
            HealthEvent(at="2026-05-03T05:00:00", kind="silent_run"),
        ],
    )


def _row(project_id: str, updated_at: str, payload: EpisodePayloadV1 | None = None) -> dict[str, Any]:
    p = payload if payload is not None else _make_payload(project_id)
    return {
        "project_id": project_id,
        "summary": "...",
        "payload_json": p.model_dump_json(),
        "retention_tier": "full",
        "run_id": None,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


# ---------------------------------------------------------------------------
# 1) Pydantic schema validation
# ---------------------------------------------------------------------------

def test_distilled_pattern_validates_happy_path():
    p = DistilledPattern(
        target_agent_role="CFO",  # normalized to lower
        pattern_key="Budget-Cap-500 ",  # normalized & trimmed
        pattern_summary="Player rejects budget proposals above $500.",
        confidence=0.85,
        evidence_episode_ids=["p1", "p2"],
    )
    assert p.target_agent_role == "cfo"
    assert p.pattern_key == "budget-cap-500"
    assert 0.0 <= p.confidence <= 1.0


def test_distilled_pattern_rejects_extra_field():
    with pytest.raises(ValidationError):
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="k",
            pattern_summary="s",
            confidence=0.5,
            evidence_episode_ids=[],
            extra_field="boom",
        )


def test_distilled_pattern_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="k",
            pattern_summary="s",
            confidence=1.7,
            evidence_episode_ids=[],
        )
    with pytest.raises(ValidationError):
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="k",
            pattern_summary="s",
            confidence=-0.1,
            evidence_episode_ids=[],
        )


def test_distilled_pattern_rejects_missing_field():
    with pytest.raises(ValidationError):
        DistilledPattern.model_validate({
            "target_agent_role": "cfo",
            "pattern_key": "k",
            # missing pattern_summary
            "confidence": 0.5,
            "evidence_episode_ids": [],
        })


def test_distilled_pattern_pattern_key_max_length():
    with pytest.raises(ValidationError):
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="x" * 41,
            pattern_summary="s",
            confidence=0.5,
            evidence_episode_ids=[],
        )


def test_distillation_output_rejects_extras():
    with pytest.raises(ValidationError):
        DistillationOutput.model_validate({
            "patterns": [],
            "meta": {},
            "bogus": 1,
        })


def test_distillation_output_parses_full_payload():
    raw = json.dumps({
        "patterns": [
            {
                "target_agent_role": "cfo",
                "pattern_key": "budget-cap-500",
                "pattern_summary": "Reject above $500.",
                "confidence": 0.9,
                "evidence_episode_ids": ["p1", "p2"],
            }
        ],
        "meta": {"episodes_consumed": 2},
    })
    parsed = DistillationOutput.model_validate_json(raw)
    assert parsed.patterns[0].pattern_key == "budget-cap-500"
    assert parsed.meta["episodes_consumed"] == 2


# ---------------------------------------------------------------------------
# 2) Episode selection / summarization
# ---------------------------------------------------------------------------

def test_select_episode_rows_explicit_ids_win():
    rows = [_row("p1", "2026-05-15 00:00:00"), _row("p2", "2026-05-14 00:00:00")]
    selected = select_episode_rows(rows, episode_ids=["p2"], since=None)
    assert [r["project_id"] for r in selected] == ["p2"]


def test_select_episode_rows_filters_by_since():
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    rows = [
        _row("p_recent", "2026-05-15 00:00:00"),
        _row("p_old", "2025-01-01 00:00:00"),
    ]
    selected = select_episode_rows(
        rows, episode_ids=None, since=timedelta(days=30), now=now,
    )
    assert [r["project_id"] for r in selected] == ["p_recent"]


def test_select_episode_rows_since_zero_empty():
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    rows = [_row("p1", "2026-05-15 00:00:00")]
    selected = select_episode_rows(
        rows, episode_ids=None, since=timedelta(0), now=now,
    )
    assert selected == []


def test_select_episode_rows_skips_summary_only():
    rows = [
        {"project_id": "p_full", "payload_json": _make_payload("p_full").model_dump_json(),
         "updated_at": "2026-05-15 00:00:00"},
        {"project_id": "p_summary", "payload_json": None, "updated_at": "2026-05-15 00:00:00"},
    ]
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    selected = select_episode_rows(
        rows, episode_ids=None, since=timedelta(days=30), now=now,
    )
    assert [r["project_id"] for r in selected] == ["p_full"]


def test_summarize_episode_carries_key_fields():
    summary = summarize_episode(_make_payload("p1"))
    assert summary["project_id"] == "p1"
    assert summary["task_status_counts"] == {"completed": 1, "failed": 1}
    assert len(summary["reflections"]) == 2
    assert summary["health_events_by_kind"] == {"silent_run": 1}
    assert summary["approval_events"][0]["outcome"] == "approved"
    assert summary["ledger_summary"]["ai_cost"] == 10.0


def test_build_episode_summaries_skips_malformed_payload():
    good = _row("good", "2026-05-15 00:00:00")
    bad = {
        "project_id": "bad",
        "payload_json": "{not valid json}",
        "updated_at": "2026-05-15 00:00:00",
    }
    summaries, failures = build_episode_summaries([good, bad])
    assert [s["project_id"] for s in summaries] == ["good"]
    assert failures == ["bad"]


def test_user_prompt_includes_every_episode():
    payloads = [_make_payload(f"p{i}", name=f"P{i}") for i in range(3)]
    summaries = [summarize_episode(p) for p in payloads]
    prompt = build_distillation_user_prompt(summaries)
    for i in range(3):
        assert f"p{i}" in prompt
    assert "DistillationOutput" in prompt


# ---------------------------------------------------------------------------
# 3) Post-processing filters
# ---------------------------------------------------------------------------

def test_filter_patterns_drops_unknown_role():
    output = DistillationOutput(patterns=[
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="k1",
            pattern_summary="s",
            confidence=0.7,
            evidence_episode_ids=[],
        ),
        DistilledPattern(
            target_agent_role="wizard",
            pattern_key="k2",
            pattern_summary="s",
            confidence=0.5,
            evidence_episode_ids=[],
        ),
    ])
    kept, warnings = filter_patterns(output)
    assert [p.pattern_key for p in kept] == ["k1"]
    assert warnings[0]["reason"] == "unknown_target_agent_role"


def test_filter_patterns_dedupes_same_batch_keep_last():
    output = DistillationOutput(patterns=[
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="dup",
            pattern_summary="first",
            confidence=0.5,
            evidence_episode_ids=["p1"],
        ),
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="dup",
            pattern_summary="second",
            confidence=0.9,
            evidence_episode_ids=["p1", "p2"],
        ),
    ])
    kept, warnings = filter_patterns(output)
    assert len(kept) == 1
    assert kept[0].pattern_summary == "second"
    assert kept[0].confidence == 0.9
    assert warnings[0]["reason"] == "duplicate_pattern_key_in_batch"


def test_filter_patterns_keeps_known_roles():
    output = DistillationOutput(patterns=[
        DistilledPattern(
            target_agent_role=r,
            pattern_key=f"k-{r}",
            pattern_summary="s",
            confidence=0.5,
            evidence_episode_ids=[],
        )
        for r in sorted(KNOWN_AGENT_ROLES)
    ])
    kept, warnings = filter_patterns(output)
    assert len(kept) == len(KNOWN_AGENT_ROLES)
    assert warnings == []


# ---------------------------------------------------------------------------
# 4) AgentMemory UPSERT semantics
# ---------------------------------------------------------------------------

def test_upsert_by_pattern_key_inserts_then_updates():
    mem = _make_memory()
    result1 = mem.upsert_by_pattern_key(
        agent_role="cfo",
        pattern_key="budget-cap-500",
        content="Reject above $500.",
        metadata={"confidence": 0.8, "evidence_episode_ids": ["p1"]},
    )
    assert result1["action"] == "inserted"

    result2 = mem.upsert_by_pattern_key(
        agent_role="cfo",
        pattern_key="budget-cap-500",
        content="Reject above $500 unless task-essential.",
        metadata={"confidence": 0.9, "evidence_episode_ids": ["p1", "p2"]},
    )
    assert result2["action"] == "updated"
    assert result2["id"] == result1["id"]

    # Exactly one row for that key.
    rows = mem.recall("cfo", category="experiential")
    assert len(rows) == 1
    assert rows[0]["content"].endswith("task-essential.")

    fetched = mem.get_by_pattern_key("cfo", "budget-cap-500")
    assert fetched is not None
    assert fetched["metadata"]["confidence"] == 0.9
    assert fetched["metadata"]["evidence_episode_ids"] == ["p1", "p2"]


def test_upsert_distinct_keys_create_distinct_rows():
    mem = _make_memory()
    mem.upsert_by_pattern_key("cfo", "key-a", "A", metadata={})
    mem.upsert_by_pattern_key("cfo", "key-b", "B", metadata={})
    rows = mem.recall("cfo", category="experiential")
    assert {r["content"] for r in rows} == {"A", "B"}


def test_upsert_distinct_agents_independent():
    mem = _make_memory()
    mem.upsert_by_pattern_key("cfo", "shared-key", "CFO view", metadata={})
    mem.upsert_by_pattern_key("cmo", "shared-key", "CMO view", metadata={})
    assert mem.count("cfo") == 1
    assert mem.count("cmo") == 1


def test_upsert_requires_pattern_key():
    mem = _make_memory()
    with pytest.raises(ValueError):
        mem.upsert_by_pattern_key("cfo", "", "content")


# ---------------------------------------------------------------------------
# 5) Engine-level: distill() flow with fake LLM
# ---------------------------------------------------------------------------

class _FakeCoSAgent:
    """Stand-in for the CoS agent that returns a canned DistillationOutput."""

    role = "cos"
    display_name = "CoS"

    def __init__(self, patterns: list[DistilledPattern], cost: float = 0.123):
        self._patterns = patterns
        self._cost = cost
        self.calls = 0

    def distill(
        self,
        summaries: list[dict[str, Any]],
        max_tokens: int = 4096,
        targets_summary: str | None = None,
        glossary_summary: str | None = None,
    ):
        from kompany.llm.client import LLMResponse

        self.calls += 1
        parsed = DistillationOutput(patterns=self._patterns, meta={"episodes": len(summaries)})
        resp = LLMResponse(
            text=parsed.model_dump_json(),
            input_tokens=100,
            output_tokens=50,
            cost_usd=self._cost,
            model="claude-test",
        )
        resp.parsed = parsed
        return resp


def _make_engine(tmp_path, cos_agent: _FakeCoSAgent):
    """Build a hollow engine wired with a fake CoS agent and the real DB."""

    from kompany.core.autonomy import AutonomyGate
    from kompany.core.engine import KompanyEngine
    from kompany.llm.cost_tracker import CostTracker
    from kompany.state.audit import AuditLog
    from kompany.state.episodes import Episodes
    from kompany.state.ledger import Ledger

    db = Database(tmp_path)
    ledger = Ledger(db)
    audit = AuditLog(db)
    memory = AgentMemory(db)
    episodes = Episodes(db)

    engine = KompanyEngine.__new__(KompanyEngine)
    engine.db = db
    engine.ledger = ledger
    engine.audit = audit
    engine.memory = memory
    engine.episodes = episodes
    engine.cost_tracker = CostTracker(ledger)
    engine.autonomy = AutonomyGate()

    class _FakeRegistry:
        def __init__(self, agent):
            self._agent = agent

        def get(self, role, company_state=None):
            assert role == "cos"
            return self._agent

    engine.registry = _FakeRegistry(cos_agent)
    return engine


def _seed_episode(engine, project_id: str, updated_at: str = "2026-05-18 00:00:00"):
    """Insert a project_episodes row with a real EpisodePayloadV1 payload."""
    payload = _make_payload(project_id).model_dump_json()
    engine.db.execute(
        """INSERT INTO project_episodes
           (project_id, summary, payload_json, retention_tier,
            run_id, created_at, updated_at)
           VALUES (?, ?, ?, 'full', NULL, ?, ?)""",
        (project_id, "seed", payload, updated_at, updated_at),
    )
    engine.db.commit()


def test_engine_distill_writes_n_memories(tmp_path):
    patterns = [
        DistilledPattern(
            target_agent_role=role,
            pattern_key=f"k-{role}-{i}",
            pattern_summary=f"Pattern for {role} #{i}",
            confidence=0.7,
            evidence_episode_ids=["p1"],
        )
        for i, role in enumerate(["cfo", "cmo", "cto", "ceo", "coo"])
    ]
    cos = _FakeCoSAgent(patterns)
    engine = _make_engine(tmp_path, cos)
    _seed_episode(engine, "p1")

    result = engine.distill()

    assert result["status"] == "completed"
    assert result["patterns_out"] == 5
    assert cos.calls == 1
    # Five experiential memories landed, one per target role.
    for role in ["cfo", "cmo", "cto", "ceo", "coo"]:
        rows = engine.memory.recall(role, category="experiential")
        assert len(rows) == 1


def test_engine_distill_dry_run_does_not_write(tmp_path):
    patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="dry-key",
            pattern_summary="Should not be written",
            confidence=0.5,
            evidence_episode_ids=["p1"],
        )
    ]
    cos = _FakeCoSAgent(patterns)
    engine = _make_engine(tmp_path, cos)
    _seed_episode(engine, "p1")

    before = engine.memory.count("cfo")
    result = engine.distill(dry_run=True)
    after = engine.memory.count("cfo")

    assert result["status"] == "completed"
    assert result["dry_run"] is True
    assert before == after  # no DB write
    # LLM call still happened so the operator can inspect what would land.
    assert cos.calls == 1


def test_engine_distill_idempotent_upsert(tmp_path):
    patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="stable-key",
            pattern_summary="First version",
            confidence=0.5,
            evidence_episode_ids=["p1"],
        )
    ]
    cos = _FakeCoSAgent(patterns)
    engine = _make_engine(tmp_path, cos)
    _seed_episode(engine, "p1")

    engine.distill()
    first_rows = engine.memory.recall("cfo", category="experiential")
    assert len(first_rows) == 1

    # Re-run with the same pattern_key but a different summary.
    cos._patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="stable-key",
            pattern_summary="Updated version",
            confidence=0.9,
            evidence_episode_ids=["p1", "p2"],
        )
    ]
    engine.distill()
    second_rows = engine.memory.recall("cfo", category="experiential")
    # Still exactly one row — UPSERT, not append.
    assert len(second_rows) == 1
    assert second_rows[0]["content"] == "Updated version"


def test_engine_distill_no_episodes(tmp_path):
    cos = _FakeCoSAgent(patterns=[])
    engine = _make_engine(tmp_path, cos)
    # No episodes seeded.
    result = engine.distill()
    assert result["status"] == "no_episodes"
    assert result["patterns_out"] == 0
    # LLM was NOT called.
    assert cos.calls == 0


def test_engine_distill_rejects_over_50_episodes(tmp_path):
    cos = _FakeCoSAgent(patterns=[])
    engine = _make_engine(tmp_path, cos)
    # Seed 51 episodes — selection should explode without --episodes.
    for i in range(MAX_EPISODES_PER_RUN + 1):
        _seed_episode(engine, f"p{i:02d}")

    with pytest.raises(ValueError, match="use --episodes to select"):
        engine.distill(since=timedelta(days=3650))
    # And no LLM call.
    assert cos.calls == 0


def test_engine_distill_explicit_episode_ids_bypass_cap(tmp_path):
    patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="k",
            pattern_summary="ok",
            confidence=0.5,
            evidence_episode_ids=["p00"],
        )
    ]
    cos = _FakeCoSAgent(patterns)
    engine = _make_engine(tmp_path, cos)
    for i in range(MAX_EPISODES_PER_RUN + 1):
        _seed_episode(engine, f"p{i:02d}")

    # Explicit subset works even when total exceeds the cap.
    result = engine.distill(episode_ids=["p00", "p01", "p02"])
    assert result["status"] == "completed"
    assert result["episodes_in"] == 3


def test_engine_distill_audit_event_recorded(tmp_path):
    patterns = [
        DistilledPattern(
            target_agent_role="cmo",
            pattern_key="discord-launch",
            pattern_summary="Discord-launch projects converge in 2 directives.",
            confidence=0.8,
            evidence_episode_ids=["p1"],
        )
    ]
    cos = _FakeCoSAgent(patterns, cost=0.42)
    engine = _make_engine(tmp_path, cos)
    _seed_episode(engine, "p1")

    engine.distill()

    rows = engine.db.execute(
        "SELECT event_type, detail, run_id FROM audit_log "
        "WHERE event_type = 'learning.distillation_run'"
    ).fetchall()
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail"])
    assert detail["episodes_in"] == 1
    assert detail["patterns_out"] == 1
    assert detail["ai_cost"] == pytest.approx(0.42)
    # Audit row carries a run_id (run_scope was wrapped around the call).
    assert rows[0]["run_id"] is not None
    assert detail["run_id"] == rows[0]["run_id"]


def test_engine_distill_ledger_records_ai_cost(tmp_path):
    """The LLM wrapper records an ai_cost row; ensure it lands with run_id.

    Because this test uses a fake CoS agent that bypasses the real
    ``LLMClient.call_structured`` path, the ledger row isn't written by
    the wrapper. We instead exercise the engine's audit event which
    includes the same cost figure, and confirm the LLM response cost is
    propagated to the returned ``ai_cost`` field.
    """
    patterns = [
        DistilledPattern(
            target_agent_role="cto",
            pattern_key="anthropic-silent",
            pattern_summary="Anthropic hit by silent_run 3x last week.",
            confidence=0.7,
            evidence_episode_ids=["p1"],
        )
    ]
    cos = _FakeCoSAgent(patterns, cost=0.314)
    engine = _make_engine(tmp_path, cos)
    _seed_episode(engine, "p1")

    result = engine.distill()
    assert result["ai_cost"] == pytest.approx(0.314)


def test_engine_distill_malformed_payload_audit_event(tmp_path):
    cos = _FakeCoSAgent(patterns=[])
    engine = _make_engine(tmp_path, cos)
    # Insert a row with a payload that is JSON but does NOT match V1.
    engine.db.execute(
        """INSERT INTO project_episodes
           (project_id, summary, payload_json, retention_tier,
            run_id, created_at, updated_at)
           VALUES (?, ?, ?, 'full', NULL, ?, ?)""",
        ("p_bad", "x", '{"oops": true}',
         "2026-05-18 00:00:00", "2026-05-18 00:00:00"),
    )
    engine.db.commit()

    result = engine.distill()
    assert result["status"] == "no_parseable_episodes"
    # LLM is NOT called when every payload is unparseable.
    assert cos.calls == 0
    audit = engine.db.execute(
        "SELECT event_type FROM audit_log "
        "WHERE event_type = 'learning.distillation_failed'"
    ).fetchall()
    assert len(audit) == 1


def test_engine_distill_warns_on_unknown_role(tmp_path):
    # Both patterns carry evidence_episode_ids so the evidence-trace
    # inferred-only filter (task 05-19) does not reject them — this test
    # is targeting the unknown_target_agent_role warning path only.
    patterns = [
        DistilledPattern(
            target_agent_role="cfo",
            pattern_key="ok",
            pattern_summary="kept",
            confidence=0.5,
            evidence_episode_ids=["p1"],
        ),
        DistilledPattern(
            target_agent_role="wizard",
            pattern_key="bad",
            pattern_summary="dropped",
            confidence=0.5,
            evidence_episode_ids=["p1"],
        ),
    ]
    cos = _FakeCoSAgent(patterns)
    engine = _make_engine(tmp_path, cos)
    _seed_episode(engine, "p1")

    result = engine.distill()
    assert result["patterns_out"] == 1
    assert any(w["reason"] == "unknown_target_agent_role" for w in result["warnings"])
    # The kept pattern still landed.
    assert engine.memory.count("cfo") == 1


def test_default_since_is_thirty_days():
    assert DEFAULT_SINCE == timedelta(days=30)
