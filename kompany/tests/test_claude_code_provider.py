"""Tests for the claude_code provider (local `claude` CLI, no API key)."""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest

from kompany.llm.client import LLMClient
from kompany.llm.providers import Provider, detect_provider


class FakeSettings:
    """Minimal settings for routing tests."""

    anthropic_api_key = ""
    openai_api_key = ""
    gemini_api_key = ""
    glm_api_key = ""
    kimi_api_key = ""
    custom_api_key = ""
    custom_base_url = ""

    def get_api_key_for_provider(self, provider: str) -> str:
        if provider == "claude_code":
            return "no-key-required"
        return ""


@pytest.fixture
def client():
    return LLMClient(settings=FakeSettings(), cost_tracker=None)


# ---------------------------------------------------------------------------
# Provider detection / routing
# ---------------------------------------------------------------------------


def test_detect_claude_code():
    assert detect_provider("claude-code:sonnet") == Provider.CLAUDE_CODE
    assert detect_provider("claude-code:opus") == Provider.CLAUDE_CODE
    assert detect_provider("claude-code:haiku") == Provider.CLAUDE_CODE


def test_detect_anthropic_still_wins_for_api_models():
    # The broader "claude-" prefix must still route API model ids to
    # Anthropic — order in _MODEL_PREFIX_MAP matters.
    assert detect_provider("claude-sonnet-4-20250514") == Provider.ANTHROPIC
    assert detect_provider("claude-opus-4-20250514") == Provider.ANTHROPIC


def test_resolve_claude_code(client):
    assert client._resolve_provider("claude-code:sonnet") == Provider.CLAUDE_CODE


def test_resolve_claude_code_beats_custom_base_url():
    # An explicit claude-code: id must not be misrouted through a custom
    # OpenAI-compatible endpoint.
    settings = FakeSettings()
    settings.custom_base_url = "https://my-endpoint.example.com/v1"
    c = LLMClient(settings=settings, cost_tracker=None)
    assert c._resolve_provider("claude-code:sonnet") == Provider.CLAUDE_CODE


# ---------------------------------------------------------------------------
# _call_claude_code: success path
# ---------------------------------------------------------------------------


def _cli_payload(**overrides):
    payload = {
        "result": "pong",
        "usage": {"input_tokens": 12, "output_tokens": 3},
        "total_cost_usd": 0.001,
    }
    payload.update(overrides)
    return payload


def test_call_claude_code_success(client):
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout=json.dumps(_cli_payload()),
        stderr="",
    )
    with mock.patch(
        "kompany.llm.claude_code.subprocess.run", return_value=completed
    ) as run:
        resp = client._call_claude_code(
            "claude-code:sonnet", "be terse", "ping", 64
        )

    assert resp.text == "pong"
    assert resp.input_tokens == 12
    assert resp.output_tokens == 3
    assert resp.model == "claude-code:sonnet"

    cmd = run.call_args.args[0]
    assert cmd[0] == "claude"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("-p") + 1] == "ping"
    # --system-prompt (replace, NOT --append-system-prompt) plus the
    # isolation flags: founder-level CLAUDE.md / output styles must not
    # leak into agent replies (they break strict-JSON parsing upstream).
    assert cmd[cmd.index("--system-prompt") + 1] == "be terse"
    assert "--append-system-prompt" not in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--max-turns") + 1] == "1"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""


def test_call_claude_code_model_without_suffix_defaults_to_sonnet(client):
    completed = subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout=json.dumps(_cli_payload()),
        stderr="",
    )
    with mock.patch(
        "kompany.llm.claude_code.subprocess.run", return_value=completed
    ) as run:
        client._call_claude_code("claude-code", "sys", "hi", 64)
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("--model") + 1] == "sonnet"


def test_call_claude_code_missing_usage_defaults_to_zero(client):
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout=json.dumps({"result": "ok"}),
        stderr="",
    )
    with mock.patch(
        "kompany.llm.claude_code.subprocess.run", return_value=completed
    ):
        resp = client._call_claude_code("claude-code:haiku", "sys", "hi", 64)
    assert resp.input_tokens == 0
    assert resp.output_tokens == 0


# ---------------------------------------------------------------------------
# _call_claude_code: failure paths
# ---------------------------------------------------------------------------


def test_call_claude_code_cli_not_installed(client):
    with mock.patch(
        "kompany.llm.claude_code.subprocess.run",
        side_effect=FileNotFoundError("claude"),
    ):
        with pytest.raises(RuntimeError, match="not found on PATH"):
            client._call_claude_code("claude-code:sonnet", "sys", "hi", 64)


def test_call_claude_code_nonzero_exit_includes_stderr(client):
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=1,
        stdout="",
        stderr="Invalid API key. Please run /login",
    )
    with mock.patch(
        "kompany.llm.claude_code.subprocess.run", return_value=completed
    ):
        with pytest.raises(RuntimeError, match="exited 1.*Invalid API key"):
            client._call_claude_code("claude-code:sonnet", "sys", "hi", 64)


def test_call_claude_code_timeout(client):
    with mock.patch(
        "kompany.llm.claude_code.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=90),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            client._call_claude_code("claude-code:sonnet", "sys", "hi", 64)


def test_call_claude_code_non_json_output(client):
    completed = subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout="not json at all", stderr="",
    )
    with mock.patch(
        "kompany.llm.claude_code.subprocess.run", return_value=completed
    ):
        with pytest.raises(RuntimeError, match="non-JSON"):
            client._call_claude_code("claude-code:sonnet", "sys", "hi", 64)


# ---------------------------------------------------------------------------
# End-to-end through call(): dispatch + cost recording
# ---------------------------------------------------------------------------


def test_call_dispatches_claude_code_and_records_cost():
    class FakeCostTracker:
        def __init__(self):
            self.calls = []

        def record(self, **kwargs):
            self.calls.append(kwargs)
            return 0.001

    tracker = FakeCostTracker()
    c = LLMClient(settings=FakeSettings(), cost_tracker=tracker)
    completed = subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout=json.dumps(_cli_payload()),
        stderr="",
    )
    with mock.patch(
        "kompany.llm.claude_code.subprocess.run", return_value=completed
    ):
        resp = c.call("claude-code:sonnet", "sys", "hi", agent_name="CEO")

    assert resp.text == "pong"
    assert resp.cost_usd == 0.001
    assert len(tracker.calls) == 1
    assert tracker.calls[0]["model"] == "claude-code:sonnet"
    assert tracker.calls[0]["input_tokens"] == 12
    assert tracker.calls[0]["output_tokens"] == 3


# ---------------------------------------------------------------------------
# Pricing: exact-match rows (spec: agents/llm-calls.md "Adding a New Provider")
# ---------------------------------------------------------------------------


def test_claude_code_pricing_has_exact_rows():
    # Without exact rows, "claude-code:opus" would hit the "claude-"
    # prefix fallback and be silently billed at Sonnet-tier rates.
    from kompany.llm.models import estimate_cost

    mtok = 1_000_000
    assert estimate_cost("claude-code:opus", mtok, 0) == pytest.approx(15.0)
    assert estimate_cost("claude-code:opus", 0, mtok) == pytest.approx(75.0)
    assert estimate_cost("claude-code:sonnet", mtok, 0) == pytest.approx(3.0)
    assert estimate_cost("claude-code:haiku", mtok, 0) == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# Onboarding ping helper
# ---------------------------------------------------------------------------


def test_ping_claude_code_binary_missing():
    from kompany.installer import onboard

    with mock.patch("shutil.which", return_value=None):
        detail = onboard._ping_claude_code()
    assert detail is not None
    assert "not found on PATH" in detail


def test_ping_claude_code_success():
    from kompany.installer import onboard

    completed = subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout=json.dumps(_cli_payload()),
        stderr="",
    )
    with mock.patch("shutil.which", return_value="/usr/local/bin/claude"):
        with mock.patch("subprocess.run", return_value=completed):
            assert onboard._ping_claude_code() is None


def test_ping_claude_code_nonzero_exit():
    from kompany.installer import onboard

    completed = subprocess.CompletedProcess(
        args=["claude"], returncode=1, stdout="",
        stderr="Invalid API key. Please run /login",
    )
    with mock.patch("shutil.which", return_value="/usr/local/bin/claude"):
        with mock.patch("subprocess.run", return_value=completed):
            detail = onboard._ping_claude_code()
    assert detail is not None
    assert "exited 1" in detail
    assert "Invalid API key" in detail
