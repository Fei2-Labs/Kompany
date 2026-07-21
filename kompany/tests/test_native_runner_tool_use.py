"""NativeRunner native-tool_use loop tests (06-16 P1).

A scripted fake LLM that advertises ``supports_native_tools`` and returns
``call_with_tools``-shaped responses, asserting the runner dispatches the
requested tool through the registry and threads a tool_result back before
the model finishes.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from kompany.core.harness.native_runner import NativeRunner
from kompany.core.harness.protocol import HarnessCaps
from kompany.llm.client_parts._types import ToolCallRequest


class FakeToolLLM:
    """Native-tool_use fake: pops a scripted response per call_with_tools."""

    def __init__(self, responses, cost_per_call=0.01):
        self.responses = list(responses)
        self.cost_per_call = cost_per_call
        self.calls = []

    def supports_native_tools(self, model: str) -> bool:
        return True

    def call_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        tool_calls, text, assistant = self.responses.pop(0)
        return SimpleNamespace(
            text=text,
            tool_calls=tool_calls,
            raw_assistant_message=assistant,
            stop_reason="tool_use" if tool_calls else "end_turn",
            cost_usd=self.cost_per_call,
            input_tokens=100,
            output_tokens=50,
            model=kwargs.get("model", "m"),
        )


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=str(ws), check=True)
    return ws


def test_native_tool_call_dispatches_through_registry(workspace):
    # Turn 1: model asks to write a file. Turn 2: no tool calls -> done.
    write_call = ToolCallRequest(
        call_id="toolu_1",
        name="write_file",
        arguments={"path": "out.txt", "content": "hello"},
    )
    assistant_turn1 = {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "write_file",
             "input": {"path": "out.txt", "content": "hello"}},
        ],
    }
    llm = FakeToolLLM([
        ([write_call], "writing the file", assistant_turn1),
        ([], "done — wrote out.txt", {"role": "assistant", "content": "done"}),
    ])
    runner = NativeRunner(model="claude-sonnet-4-20250514", llm_client=llm)
    result = runner.start("write hello", workspace, HarnessCaps(max_turns=5))

    assert result.exit_status == "success"
    assert result.final_text == "done — wrote out.txt"
    assert (workspace / "out.txt").read_text() == "hello"

    # native tool_use was used: tools= passed to the client
    assert "tools" in llm.calls[0]
    tool_names = {t.name for t in llm.calls[0]["tools"]}
    assert {"write_file", "web_fetch", "file_patch"} <= tool_names

    # the tool_result was threaded back as a user message before turn 2
    second_messages = llm.calls[1]["messages"]
    # assistant tool_use turn + tool_result user turn appended
    tool_result_msgs = [
        m for m in second_messages
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert tool_result_msgs, "tool_result follow-up not threaded"
    block = tool_result_msgs[0]["content"][0]
    assert block["tool_use_id"] == "toolu_1"
    assert "Wrote" in block["content"]

    # cost booked, action_type label preserved
    assert result.cost_usd == pytest.approx(0.02)
    assert all(c["action_type"] == "native_runner" for c in llm.calls)


def test_gated_tool_returns_gated_observation(workspace):
    from kompany.core.agent_tools import (
        FunctionTool,
        SideEffect,
        ToolRegistry,
    )
    from kompany.core.agent_tools.workspace_tools import workspace_tools

    gated = FunctionTool(
        name="email.send",
        description="send email",
        parameters={"type": "object", "properties": {}},
        side_effect=SideEffect.EXTERNAL_ACTION,
        func=lambda a, c: "SENT",
    )
    reg = ToolRegistry(workspace_tools() + [gated])

    call = ToolCallRequest(call_id="t1", name="email.send", arguments={})
    assistant = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t1", "name": "email.send",
                     "input": {}}],
    }
    llm = FakeToolLLM([
        ([call], "sending", assistant),
        ([], "could not send — gated", {"role": "assistant", "content": "x"}),
    ])
    runner = NativeRunner(
        model="claude-sonnet-4-20250514", llm_client=llm, registry=reg
    )
    result = runner.start("email someone", workspace, HarnessCaps(max_turns=5))

    assert result.exit_status == "success"
    # the gated observation was fed back, real tool never ran
    second_messages = llm.calls[1]["messages"]
    tr = next(
        m["content"][0] for m in second_messages
        if m.get("role") == "user" and isinstance(m.get("content"), list)
    )
    assert tr["content"].startswith("GATED: ")


def test_json_fallback_when_provider_lacks_native_tools(workspace):
    """A client without supports_native_tools uses the legacy JSON path."""
    from kompany.core.harness.native_runner import NativeTurn, ToolCall

    class JsonLLM:
        def __init__(self):
            self.turns = [
                NativeTurn(
                    thought="write",
                    tool=ToolCall(name="write_file",
                                  args={"path": "j.txt", "content": "x"}),
                ),
                NativeTurn(thought="done", done=True, final_text="ok"),
            ]
            self.calls = []

        def call_structured(self, **kwargs):
            self.calls.append(kwargs)
            turn = self.turns.pop(0)
            return SimpleNamespace(
                text=turn.model_dump_json(), parsed=turn,
                cost_usd=0.01, input_tokens=10, output_tokens=5,
            )

    llm = JsonLLM()
    runner = NativeRunner(model="some-cli-model", llm_client=llm)
    result = runner.start("go", workspace, HarnessCaps(max_turns=5))
    assert result.exit_status == "success"
    assert (workspace / "j.txt").read_text() == "x"
    # used call_structured (JSON path), not call_with_tools
    assert llm.calls and "output_schema" in llm.calls[0]
