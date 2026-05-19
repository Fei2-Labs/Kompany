"""Schema-validation tests for ``EpisodePayloadV1``.

Locks the frozen contract documented in
``docs/context/episode-payload-schema.md`` so the schema cannot drift
without a deliberate breaking change. The two reference examples
(minimal + full) live in this file and are kept aligned with the doc.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from kompany.core.run_context import RUN_ID_PATTERN as CORE_RUN_ID_PATTERN
from kompany.core.run_context import new_run_id
from kompany.state.episode_payload import (
    RUN_ID_PATTERN,
    EpisodePayloadV1,
)


# ---------------------------------------------------------------------------
# Reference examples (kept aligned with docs/context/episode-payload-schema.md)
# ---------------------------------------------------------------------------


MIN_EXAMPLE: dict = {
    "schema_version": "1.0",
    "run_ids": [],
    "project_meta": {
        "id": "proj_a1b2",
        "name": "Bootstrap landing page",
        "mission": None,
        "target_funded": [],
        "status": "delivered",
        "created_at": "2026-05-18T09:00:00Z",
        "delivered_at": "2026-05-18T11:30:00Z",
    },
    "tasks": [],
    "ledger_summary": {
        "total_income": 0.0,
        "total_expense": 0.0,
        "ai_cost": 0.0,
        "by_category": {},
        "by_agent": {},
    },
    "decisions": [],
    "debate_ids": [],
    "audit_events": [],
    "reflections": [],
    "approval_events": [],
    "health_events": [],
    "ext": {},
}


FULL_EXAMPLE: dict = {
    "schema_version": "1.0",
    "run_ids": [
        "r_01HXX0000000000000000000AB",
        "r_01HXY0000000000000000000CD",
    ],
    "project_meta": {
        "id": "proj_a1b2",
        "name": "Ship invoice export",
        "mission": "Let solo founders bill their first client in <5 minutes.",
        "target_funded": [500.0, 500.0],
        "status": "delivered",
        "created_at": "2026-05-10T09:00:00Z",
        "delivered_at": "2026-05-18T18:42:00Z",
    },
    "tasks": [
        {
            "id": "task_001",
            "title": "Design PDF template",
            "assigned_agent": "cto",
            "status": "completed",
            "result": "shipped templates/invoice_v1.pdf",
            "run_id": "r_01HXX0000000000000000000AB",
            "lifecycle_events": [
                {"at": "2026-05-10T09:05:00Z", "state": "todo", "reason": None},
                {"at": "2026-05-10T09:30:00Z", "state": "in_progress", "reason": None},
                {"at": "2026-05-10T14:00:00Z", "state": "done", "reason": None},
            ],
        },
        {
            "id": "task_002",
            "title": "Wire export endpoint",
            "assigned_agent": "cto",
            "status": "completed",
            "result": "POST /invoices/{id}/export.pdf",
            "run_id": "r_01HXY0000000000000000000CD",
            "lifecycle_events": [
                {"at": "2026-05-15T10:00:00Z", "state": "todo", "reason": None},
                {
                    "at": "2026-05-16T09:00:00Z",
                    "state": "stranded_in_progress",
                    "reason": "watchdog: no tool calls in 18h",
                },
                {"at": "2026-05-17T16:00:00Z", "state": "done", "reason": None},
            ],
        },
    ],
    "ledger_summary": {
        "total_income": 500.0,
        "total_expense": 12.40,
        "ai_cost": 9.85,
        "by_category": {"income": 500.0, "ai_cost": 9.85, "expense": 2.55},
        "by_agent": {"cto": 7.20, "cos": 1.40, "ceo": 1.25},
    },
    "decisions": [
        {
            "id": "dec_001",
            "directive_id": "dir_abc",
            "run_id": "r_01HXX0000000000000000000AB",
            "summary": "Use ReportLab over wkhtmltopdf — no system deps.",
            "agents_involved": ["ceo", "cto"],
        }
    ],
    "debate_ids": ["debate_001"],
    "audit_events": [
        {
            "at": "2026-05-10T09:00:00Z",
            "type": "project.created",
            "run_id": "r_01HXX0000000000000000000AB",
            "detail": {"project_id": "proj_a1b2"},
        },
        {
            "at": "2026-05-18T18:42:00Z",
            "type": "project.delivered",
            "run_id": "r_01HXY0000000000000000000CD",
            "detail": {"funded": 500.0},
        },
    ],
    "reflections": [
        {
            "agent_role": "cto",
            "category": "reflection",
            "content": "wkhtmltopdf install kept failing — ReportLab was right.",
        },
        {
            "agent_role": "cos",
            "category": "reflection",
            "content": "Watchdog catch saved ~1 day of wasted re-run.",
        },
    ],
    "approval_events": [
        {
            "id": "appr_001",
            "run_id": "r_01HXY0000000000000000000CD",
            "kind": "expense_over_threshold",
            "outcome": "revision_requested",
            "comments": [
                {
                    "by": "user",
                    "at": "2026-05-15T20:00:00Z",
                    "text": "$50 looks high — cheaper model for draft?",
                },
                {
                    "by": "cto",
                    "at": "2026-05-15T20:05:00Z",
                    "text": "Switching draft to Haiku, final stays Sonnet.",
                },
                {"by": "user", "at": "2026-05-15T20:08:00Z", "text": "ok approved."},
            ],
            "decided_at": "2026-05-15T20:08:00Z",
        }
    ],
    "health_events": [
        {
            "at": "2026-05-16T09:00:00Z",
            "run_id": "r_01HXY0000000000000000000CD",
            "kind": "stranded_in_progress",
            "task_id": "task_002",
            "detail": {"silent_for_seconds": 64800},
        },
        {
            "at": "2026-05-17T10:00:00Z",
            "run_id": "r_01HXY0000000000000000000CD",
            "kind": "recovered",
            "task_id": "task_002",
            "detail": {"action": "re-prompted cto"},
        },
    ],
    "ext": {
        "approval-thread-and-rpg": {
            "player_xp_delta": 12,
            "player_preference_tags": ["cost-sensitive", "explicit-model-choice"],
        }
    },
}


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


def test_min_example_validates():
    """A just-delivered project with every reserved slot empty must validate."""
    payload = EpisodePayloadV1.model_validate(MIN_EXAMPLE)
    assert payload.schema_version == "1.0"
    assert payload.approval_events == []
    assert payload.health_events == []
    assert payload.ext == {}
    assert payload.run_ids == []


def test_full_example_validates():
    """The richly-populated example must validate every slot."""
    payload = EpisodePayloadV1.model_validate(FULL_EXAMPLE)
    assert len(payload.tasks) == 2
    assert len(payload.approval_events) == 1
    assert len(payload.health_events) == 2
    assert "approval-thread-and-rpg" in payload.ext


def test_run_id_pattern_matches_core_module():
    """Schema's run-id regex must equal ``run_context.RUN_ID_PATTERN`` verbatim."""
    assert RUN_ID_PATTERN == CORE_RUN_ID_PATTERN


def test_generated_run_id_validates_against_schema():
    """``new_run_id()`` output must satisfy every schema run_id field."""
    rid = new_run_id()
    assert re.match(RUN_ID_PATTERN, rid)
    data = dict(MIN_EXAMPLE)
    data["run_ids"] = [rid]
    payload = EpisodePayloadV1.model_validate(data)
    assert payload.run_ids == [rid]


def test_invalid_run_id_pattern_rejected_in_tasks():
    bad = {
        "id": "task_x",
        "title": "bad",
        "status": "todo",
        "run_id": "not-a-ulid",
        "lifecycle_events": [],
    }
    data = dict(MIN_EXAMPLE)
    data["tasks"] = [bad]
    with pytest.raises(ValidationError):
        EpisodePayloadV1.model_validate(data)


def test_invalid_run_id_pattern_rejected_in_decisions():
    bad = {
        "id": "dec_x",
        "run_id": "R_" + "0" * 26,  # wrong-case prefix
        "summary": "x",
    }
    data = dict(MIN_EXAMPLE)
    data["decisions"] = [bad]
    with pytest.raises(ValidationError):
        EpisodePayloadV1.model_validate(data)


def test_invalid_run_id_pattern_rejected_in_audit_events():
    bad = {
        "at": "2026-05-18T09:00:00Z",
        "type": "foo",
        "run_id": "r_" + "I" * 26,  # Crockford forbids I/L/O/U
    }
    data = dict(MIN_EXAMPLE)
    data["audit_events"] = [bad]
    with pytest.raises(ValidationError):
        EpisodePayloadV1.model_validate(data)


def test_run_id_none_accepted_everywhere():
    """``None`` is the documented default for writers outside a ``run_scope``."""
    data = dict(MIN_EXAMPLE)
    data["tasks"] = [
        {
            "id": "task_x",
            "title": "no run id",
            "status": "todo",
            "run_id": None,
            "lifecycle_events": [],
        }
    ]
    data["decisions"] = [{"id": "dec_x", "run_id": None, "summary": "x"}]
    data["audit_events"] = [{"at": "2026-05-18T09:00:00Z", "type": "foo", "run_id": None}]
    EpisodePayloadV1.model_validate(data)


def test_unknown_key_under_ext_is_accepted():
    """``ext`` is the namespaced escape hatch — unknown keys allowed."""
    data = dict(MIN_EXAMPLE)
    data["ext"] = {
        "approval-thread-and-rpg": {"xp": 5},
        "resilience-foundation": {"watchdog_fires": 3},
        "some-future-task": {"anything": [1, 2, 3]},
    }
    payload = EpisodePayloadV1.model_validate(data)
    assert payload.ext["some-future-task"]["anything"] == [1, 2, 3]


def test_unknown_top_level_key_rejected():
    """Schema discipline: ``extra='forbid'`` rejects unknown top-level keys.

    Forward-compatible additions must go under ``ext`` namespaced by task
    slug, not as new top-level keys.
    """
    data = dict(MIN_EXAMPLE)
    data["unexpected_top_level"] = {"oops": True}
    with pytest.raises(ValidationError):
        EpisodePayloadV1.model_validate(data)


def test_unknown_nested_key_rejected():
    """Nested models also forbid extras — catches typos in materializer code."""
    data = dict(MIN_EXAMPLE)
    bad_meta = dict(MIN_EXAMPLE["project_meta"])
    bad_meta["typo_field"] = "uh oh"
    data["project_meta"] = bad_meta
    with pytest.raises(ValidationError):
        EpisodePayloadV1.model_validate(data)


def test_schema_version_locked_to_v1():
    data = dict(MIN_EXAMPLE)
    data["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        EpisodePayloadV1.model_validate(data)


def test_reserved_slots_default_to_empty_lists():
    """Materializer can omit reserved slots — defaults are always lists, never None."""
    minimal_without_reserved = {
        "project_meta": MIN_EXAMPLE["project_meta"],
    }
    payload = EpisodePayloadV1.model_validate(minimal_without_reserved)
    assert payload.approval_events == []
    assert payload.health_events == []
    assert payload.run_ids == []
    assert payload.debate_ids == []
    assert payload.tasks == []
    assert payload.ext == {}
    # ledger_summary defaults to all-zero, not None
    assert payload.ledger_summary.total_income == 0.0
