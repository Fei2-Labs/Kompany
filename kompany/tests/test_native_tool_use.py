"""Native tool_use + tool-schema translation tests (06-16 P1).

No network: the Anthropic / OpenAI clients are replaced with fakes that
record the kwargs they were called with and return canned tool_use /
tool_calls responses.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kompany.llm.client import LLMClient
from kompany.llm.client_parts._provider_mixin import (
    provider_supports_native_tools,
)
from kompany.llm.client_parts._types import ToolSpec
from kompany.llm.providers import Provider


# --------------------------------------------------------------------- #
# schema translation
# --------------------------------------------------------------------- #

def _spec() -> ToolSpec:
    return ToolSpec(
        name="web_fetch",
        description="Fetch a URL.",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    )


def test_toolspec_to_anthropic():
    a = _spec().to_anthropic()
    assert a["name"] == "web_fetch"
    assert a["description"] == "Fetch a URL."
    assert a["input_schema"]["properties"]["url"]["type"] == "string"
    assert a["input_schema"]["required"] == ["url"]


def test_toolspec_to_openai():
    o = _spec().to_openai()
    assert o["type"] == "function"
    assert o["function"]["name"] == "web_fetch"
    assert o["function"]["parameters"]["required"] == ["url"]


def test_capability_flag_excludes_cli_providers():
    assert provider_supports_native_tools(Provider.ANTHROPIC)
    assert provider_supports_native_tools(Provider.OPENAI)
    assert provider_supports_native_tools(Provider.GEMINI)
    assert not provider_supports_native_tools(Provider.CLAUDE_CODE)
    assert not provider_supports_native_tools(Provider.CODEX_CLI)
    assert not provider_supports_native_tools(Provider.OPENCODE_CLI)


# --------------------------------------------------------------------- #
# client wiring (mocked SDKs)
# --------------------------------------------------------------------- #

class _Settings:
    anthropic_api_key = "sk-ant-test"
    openai_api_key = "sk-openai-test"
    custom_api_key = ""
    custom_base_url = ""

    def get_api_key_for_provider(self, provider: str) -> str:
        return "k"


class _CostTracker:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return 0.42


class _FakeAnthropicMessages:
    def __init__(self, capture):
        self._capture = capture

    def create(self, **kwargs):
        self._capture.update(kwargs)
        tool_use = SimpleNamespace(
            type="tool_use", id="toolu_1", name="web_fetch",
            input={"url": "https://x.test"},
        )
        text = SimpleNamespace(type="text", text="let me fetch")
        return SimpleNamespace(
            content=[text, tool_use],
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            stop_reason="tool_use",
        )


class _FakeAnthropicClient:
    def __init__(self, capture):
        self.messages = _FakeAnthropicMessages(capture)


def test_anthropic_call_with_tools_includes_tools_and_parses_tool_use():
    capture: dict = {}
    cost = _CostTracker()
    client = LLMClient(settings=_Settings(), cost_tracker=cost)
    client._anthropic_client = _FakeAnthropicClient(capture)

    resp = client.call_with_tools(
        model="claude-sonnet-4-20250514",
        system="be helpful",
        messages=[{"role": "user", "content": "fetch x"}],
        tools=[_spec()],
        action_type="native_runner",
    )

    # native tool_use: tools= present in the request
    assert "tools" in capture
    assert capture["tools"][0]["name"] == "web_fetch"
    assert capture["tool_choice"] == {"type": "auto"}
    assert capture["system"] == "be helpful"
    # response parsed into normalized tool calls
    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.name == "web_fetch"
    assert tc.arguments == {"url": "https://x.test"}
    assert tc.call_id == "toolu_1"
    # assistant message replay carries tool_use block
    assert resp.raw_assistant_message["role"] == "assistant"
    blocks = resp.raw_assistant_message["content"]
    assert any(b["type"] == "tool_use" for b in blocks)
    # cost booked through the existing path
    assert resp.cost_usd == 0.42
    assert cost.calls[0]["action_type"] == "native_runner"


class _FakeOpenAICompletions:
    def __init__(self, capture):
        self._capture = capture

    def create(self, **kwargs):
        self._capture.update(kwargs)
        fn = SimpleNamespace(name="web_search", arguments='{"query": "kompany"}')
        tc = SimpleNamespace(id="call_1", function=fn)
        msg = SimpleNamespace(content=None, tool_calls=[tc])
        choice = SimpleNamespace(message=msg, finish_reason="tool_calls")
        return SimpleNamespace(
            choices=[choice],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=9),
        )


class _FakeOpenAIClient:
    def __init__(self, capture):
        self.chat = SimpleNamespace(
            completions=_FakeOpenAICompletions(capture)
        )


def test_openai_call_with_tools_includes_tools_and_parses_tool_calls():
    capture: dict = {}
    client = LLMClient(settings=_Settings(), cost_tracker=_CostTracker())
    client._openai_clients[Provider.OPENAI] = _FakeOpenAIClient(capture)

    resp = client.call_with_tools(
        model="gpt-4o",
        system="be helpful",
        messages=[{"role": "user", "content": "search kompany"}],
        tools=[_spec()],
    )

    assert capture["tool_choice"] == "auto"
    assert capture["tools"][0]["function"]["name"] == "web_fetch"
    # system prepended
    assert capture["messages"][0] == {"role": "system", "content": "be helpful"}
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.name == "web_search"
    assert tc.arguments == {"query": "kompany"}
    assert tc.call_id == "call_1"
    # assistant replay has openai tool_calls shape
    assert resp.raw_assistant_message["tool_calls"][0]["id"] == "call_1"


def test_supports_native_tools_method():
    client = LLMClient(settings=_Settings(), cost_tracker=_CostTracker())
    assert client.supports_native_tools("claude-sonnet-4-20250514")
    assert client.supports_native_tools("gpt-4o")
    assert not client.supports_native_tools("claude-code:opus")
    assert not client.supports_native_tools("codex:gpt-5")
