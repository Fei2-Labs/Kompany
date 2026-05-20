"""Tests for the evidence-trace distillation guard.

Task 05-19 evidence-traced-debate, PR3. Verifies that
:func:`kompany.agents.cos_distillation.filter_inferred_only_patterns`
drops patterns with no ``evidence_episode_ids`` and that the engine
emits one ``distillation.claim_rejected_inferred_only`` audit event per
rejected pattern.

Long-term ``agent_memories`` pollution is irreversible — this filter is
the only hard block in the evidence-trace plan, so it gets its own
focused test module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kompany.agents.cos_distillation import (
    DistillationOutput,
    DistilledPattern,
    filter_inferred_only_patterns,
)


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------

def _pattern(
    pattern_key: str,
    *,
    evidence: list[str],
    role: str = "cfo",
    summary: str = "stable pattern",
) -> DistilledPattern:
    return DistilledPattern(
        target_agent_role=role,
        pattern_key=pattern_key,
        pattern_summary=summary,
        confidence=0.7,
        evidence_episode_ids=evidence,
    )


def test_filter_drops_pattern_with_no_evidence_ids() -> None:
    """A pattern with no episode ids is inferred-only and must be dropped."""
    sourced, rejected = filter_inferred_only_patterns([
        _pattern("hallucinated-fact", evidence=[]),
    ])
    assert sourced == []
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "inferred_only"
    assert rejected[0]["pattern_key"] == "hallucinated-fact"


def test_filter_keeps_pattern_with_at_least_one_episode_id() -> None:
    p = _pattern("real-fact", evidence=["proj-1"])
    sourced, rejected = filter_inferred_only_patterns([p])
    assert sourced == [p]
    assert rejected == []


def test_filter_partitions_batch() -> None:
    """A mixed batch: sourced patterns pass; inferred-only ones are rejected
    individually — not all-or-nothing."""
    good_a = _pattern("real-a", evidence=["proj-1", "proj-2"])
    bad = _pattern("hallucination", evidence=[], summary="we'll grow 10x")
    good_b = _pattern("real-b", evidence=["proj-3"], role="cos")

    sourced, rejected = filter_inferred_only_patterns([good_a, bad, good_b])

    assert sourced == [good_a, good_b]
    assert len(rejected) == 1
    assert rejected[0]["pattern_key"] == "hallucination"
    # 200-char snippet of the claim text is in the rejection record so the
    # audit event can carry it without a re-read.
    assert "10x" in rejected[0]["claim_text"]


def test_rejection_record_includes_target_role() -> None:
    """Audit event consumers need the role to attribute the rejection."""
    sourced, rejected = filter_inferred_only_patterns([
        _pattern("p", evidence=[], role="cto"),
    ])
    assert rejected[0]["target_agent_role"] == "cto"


def test_filter_empty_input_is_empty_output() -> None:
    sourced, rejected = filter_inferred_only_patterns([])
    assert sourced == []
    assert rejected == []


# ---------------------------------------------------------------------------
# Engine-level wiring: audit event fires
# ---------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    from kompany.core.engine import KompanyEngine

    return KompanyEngine()


class _FakeLLMResponse:
    def __init__(self, parsed: DistillationOutput) -> None:
        self.parsed = parsed
        self.cost_usd = 0.0
        self.text = ""


def _make_distill_inputs(engine: Any) -> None:
    """Seed one episode row so _distill_inner has something to summarize."""
    from kompany.state.episode_payload import (
        EpisodePayloadV1,
        LedgerSummary,
        ProjectMeta,
    )

    meta = ProjectMeta(
        id="proj-seed",
        name="seed-project",
        status="completed",
        mission="grow",
        created_at="2026-05-19T00:00:00+00:00",
    )
    payload = EpisodePayloadV1(
        project_meta=meta,
        ledger_summary=LedgerSummary(
            total_income=0.0, total_expense=0.0, ai_cost=0.0
        ),
    )
    engine.db.execute(
        "INSERT INTO project_episodes (project_id, summary, payload_json, updated_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (meta.id, "seed summary", payload.model_dump_json()),
    )


def test_engine_emits_rejection_audit_event(engine: Any) -> None:
    """When CoS produces an inferred-only pattern, _distill_inner emits
    one ``distillation.claim_rejected_inferred_only`` audit event AND
    does not write the pattern into ``agent_memories``."""
    _make_distill_inputs(engine)

    # Mock the CoS LLM call to return one sourced + one inferred-only.
    output = DistillationOutput(
        patterns=[
            DistilledPattern(
                target_agent_role="cfo",
                pattern_key="real-runway-discipline",
                pattern_summary="With cash < 30d burn, freeze marketing.",
                confidence=0.8,
                evidence_episode_ids=["proj-seed"],
            ),
            DistilledPattern(
                target_agent_role="cfo",
                pattern_key="hallucinated-ltv",
                pattern_summary="Customer LTV will average $1,200.",
                confidence=0.6,
                evidence_episode_ids=[],
            ),
        ],
    )

    cos_agent = engine.registry.get("cos")
    with patch.object(cos_agent, "distill", return_value=_FakeLLMResponse(output)):
        result = engine.distill(dry_run=False)

    # One pattern survived; one rejected.
    assert result["patterns_out"] == 1
    assert len(result["claims_rejected_inferred_only"]) == 1
    assert (
        result["claims_rejected_inferred_only"][0]["pattern_key"]
        == "hallucinated-ltv"
    )

    # Audit event landed exactly once with the right type.
    rows = engine.db.execute(
        "SELECT detail FROM audit_log "
        "WHERE event_type = 'distillation.claim_rejected_inferred_only'"
    ).fetchall()
    assert len(rows) == 1

    # And the inferred-only pattern is NOT in agent_memories.
    mem_rows = engine.db.execute(
        "SELECT pattern_key FROM agent_memories "
        "WHERE pattern_key = 'hallucinated-ltv'"
    ).fetchall()
    assert mem_rows == []

    # The sourced pattern IS written.
    sourced_rows = engine.db.execute(
        "SELECT pattern_key FROM agent_memories "
        "WHERE pattern_key = 'real-runway-discipline'"
    ).fetchall()
    assert len(sourced_rows) == 1
