"""Tests for CodexRunner — fake subprocess, canned `codex exec --json` lines.

Line shapes follow the research doc
``.trellis/tasks/06-11-harness-execution-leg/research/multi-backend-runners.md``
(codex 0.133.0 JSONL: thread.started / turn.* / item.*). No real ``codex``
calls, no spend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import kompany
from kompany.core.harness import runner_utils
from kompany.core.harness.codex_runner import (
    CACHED_INPUT_RATE,
    CodexRunner,
    estimate_turn_cost,
)
from kompany.core.harness.protocol import HarnessCaps
from kompany.llm.models import estimate_cost
from tests.harness_stubs import install_fake_popen

THREAD_ID = "0199a213-81ba-7632-bb24-3742a3cbabcd"
MODEL = "gpt-4.1"

USAGE = {
    "input_tokens": 24763,
    "cached_input_tokens": 24448,
    "output_tokens": 122,
    "reasoning_output_tokens": 0,
}


# ---------------------------------------------------------------------------
# Canned `--json` JSONL events (shapes from the research doc)
# ---------------------------------------------------------------------------


def thread_started() -> dict:
    return {"type": "thread.started", "thread_id": THREAD_ID}


def turn_started() -> dict:
    return {"type": "turn.started"}


def item_command() -> dict:
    return {
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "item_type": "command_execution",
            "command": "touch hello.txt",
            "exit_code": 0,
            "status": "completed",
        },
    }


def item_message(text: str = "Created hello.txt.") -> dict:
    return {
        "type": "item.completed",
        "item": {"id": "item_1", "item_type": "agent_message", "text": text},
    }


def turn_completed(usage: dict | None = None) -> dict:
    return {"type": "turn.completed", "usage": dict(usage or USAGE)}


def turn_failed(message: str = "stream disconnected") -> dict:
    return {"type": "turn.failed", "error": {"message": message}}


HAPPY_PATH = (
    thread_started(),
    turn_started(),
    item_command(),
    item_message(),
    turn_completed(),
)


@pytest.fixture
def ws(tmp_path) -> Path:
    """A git workspace — codex refuses non-git dirs (research pitfall 3)."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _start(monkeypatch, ws, events, returncode=0, caps=None):
    state = install_fake_popen(
        monkeypatch, runner_utils, events=events, returncode=returncode
    )
    collected = []
    runner = CodexRunner(model=MODEL)
    result = runner.start(
        "do the thing",
        ws,
        caps or HarnessCaps(max_turns=5),
        on_event=collected.append,
    )
    return result, state, collected


# ---------------------------------------------------------------------------
# Stream parsing
# ---------------------------------------------------------------------------


def test_thread_id_captured_as_session_id(monkeypatch, ws):
    result, _, events = _start(monkeypatch, ws, HAPPY_PATH)
    assert result.session_id == THREAD_ID
    assert events[0].kind == "session_started"
    assert events[0].payload["session_id"] == THREAD_ID


def test_event_normalization_order(monkeypatch, ws):
    _, _, events = _start(monkeypatch, ws, HAPPY_PATH)
    assert [e.kind for e in events] == [
        "session_started",
        "turn",
        "tool_use",
        "text",
        "cost_delta",
        "completed",
    ]
    assert events[2].payload["tool_name"] == "command_execution"
    assert events[2].payload["tool_input"] == {"command": "touch hello.txt"}
    assert events[3].payload["text"] == "Created hello.txt."
    assert events[4].payload["estimated"] is True  # tokens-only vehicle
    assert events[4].payload["usd"] == pytest.approx(
        estimate_turn_cost(MODEL, USAGE)
    )
    assert events[4].raw is not None  # passthrough fidelity (pitfall 12)


def test_result_cost_is_flagged_estimate(monkeypatch, ws):
    result, _, _ = _start(monkeypatch, ws, HAPPY_PATH)
    assert result.cost_is_estimate is True
    assert result.cost_usd == pytest.approx(estimate_turn_cost(MODEL, USAGE))
    assert result.tokens_in == 24763
    assert result.tokens_out == 122
    assert result.final_text == "Created hello.txt."
    assert result.exit_status == "success"


def test_estimate_bills_cached_input_at_discounted_rate():
    uncached = USAGE["input_tokens"] - USAGE["cached_input_tokens"]
    expected = estimate_cost(MODEL, uncached, USAGE["output_tokens"]) + (
        CACHED_INPUT_RATE * estimate_cost(MODEL, USAGE["cached_input_tokens"], 0)
    )
    assert estimate_turn_cost(MODEL, USAGE) == pytest.approx(expected)
    # Cached tokens must not be billed at the full input rate.
    assert estimate_turn_cost(MODEL, USAGE) < estimate_cost(
        MODEL, USAGE["input_tokens"], USAGE["output_tokens"]
    )


# ---------------------------------------------------------------------------
# External budget enforcement (estimated deltas feed the meter)
# ---------------------------------------------------------------------------


def test_budget_breach_kills_and_marks_budget_exceeded(monkeypatch, ws):
    per_turn = estimate_turn_cost(MODEL, USAGE)
    events = (
        thread_started(),
        turn_started(),
        turn_completed(),
        turn_started(),
        turn_completed(),  # second turn crosses the cap
        turn_started(),
        turn_completed(),  # must never be processed
    )
    result, state, collected = _start(
        monkeypatch,
        ws,
        events,
        caps=HarnessCaps(budget_cap_usd=per_turn * 1.5, max_turns=5),
    )
    assert state["proc"].terminated  # process group killed on breach
    assert result.exit_status == "budget_exceeded"
    # Meter total = the two processed estimated deltas, flagged estimate.
    assert result.cost_usd == pytest.approx(2 * per_turn)
    assert result.cost_is_estimate is True
    failed = [e for e in collected if e.kind == "failed"]
    assert failed and failed[-1].payload["subtype"] == "budget_exceeded"
    assert failed[-1].payload["estimated"] is True


def test_exact_cap_is_not_a_breach(monkeypatch, ws):
    per_turn = estimate_turn_cost(MODEL, USAGE)
    events = (thread_started(), turn_started(), turn_completed(), item_message())
    result, state, _ = _start(
        monkeypatch,
        ws,
        events,
        caps=HarnessCaps(budget_cap_usd=per_turn, max_turns=5),
    )
    assert not state["proc"].terminated
    assert result.exit_status == "success"


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_command_shape(monkeypatch, ws):
    _, state, _ = _start(monkeypatch, ws, HAPPY_PATH)
    cmd = state["cmd"]
    assert cmd[:2] == ["codex", "exec"]
    assert "resume" not in cmd
    assert "--json" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert cmd[cmd.index("--model") + 1] == MODEL
    assert cmd[-1] == "do the thing"
    assert state["kwargs"]["cwd"] == str(ws)
    assert state["kwargs"]["start_new_session"] is True


def test_resume_uses_exec_resume_form(monkeypatch, ws):
    state = install_fake_popen(monkeypatch, runner_utils, events=HAPPY_PATH)
    runner = CodexRunner(model=MODEL)
    result = runner.resume(
        THREAD_ID, "continue the work", ws, HarnessCaps(max_turns=5)
    )
    cmd = state["cmd"]
    assert cmd[:4] == ["codex", "exec", "resume", THREAD_ID]
    assert cmd[-1] == "continue the work"
    # `exec resume` rejects -s/--sandbox (0.133.0) — config override instead.
    assert "--sandbox" not in cmd
    assert cmd[cmd.index("-c") + 1] == 'sandbox_mode="workspace-write"'
    assert result.session_id == THREAD_ID


# ---------------------------------------------------------------------------
# Git workspace requirement
# ---------------------------------------------------------------------------


def test_refuses_non_git_workspace(monkeypatch, tmp_path):
    state = install_fake_popen(monkeypatch, runner_utils, events=HAPPY_PATH)
    runner = CodexRunner(model=MODEL)
    with pytest.raises(ValueError, match="git workspace"):
        runner.start("x", tmp_path, HarnessCaps(max_turns=5))
    assert "proc" not in state  # never spawned


# ---------------------------------------------------------------------------
# Env hygiene
# ---------------------------------------------------------------------------


def test_env_scrubs_claude_vars(monkeypatch, ws):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_FOO", "leak")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _, state, _ = _start(monkeypatch, ws, HAPPY_PATH)
    env = state["kwargs"]["env"]
    assert "CLAUDECODE" not in env
    assert "CLAUDE_FOO" not in env
    assert env["OPENAI_API_KEY"] == "sk-test"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_turn_failed_marks_run_failed(monkeypatch, ws):
    events = (thread_started(), turn_started(), turn_failed())
    result, _, collected = _start(monkeypatch, ws, events, returncode=1)
    assert result.exit_status == "error"
    assert result.error is not None and "stream disconnected" in result.error
    assert collected[-1].kind == "failed"


def test_resume_keeps_session_id_when_stream_dies_early(monkeypatch, ws):
    install_fake_popen(monkeypatch, runner_utils, events=(), returncode=1)
    runner = CodexRunner(model=MODEL)
    result = runner.resume(THREAD_ID, "continue", ws, HarnessCaps(max_turns=5))
    assert result.exit_status == "error"
    # The known id must survive an event-less crash — still resumable.
    assert result.session_id == THREAD_ID


# ---------------------------------------------------------------------------
# Constitution guard (shared safety module)
# ---------------------------------------------------------------------------


def test_refuses_workspace_inside_kompany_source(monkeypatch):
    install_fake_popen(monkeypatch, runner_utils, events=HAPPY_PATH)
    pkg_dir = Path(kompany.__file__).resolve().parent
    runner = CodexRunner(model=MODEL)
    with pytest.raises(ValueError, match="source"):
        runner.start("x", pkg_dir, HarnessCaps(max_turns=5))


def test_resume_also_guarded(monkeypatch):
    install_fake_popen(monkeypatch, runner_utils, events=HAPPY_PATH)
    pkg_dir = Path(kompany.__file__).resolve().parent
    runner = CodexRunner(model=MODEL)
    with pytest.raises(ValueError, match="source"):
        runner.resume(THREAD_ID, "x", pkg_dir, HarnessCaps(max_turns=5))


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------


def test_handoff_vehicle_and_budget_branch(monkeypatch, ws):
    events = (thread_started(), turn_started(), turn_completed())
    result, _, _ = _start(
        monkeypatch,
        ws,
        events,
        caps=HarnessCaps(budget_cap_usd=0.000001, max_turns=5),
    )
    runner = CodexRunner(model=MODEL)
    brief = runner.handoff(result)
    assert brief.vehicle == "codex"
    assert brief.session_id == THREAD_ID
    assert "Budget cap" in brief.next_steps
    assert "resume" in brief.next_steps
