"""Tests for OpencodeRunner — fake subprocess, canned `--format json` lines.

Line shapes follow the research doc
``.trellis/tasks/06-11-harness-execution-leg/research/multi-backend-runners.md``
(opencode 1.1.42; ``run.ts`` json emit + ``StepFinishPart`` from
``types.gen.ts``). No real ``opencode`` calls, no spend.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import kompany
from kompany.core.harness import runner_utils
from kompany.core.harness.opencode_runner import (
    RESTRICTIVE_PERMISSION,
    OpencodeRunner,
)
from kompany.core.harness.protocol import HarnessCaps
from tests.harness_stubs import install_fake_popen

SESSION_ID = "ses_4f7abc123def"
MODEL = "openai/gpt-5"


# ---------------------------------------------------------------------------
# Canned `--format json` events (shapes from the research doc)
# ---------------------------------------------------------------------------


def step_start() -> dict:
    return {
        "type": "step_start",
        "timestamp": 1760000000,
        "sessionID": SESSION_ID,
        "part": {"type": "step-start"},
    }


def tool_event() -> dict:
    return {
        "type": "tool_use",
        "timestamp": 1760000001,
        "sessionID": SESSION_ID,
        "part": {
            "type": "tool",
            "tool": "bash",
            "state": {
                "status": "completed",
                "input": {"command": "touch hello.txt"},
                "output": "",
            },
        },
    }


def text_event(text: str = "Created hello.txt.") -> dict:
    return {
        "type": "text",
        "timestamp": 1760000002,
        "sessionID": SESSION_ID,
        "part": {"type": "text", "text": text, "time": {"start": 1, "end": 2}},
    }


def step_finish(cost: float = 0.0123) -> dict:
    return {
        "type": "step_finish",
        "timestamp": 1760000003,
        "sessionID": SESSION_ID,
        "part": {
            "type": "step-finish",
            "reason": "stop",
            "cost": cost,
            "tokens": {
                "total": 154,
                "input": 100,
                "output": 50,
                "reasoning": 4,
                "cache": {"read": 0, "write": 0},
            },
        },
    }


def error_event() -> dict:
    return {
        "type": "error",
        "timestamp": 1760000004,
        "sessionID": SESSION_ID,
        "error": {"name": "ProviderAuthError", "message": "invalid api key"},
    }


HAPPY_PATH = (step_start(), tool_event(), text_event(), step_finish())


def _start(monkeypatch, tmp_path, events, returncode=0, caps=None):
    state = install_fake_popen(
        monkeypatch, runner_utils, events=events, returncode=returncode
    )
    collected = []
    runner = OpencodeRunner(model=MODEL)
    result = runner.start(
        "do the thing",
        tmp_path,
        caps or HarnessCaps(max_turns=5),
        on_event=collected.append,
    )
    return result, state, collected


# ---------------------------------------------------------------------------
# Stream parsing
# ---------------------------------------------------------------------------


def test_session_id_captured_from_first_event(monkeypatch, tmp_path):
    result, _, events = _start(monkeypatch, tmp_path, HAPPY_PATH)
    assert result.session_id == SESSION_ID
    assert events[0].kind == "session_started"
    assert events[0].payload["session_id"] == SESSION_ID


def test_event_normalization_order(monkeypatch, tmp_path):
    _, _, events = _start(monkeypatch, tmp_path, HAPPY_PATH)
    assert [e.kind for e in events] == [
        "session_started",
        "turn",
        "tool_use",
        "text",
        "cost_delta",
        "completed",
    ]
    assert events[2].payload["tool_name"] == "bash"
    assert events[2].payload["tool_input"] == {"command": "touch hello.txt"}
    assert events[3].payload["text"] == "Created hello.txt."
    assert events[4].payload["usd"] == pytest.approx(0.0123)
    assert events[4].payload["estimated"] is False
    assert events[4].raw is not None  # passthrough fidelity (pitfall 12)


def test_per_step_cost_summed_and_authoritative(monkeypatch, tmp_path):
    events = (
        step_start(),
        step_finish(cost=0.01),
        step_start(),
        text_event("Done."),
        step_finish(cost=0.02),
    )
    result, _, _ = _start(monkeypatch, tmp_path, events)
    assert result.cost_usd == pytest.approx(0.03)
    assert result.cost_is_estimate is False  # per-step reals, not estimates
    assert result.tokens_in == 200
    assert result.tokens_out == 100
    assert result.final_text == "Done."
    assert result.exit_status == "success"


# ---------------------------------------------------------------------------
# External budget enforcement
# ---------------------------------------------------------------------------


def test_budget_breach_kills_and_marks_budget_exceeded(monkeypatch, tmp_path):
    events = (
        step_start(),
        step_finish(cost=0.01),
        step_start(),
        step_finish(cost=0.01),  # 0.02 > 0.015 cap -> breach
        step_start(),
        step_finish(cost=0.01),  # must never be processed
    )
    result, state, collected = _start(
        monkeypatch,
        tmp_path,
        events,
        caps=HarnessCaps(budget_cap_usd=0.015, max_turns=5),
    )
    assert state["proc"].terminated  # process group killed on breach
    assert result.exit_status == "budget_exceeded"
    # Meter total is authoritative: only the two processed steps.
    assert result.cost_usd == pytest.approx(0.02)
    assert result.error is not None and "budget cap" in result.error
    failed = [e for e in collected if e.kind == "failed"]
    assert failed and failed[-1].payload["subtype"] == "budget_exceeded"
    assert failed[-1].payload["spent_usd"] == pytest.approx(0.02)


def test_exact_cap_is_not_a_breach(monkeypatch, tmp_path):
    events = (
        step_start(),
        step_finish(cost=0.01),
        step_start(),
        text_event(),
        step_finish(cost=0.01),
    )
    result, state, _ = _start(
        monkeypatch,
        tmp_path,
        events,
        caps=HarnessCaps(budget_cap_usd=0.02, max_turns=5),
    )
    assert not state["proc"].terminated
    assert result.exit_status == "success"
    assert result.cost_usd == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_command_shape(monkeypatch, tmp_path):
    _, state, _ = _start(monkeypatch, tmp_path, HAPPY_PATH)
    cmd = state["cmd"]
    assert cmd[:3] == ["opencode", "run", "do the thing"]
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd[cmd.index("--model") + 1] == MODEL
    assert "--session" not in cmd
    assert state["kwargs"]["cwd"] == str(tmp_path)
    assert state["kwargs"]["start_new_session"] is True


def test_resume_builds_session_flag(monkeypatch, tmp_path):
    state = install_fake_popen(monkeypatch, runner_utils, events=HAPPY_PATH)
    runner = OpencodeRunner(model=MODEL)
    result = runner.resume(
        SESSION_ID, "continue the work", tmp_path, HarnessCaps(max_turns=5)
    )
    cmd = state["cmd"]
    assert cmd[cmd.index("--session") + 1] == SESSION_ID
    assert cmd[2] == "continue the work"
    assert result.session_id == SESSION_ID


# ---------------------------------------------------------------------------
# Env hygiene + per-run permission injection
# ---------------------------------------------------------------------------


def test_env_injects_isolation_and_permission(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"permission":{"bash":"allow"}}')
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _, state, _ = _start(monkeypatch, tmp_path, HAPPY_PATH)
    env = state["kwargs"]["env"]
    # Claude-artifact isolation (research pitfall 7) + CLAUDE* scrub.
    assert env["OPENCODE_DISABLE_CLAUDE_CODE"] == "true"
    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    # Inherited OPENCODE_* must never override the per-run profile (PRD).
    assert "OPENCODE_CONFIG_CONTENT" not in env
    assert env["OPENAI_API_KEY"] == "sk-test"
    # Restrictive per-run permission profile (research pitfall 6).
    permission = json.loads(env["OPENCODE_PERMISSION"])
    assert permission == RESTRICTIVE_PERMISSION
    assert permission["external_directory"] == "deny"  # workspace scoping
    assert permission["edit"] == "allow"
    # Run mode denies external side-effects outright — "ask" stalls a
    # TTY-less subprocess run (PR5 stall fix; serve-mode interactive
    # approvals are future work).
    assert permission["bash"] == "deny"
    assert permission["webfetch"] == "deny"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_error_event_marks_run_failed(monkeypatch, tmp_path):
    events = (step_start(), error_event())
    result, _, collected = _start(monkeypatch, tmp_path, events, returncode=1)
    assert result.exit_status == "error"
    assert result.error is not None and "invalid api key" in result.error
    assert collected[-1].kind == "failed"


def test_nonzero_exit_without_error_event(monkeypatch, tmp_path):
    result, _, _ = _start(monkeypatch, tmp_path, (step_start(),), returncode=2)
    assert result.exit_status == "error"
    assert result.error is not None


def test_resume_keeps_session_id_when_stream_dies_early(monkeypatch, tmp_path):
    install_fake_popen(monkeypatch, runner_utils, events=(), returncode=1)
    runner = OpencodeRunner(model=MODEL)
    result = runner.resume(
        SESSION_ID, "continue", tmp_path, HarnessCaps(max_turns=5)
    )
    assert result.exit_status == "error"
    # The known id must survive an event-less crash — still resumable.
    assert result.session_id == SESSION_ID


# ---------------------------------------------------------------------------
# Constitution guard (shared safety module)
# ---------------------------------------------------------------------------


def test_refuses_workspace_inside_kompany_source(monkeypatch):
    install_fake_popen(monkeypatch, runner_utils, events=HAPPY_PATH)
    pkg_dir = Path(kompany.__file__).resolve().parent
    runner = OpencodeRunner(model=MODEL)
    with pytest.raises(ValueError, match="source"):
        runner.start("x", pkg_dir, HarnessCaps(max_turns=5))


def test_resume_also_guarded(monkeypatch):
    install_fake_popen(monkeypatch, runner_utils, events=HAPPY_PATH)
    pkg_dir = Path(kompany.__file__).resolve().parent
    runner = OpencodeRunner(model=MODEL)
    with pytest.raises(ValueError, match="source"):
        runner.resume(SESSION_ID, "x", pkg_dir, HarnessCaps(max_turns=5))


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------


def test_handoff_budget_exceeded_suggests_approval_then_resume(
    monkeypatch, tmp_path
):
    events = (step_start(), step_finish(cost=0.05))
    result, _, _ = _start(
        monkeypatch,
        tmp_path,
        events,
        caps=HarnessCaps(budget_cap_usd=0.01, max_turns=5),
    )
    runner = OpencodeRunner(model=MODEL)
    brief = runner.handoff(result)
    assert brief.vehicle == "opencode"
    assert brief.session_id == SESSION_ID
    assert "Budget cap" in brief.next_steps
    assert "resume" in brief.next_steps
