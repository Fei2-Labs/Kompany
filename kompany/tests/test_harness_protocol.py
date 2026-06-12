"""Tests for the HarnessRunner protocol and its pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kompany.core.harness import (
    ClaudeCodeRunner,
    HandoffBrief,
    HarnessCaps,
    HarnessEvent,
    HarnessResult,
    HarnessRunner,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_caps_defaults():
    caps = HarnessCaps()
    assert caps.budget_cap_usd is None
    assert caps.max_turns == 50


def test_caps_explicit_values():
    caps = HarnessCaps(budget_cap_usd=2.5, max_turns=10)
    assert caps.budget_cap_usd == 2.5
    assert caps.max_turns == 10


def test_event_valid_kinds():
    for kind in (
        "session_started",
        "turn",
        "tool_use",
        "text",
        "cost_delta",
        "permission_denied",
        "completed",
        "failed",
    ):
        event = HarnessEvent(kind=kind)
        assert event.kind == kind
        assert event.payload == {}
        assert event.raw is None


def test_event_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        HarnessEvent(kind="telepathy")


def test_event_payload_and_raw_passthrough():
    raw = {"type": "result", "subtype": "success"}
    event = HarnessEvent(kind="completed", payload={"total_cost_usd": 0.1}, raw=raw)
    assert event.payload["total_cost_usd"] == 0.1
    assert event.raw == raw


def test_result_defaults():
    result = HarnessResult()
    assert result.final_text == ""
    assert result.session_id is None
    assert result.files_changed == []
    assert result.cost_usd is None
    assert result.tokens_in == 0
    assert result.tokens_out == 0
    assert result.permission_denials == []
    assert result.exit_status == "success"
    assert result.error is None


def test_handoff_brief_requires_core_fields():
    with pytest.raises(ValidationError):
        HandoffBrief(done="d", stuck="s")  # missing next_steps + vehicle
    brief = HandoffBrief(
        done="d", stuck="s", next_steps="n", session_id=None, vehicle="claude_code"
    )
    assert brief.vehicle == "claude_code"


# ---------------------------------------------------------------------------
# Protocol conformance (runtime_checkable)
# ---------------------------------------------------------------------------


def test_claude_code_runner_satisfies_protocol():
    assert isinstance(ClaudeCodeRunner(), HarnessRunner)


def test_unrelated_object_fails_protocol():
    class NotARunner:
        vehicle_name = "nope"

    assert not isinstance(NotARunner(), HarnessRunner)


def test_minimal_duck_typed_runner_satisfies_protocol():
    class DuckRunner:
        @property
        def vehicle_name(self) -> str:
            return "duck"

        def start(self, prompt, workspace, caps, on_event=None):
            return HarnessResult()

        def resume(self, session_id, prompt, workspace, caps, on_event=None):
            return HarnessResult(session_id=session_id)

        def handoff(self, result):
            return HandoffBrief(
                done="", stuck="", next_steps="", vehicle="duck"
            )

    assert isinstance(DuckRunner(), HarnessRunner)
