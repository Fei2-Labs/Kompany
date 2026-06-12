"""Tests for ClaudeCodeRunner — fake subprocess, canned stream-json lines.

Line shapes are taken from the captured schema in
``.trellis/tasks/06-11-harness-execution-leg/research/claude-headless-interface.md``
(claude CLI 2.1.173). No real ``claude`` calls, no paid APIs.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

import kompany
from kompany.core.harness import claude_code_runner as ccr
from kompany.core.harness.protocol import HarnessCaps, HarnessResult

SESSION_ID = "fdec9d49-aaaa-bbbb-cccc-1234567890ab"


# ---------------------------------------------------------------------------
# Canned stream-json events (shapes verified live in the research doc)
# ---------------------------------------------------------------------------


def init_event() -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "cwd": "/tmp/ws",
        "session_id": SESSION_ID,
        "tools": ["Bash", "Edit", "Read"],
        "mcp_servers": [],
        "model": "claude-sonnet-4-6",
        "permissionMode": "default",
        "slash_commands": [],
        "apiKeySource": "none",
        "claude_code_version": "2.1.173",
        "uuid": "evt-init",
    }


def assistant_event() -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Creating the file now."},
                {
                    "type": "tool_use",
                    "id": "toolu_01ABC",
                    "name": "Bash",
                    "input": {"command": "touch hello.txt"},
                },
            ],
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 5949,
                "cache_read_input_tokens": 17485,
                "output_tokens": 4,
                "service_tier": "standard",
            },
        },
        "parent_tool_use_id": None,
        "session_id": SESSION_ID,
        "request_id": "req_1",
        "uuid": "evt-asst",
    }


def result_event(**overrides) -> dict:
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "result": "OK",
        "stop_reason": "end_turn",
        "session_id": SESSION_ID,
        "total_cost_usd": 0.0415145,
        "usage": {
            "input_tokens": 3,
            "cache_creation_input_tokens": 5949,
            "cache_read_input_tokens": 17485,
            "output_tokens": 4,
            "server_tool_use": {
                "web_search_requests": 0,
                "web_fetch_requests": 0,
            },
        },
        "modelUsage": {
            "claude-sonnet-4-6": {
                "inputTokens": 3,
                "outputTokens": 4,
                "cacheReadInputTokens": 17485,
                "cacheCreationInputTokens": 5949,
                "costUSD": 0.0410085,
                "contextWindow": 200000,
            }
        },
        "permission_denials": [],
        "terminal_reason": "completed",
        "duration_ms": 1647,
        "duration_api_ms": 3119,
        "ttft_ms": 1638,
        "uuid": "evt-result",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fake subprocess
# ---------------------------------------------------------------------------


class _BlockingStdout:
    """Stdout that never yields a line until the process is terminated."""

    def __init__(self) -> None:
        self.proc: FakePopen | None = None

    def __iter__(self):
        return self

    def __next__(self) -> str:
        deadline = time.time() + 5.0  # safety net so tests can't hang
        while time.time() < deadline:
            if self.proc is not None and (self.proc.terminated or self.proc.killed):
                raise StopIteration
            time.sleep(0.01)
        raise StopIteration


class FakePopen:
    def __init__(
        self,
        args,
        *,
        events=(),
        returncode=0,
        stderr_text="",
        block=False,
    ) -> None:
        self.args = args
        self.returncode = returncode
        # Way above macOS/Linux pid_max so os.getpgid raises and the
        # runner falls back to .terminate() on this stub.
        self.pid = 999_999_999
        self.terminated = False
        self.killed = False
        self.stderr = io.StringIO(stderr_text)
        if block:
            stdout = _BlockingStdout()
            stdout.proc = self
            self.stdout = stdout
        else:
            self.stdout = [json.dumps(e) + "\n" for e in events]

    def wait(self, timeout=None) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


@pytest.fixture(autouse=True)
def safe_mode_supported(monkeypatch):
    """Never probe the real `claude --help`; default to safe-mode capable."""
    monkeypatch.setattr(ccr, "_HELP_CACHE", {"safe_mode": True})


@pytest.fixture
def fake_popen(monkeypatch):
    """Install a Popen stub; returns a dict capturing cmd/kwargs/proc."""
    state: dict = {}

    def install(events=(), returncode=0, stderr_text="", block=False):
        def popen(cmd, **kwargs):
            proc = FakePopen(
                cmd,
                events=events,
                returncode=returncode,
                stderr_text=stderr_text,
                block=block,
            )
            state["cmd"] = cmd
            state["kwargs"] = kwargs
            state["proc"] = proc
            return proc

        monkeypatch.setattr(ccr.subprocess, "Popen", popen)
        return state

    return install


def _start(fake_popen, tmp_path, events, returncode=0, caps=None, **runner_kw):
    state = fake_popen(events=events, returncode=returncode)
    collected = []
    runner = ccr.ClaudeCodeRunner(**runner_kw)
    result = runner.start(
        "do the thing",
        tmp_path,
        caps or HarnessCaps(max_turns=5),
        on_event=collected.append,
    )
    return result, state, collected


HAPPY_PATH = (init_event(), assistant_event(), result_event())


# ---------------------------------------------------------------------------
# Stream parsing
# ---------------------------------------------------------------------------


def test_session_id_captured(fake_popen, tmp_path):
    result, _, _ = _start(fake_popen, tmp_path, HAPPY_PATH)
    assert result.session_id == SESSION_ID


def test_event_normalization_order(fake_popen, tmp_path):
    _, _, events = _start(fake_popen, tmp_path, HAPPY_PATH)
    assert [e.kind for e in events] == [
        "session_started",
        "turn",
        "text",
        "tool_use",
        "completed",
    ]
    assert events[0].payload["session_id"] == SESSION_ID
    assert events[1].payload["usage"]["output_tokens"] == 4
    assert events[2].payload["text"] == "Creating the file now."
    assert events[3].payload["tool_name"] == "Bash"
    assert events[3].payload["tool_input"] == {"command": "touch hello.txt"}
    assert events[4].payload["total_cost_usd"] == pytest.approx(0.0415145)


def test_result_fields_parsed(fake_popen, tmp_path):
    result, _, _ = _start(fake_popen, tmp_path, HAPPY_PATH)
    assert result.final_text == "OK"
    assert result.cost_usd == pytest.approx(0.0415145)
    assert result.tokens_in == 3
    assert result.tokens_out == 4
    assert result.exit_status == "success"
    assert result.error is None
    assert result.permission_denials == []
    # tmp_path is not a git repo -> no diff evidence
    assert result.files_changed == []


def test_permission_denials_surfaced_not_swallowed(fake_popen, tmp_path):
    denial = {
        "tool_name": "Bash",
        "tool_use_id": "toolu_02DEF",
        "tool_input": {"command": "curl https://example.com"},
    }
    events = (
        init_event(),
        assistant_event(),
        result_event(permission_denials=[denial]),
    )
    result, _, collected = _start(fake_popen, tmp_path, events)
    # Run "succeeds" (exit 0) but the denial must be visible.
    assert result.exit_status == "success"
    assert result.permission_denials == [denial]
    kinds = [e.kind for e in collected]
    assert "permission_denied" in kinds
    assert kinds.index("permission_denied") < kinds.index("completed")
    denied = next(e for e in collected if e.kind == "permission_denied")
    assert denied.payload["tool_name"] == "Bash"


def test_error_max_turns_exit_1_cost_still_parsed(fake_popen, tmp_path):
    events = (
        init_event(),
        assistant_event(),
        result_event(
            subtype="error_max_turns",
            is_error=True,
            result="",
            terminal_reason="max_turns",
            errors=["Reached maximum number of turns (5)"],
        ),
    )
    result, _, collected = _start(fake_popen, tmp_path, events, returncode=1)
    assert result.exit_status == "error_max_turns"
    # Capped runs still yield an authoritative ledger entry (verified live).
    assert result.cost_usd == pytest.approx(0.0415145)
    assert result.tokens_in == 3
    assert result.tokens_out == 4
    assert result.error is not None
    assert "maximum number of turns" in result.error
    assert collected[-1].kind == "failed"


def test_no_result_event_is_error(fake_popen, tmp_path):
    result, _, collected = _start(
        fake_popen, tmp_path, (init_event(),), returncode=1
    )
    assert result.exit_status == "error"
    assert result.cost_usd is None
    assert result.error is not None
    assert collected[-1].kind == "failed"


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_command_shape(fake_popen, tmp_path):
    _, state, _ = _start(
        fake_popen,
        tmp_path,
        HAPPY_PATH,
        caps=HarnessCaps(max_turns=7),
        model="sonnet",
        permission_mode="default",
    )
    cmd = state["cmd"]
    assert cmd[0] == "claude"
    assert cmd[cmd.index("-p") + 1] == "do the thing"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "7"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--permission-mode") + 1] == "default"
    assert state["kwargs"]["cwd"] == str(tmp_path)
    assert state["kwargs"]["start_new_session"] is True


def test_budget_flag_present_iff_cap_set(fake_popen, tmp_path):
    _, state, _ = _start(
        fake_popen,
        tmp_path,
        HAPPY_PATH,
        caps=HarnessCaps(budget_cap_usd=2.5, max_turns=5),
    )
    cmd = state["cmd"]
    assert cmd[cmd.index("--max-budget-usd") + 1] == "2.5"

    _, state, _ = _start(
        fake_popen, tmp_path, HAPPY_PATH, caps=HarnessCaps(max_turns=5)
    )
    assert "--max-budget-usd" not in state["cmd"]


def test_safe_mode_flag_when_supported(fake_popen, tmp_path):
    _, state, _ = _start(fake_popen, tmp_path, HAPPY_PATH)
    assert "--safe-mode" in state["cmd"]
    assert "--setting-sources" not in state["cmd"]


def test_setting_sources_fallback_when_safe_mode_unsupported(
    fake_popen, tmp_path, monkeypatch
):
    monkeypatch.setattr(ccr, "_HELP_CACHE", {"safe_mode": False})
    _, state, _ = _start(fake_popen, tmp_path, HAPPY_PATH)
    cmd = state["cmd"]
    assert "--safe-mode" not in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""


def test_resume_builds_resume_flag(fake_popen, tmp_path):
    state = fake_popen(events=HAPPY_PATH)
    runner = ccr.ClaudeCodeRunner()
    result = runner.resume(
        SESSION_ID, "continue the review", tmp_path, HarnessCaps(max_turns=5)
    )
    cmd = state["cmd"]
    assert cmd[cmd.index("--resume") + 1] == SESSION_ID
    assert cmd[cmd.index("-p") + 1] == "continue the review"
    assert result.session_id == SESSION_ID


def test_start_has_no_resume_flag(fake_popen, tmp_path):
    _, state, _ = _start(fake_popen, tmp_path, HAPPY_PATH)
    assert "--resume" not in state["cmd"]


# ---------------------------------------------------------------------------
# Env hygiene
# ---------------------------------------------------------------------------


def test_env_scrubs_claude_vars_keeps_anthropic_key(
    fake_popen, tmp_path, monkeypatch
):
    monkeypatch.setenv("CLAUDE_FOO", "leak")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("KOMPANY_HOME", "/tmp/k")
    _, state, _ = _start(fake_popen, tmp_path, HAPPY_PATH)
    env = state["kwargs"]["env"]
    assert "CLAUDE_FOO" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert "CLAUDECODE" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["KOMPANY_HOME"] == "/tmp/k"


# ---------------------------------------------------------------------------
# Timeout / kill
# ---------------------------------------------------------------------------


def test_timeout_terminates_and_marks_cost_non_authoritative(
    fake_popen, tmp_path
):
    state = fake_popen(block=True)
    runner = ccr.ClaudeCodeRunner(timeout_seconds=0.1)
    result = runner.start("hang forever", tmp_path, HarnessCaps(max_turns=5))
    assert state["proc"].terminated
    assert result.exit_status == "timeout"
    assert result.cost_usd is None
    assert result.error is not None
    assert "timeout" in result.error


# ---------------------------------------------------------------------------
# Constitution guard: never target the Kompany source repo
# ---------------------------------------------------------------------------


def test_refuses_workspace_inside_kompany_source(fake_popen, tmp_path):
    fake_popen(events=HAPPY_PATH)
    pkg_dir = Path(kompany.__file__).resolve().parent
    runner = ccr.ClaudeCodeRunner()
    with pytest.raises(ValueError, match="source"):
        runner.start("x", pkg_dir, HarnessCaps(max_turns=5))


def test_refuses_workspace_that_contains_kompany_source(fake_popen):
    fake_popen(events=HAPPY_PATH)
    pkg_parent = Path(kompany.__file__).resolve().parent.parent
    runner = ccr.ClaudeCodeRunner()
    with pytest.raises(ValueError, match="source"):
        runner.start("x", pkg_parent, HarnessCaps(max_turns=5))


def test_resume_also_guarded(fake_popen):
    fake_popen(events=HAPPY_PATH)
    pkg_dir = Path(kompany.__file__).resolve().parent
    runner = ccr.ClaudeCodeRunner()
    with pytest.raises(ValueError, match="source"):
        runner.resume(SESSION_ID, "x", pkg_dir, HarnessCaps(max_turns=5))


def test_unrelated_workspace_allowed(fake_popen, tmp_path):
    result, _, _ = _start(fake_popen, tmp_path, HAPPY_PATH)
    assert result.exit_status == "success"


# ---------------------------------------------------------------------------
# Handoff brief (non-LLM assembly)
# ---------------------------------------------------------------------------


def test_handoff_assembles_brief_from_result():
    runner = ccr.ClaudeCodeRunner()
    result = HarnessResult(
        final_text="Landing page drafted.",
        session_id=SESSION_ID,
        files_changed=["index.html", "style.css"],
        cost_usd=0.05,
        exit_status="success",
        permission_denials=[{"tool_name": "Bash", "tool_input": {}}],
    )
    brief = runner.handoff(result)
    assert brief.vehicle == "claude_code"
    assert brief.session_id == SESSION_ID
    assert "index.html" in brief.done
    assert "Landing page drafted." in brief.done
    assert "Bash" in brief.stuck
    assert "approv" in brief.next_steps


def test_handoff_max_turns_suggests_resume():
    runner = ccr.ClaudeCodeRunner()
    result = HarnessResult(
        session_id=SESSION_ID,
        exit_status="error_max_turns",
        error="Reached maximum number of turns (5)",
        cost_usd=0.02,
    )
    brief = runner.handoff(result)
    assert "resume" in brief.next_steps.lower()
    assert "Reached maximum number of turns" in brief.stuck
