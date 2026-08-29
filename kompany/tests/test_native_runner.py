"""NativeRunner vehicle tests (issue #20, 06-12-native-runner PRD).

Scripted fake-LLM loop tests — no network, no real CLI. The fake mirrors
``LLMClient.call_structured``'s return shape (an LLMResponse-like object
with ``.parsed``, ``.cost_usd``, token counts).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kompany.config.model_source import ModelSource
from kompany.core.harness.native_runner import (
    SESSION_DIR_NAME,
    NativeRunner,
    NativeTurn,
    ToolCall,
)
from kompany.core.harness.native_tools import execute_tool
from kompany.core.harness.protocol import HarnessCaps
from kompany.core.harness.safety import kompany_source_root
from kompany.core.harness_execution.selection import select_runner


class FakeLLM:
    """Scripted call_structured: pops the next NativeTurn per call."""

    def __init__(self, turns, cost_per_call=0.01, cost_recorded=True):
        self.turns = list(turns)
        self.cost_per_call = cost_per_call
        self.cost_recorded = cost_recorded
        self.calls = []

    def call_structured(self, **kwargs):
        self.calls.append(kwargs)
        turn = self.turns.pop(0)
        return SimpleNamespace(
            text=turn.model_dump_json(),
            parsed=turn,
            cost_usd=self.cost_per_call,
            input_tokens=100,
            output_tokens=50,
            model=kwargs.get("model", "m"),
            _cost_recorded=self.cost_recorded,
        )


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=str(ws), check=True)
    return ws


def _write_then_done():
    return [
        NativeTurn(
            thought="write the file",
            tool=ToolCall(
                name="write_file",
                args={"path": "hello.txt", "content": "hi"},
            ),
        ),
        NativeTurn(thought="done", done=True, final_text="wrote hello.txt"),
    ]


def test_happy_path_write_then_done(workspace):
    llm = FakeLLM(_write_then_done())
    runner = NativeRunner(model="gpt-test", llm_client=llm)
    result = runner.start("write hello", workspace, HarnessCaps(max_turns=5))

    assert result.exit_status == "success"
    assert result.final_text == "wrote hello.txt"
    assert (workspace / "hello.txt").read_text() == "hi"
    assert "hello.txt" in result.files_changed
    assert result.cost_usd == pytest.approx(0.02)
    assert result.cost_is_estimate is False
    assert result.cost_already_recorded is True
    assert result.tokens_in == 200 and result.tokens_out == 100
    assert result.session_id
    # every call books through the normal cost path label
    assert all(c["action_type"] == "native_runner" for c in llm.calls)


def test_events_emitted_in_order(workspace):
    llm = FakeLLM(_write_then_done())
    runner = NativeRunner(model="m", llm_client=llm)
    events = []
    runner.start(
        "go", workspace, HarnessCaps(max_turns=5), on_event=events.append
    )
    kinds = [e.kind for e in events]
    assert kinds == [
        "session_started",
        "turn",
        "tool_use",
        "turn",
        "text",
        "completed",
    ]
    assert events[2].payload["tool_name"] == "write_file"


def test_budget_breach_stops_cleanly(workspace):
    turns = [
        NativeTurn(thought=f"t{i}", tool=ToolCall(name="list_dir", args={}))
        for i in range(5)
    ]
    llm = FakeLLM(turns, cost_per_call=0.6)
    runner = NativeRunner(model="m", llm_client=llm)
    result = runner.start(
        "go", workspace, HarnessCaps(budget_cap_usd=1.0, max_turns=10)
    )
    assert result.exit_status == "budget_exceeded"
    assert len(llm.calls) == 2  # 0.6 < 1.0, 1.2 >= 1.0 → stop turn 2
    assert result.cost_usd == pytest.approx(1.2)
    assert "budget" in (result.error or "")


def test_untracked_llm_client_leaves_cost_for_outer_booking(workspace):
    llm = FakeLLM(
        [NativeTurn(thought="done", done=True, final_text="ok")],
        cost_recorded=False,
    )
    runner = NativeRunner(model="m", llm_client=llm)

    result = runner.start("go", workspace, HarnessCaps(max_turns=1))

    assert result.cost_usd == pytest.approx(0.01)
    assert result.cost_already_recorded is False


def test_turn_cap_preserves_work(workspace):
    turns = [
        NativeTurn(
            thought=f"t{i}",
            tool=ToolCall(
                name="write_file", args={"path": f"f{i}.txt", "content": "x"}
            ),
        )
        for i in range(10)
    ]
    llm = FakeLLM(turns)
    runner = NativeRunner(model="m", llm_client=llm)
    result = runner.start("go", workspace, HarnessCaps(max_turns=3))
    assert result.exit_status == "error_max_turns"
    assert len(llm.calls) == 3
    assert (workspace / "f0.txt").exists()  # work preserved
    assert "f0.txt" in result.files_changed


def test_shell_tool_observation_captured(workspace):
    llm = FakeLLM([
        NativeTurn(
            thought="run",
            tool=ToolCall(name="run_shell", args={"command": "echo hello-obs"}),
        ),
        NativeTurn(thought="done", done=True, final_text="ok"),
    ])
    runner = NativeRunner(model="m", llm_client=llm)
    result = runner.start("go", workspace, HarnessCaps(max_turns=5))
    assert result.exit_status == "success"
    # the observation reaches the next turn's prompt
    assert "hello-obs" in llm.calls[1]["prompt"]
    assert "exit code: 0" in llm.calls[1]["prompt"]


def test_path_escape_rejected_as_observation(workspace):
    llm = FakeLLM([
        NativeTurn(
            thought="escape",
            tool=ToolCall(
                name="write_file",
                args={"path": "../outside.txt", "content": "x"},
            ),
        ),
        NativeTurn(thought="done", done=True, final_text="ok"),
    ])
    runner = NativeRunner(model="m", llm_client=llm)
    result = runner.start("go", workspace, HarnessCaps(max_turns=5))
    # loop continued past the rejection and completed
    assert result.exit_status == "success"
    assert not (workspace.parent / "outside.txt").exists()
    assert "escapes the workspace" in llm.calls[1]["prompt"]


def test_execute_tool_unknown_name_is_observation(workspace):
    obs = execute_tool(workspace, "browse_web", {})
    assert obs.startswith("ERROR: unknown tool")


def test_transcript_persisted_per_turn(workspace):
    llm = FakeLLM(_write_then_done())
    runner = NativeRunner(model="m", llm_client=llm)
    result = runner.start("go", workspace, HarnessCaps(max_turns=5))
    path = workspace / SESSION_DIR_NAME / f"{result.session_id}.jsonl"
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["tool"]["name"] == "write_file"
    assert "Wrote" in lines[0]["observation"]
    assert lines[1]["done"] is True


def test_resume_loads_transcript_and_continues(workspace):
    # First leg: one tool turn, then turn cap.
    llm1 = FakeLLM([
        NativeTurn(
            thought="step one",
            tool=ToolCall(
                name="write_file", args={"path": "a.txt", "content": "1"}
            ),
        ),
    ])
    runner1 = NativeRunner(model="m", llm_client=llm1)
    first = runner1.start("go", workspace, HarnessCaps(max_turns=1))
    assert first.exit_status == "error_max_turns"

    # Second leg resumes the same session and finishes.
    llm2 = FakeLLM([NativeTurn(thought="done", done=True, final_text="fin")])
    runner2 = NativeRunner(model="m", llm_client=llm2)
    result = runner2.resume(
        first.session_id, "continue", workspace, HarnessCaps(max_turns=5)
    )
    assert result.exit_status == "success"
    assert result.session_id == first.session_id
    # prior transcript context made it into the resumed prompt
    assert "step one" in llm2.calls[0]["prompt"]
    # transcript file accumulated both legs
    path = workspace / SESSION_DIR_NAME / f"{first.session_id}.jsonl"
    assert len(path.read_text().splitlines()) == 2


def test_llm_error_returns_error_result(workspace):
    class BoomLLM:
        def call_structured(self, **kwargs):
            raise RuntimeError("provider down")

    runner = NativeRunner(model="m", llm_client=BoomLLM())
    result = runner.start("go", workspace, HarnessCaps(max_turns=5))
    assert result.exit_status == "error"
    assert "provider down" in (result.error or "")


def test_safety_guard_refuses_source_repo():
    runner = NativeRunner(model="m", llm_client=FakeLLM([]))
    with pytest.raises(ValueError, match="constitution"):
        runner.start("go", kompany_source_root(), HarnessCaps())


def test_selection_returns_native_when_flag_on():
    settings = SimpleNamespace(
        model_source=ModelSource(kind="custom_api"),
        harness_execution_enabled=True,
        native_runner_enabled=True,
        model_primary="gpt-5",
    )
    llm = FakeLLM([])
    runner = select_runner(settings, llm_client=llm)
    assert isinstance(runner, NativeRunner)
    assert runner.vehicle_name == "native"
    assert runner._model == "gpt-5"  # passes through, no prefix strip


def test_selection_flag_off_keeps_opencode_derivation():
    settings = SimpleNamespace(
        model_source=ModelSource(kind="custom_api"),
        harness_execution_enabled=True,
        native_runner_enabled=False,
        model_primary="gpt-5",
    )
    runner = select_runner(settings, llm_client=FakeLLM([]))
    assert not isinstance(runner, NativeRunner)


def test_selection_native_requires_llm_client():
    settings = SimpleNamespace(
        model_source=ModelSource(kind="custom_api"),
        harness_execution_enabled=True,
        native_runner_enabled=True,
        model_primary="gpt-5",
    )
    runner = select_runner(settings, llm_client=None)
    assert not isinstance(runner, NativeRunner)
