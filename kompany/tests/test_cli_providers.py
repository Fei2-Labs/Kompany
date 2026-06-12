"""Tests for the generic CLI providers (codex / opencode, issue #18).

Mirrors test_claude_code_provider.py: fake subprocess, canned JSONL
shapes from the multi-backend-runners research doc.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from kompany.config.model_source import ModelSource
from kompany.llm.client import LLMClient
from kompany.llm.cli_providers import run_cli_completion
from kompany.llm.cost_tracker import CostTracker
from kompany.llm.models import PRICING, estimate_cost
from kompany.llm.providers import Provider, detect_provider
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory


class FakeSettings:
    """Minimal settings for routing tests."""

    anthropic_api_key = ""
    openai_api_key = ""
    gemini_api_key = ""
    glm_api_key = ""
    kimi_api_key = ""
    custom_api_key = ""
    custom_base_url = ""
    model_source = None

    def get_api_key_for_provider(self, provider: str) -> str:
        if provider in ("claude_code", "codex_cli", "opencode_cli"):
            return "no-key-required"
        return ""


@pytest.fixture
def client():
    return LLMClient(settings=FakeSettings(), cost_tracker=None)


def _completed(cli: str, stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[cli], returncode=returncode, stdout=stdout, stderr=stderr
    )


# Canned JSONL shapes (research doc: codex exec --json, opencode run
# --format json).
CODEX_JSONL = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "t-1"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "pong"},
    }),
    json.dumps({
        "type": "turn.completed",
        "usage": {
            "input_tokens": 24763,
            "cached_input_tokens": 24448,
            "output_tokens": 122,
            "reasoning_output_tokens": 0,
        },
    }),
])

OPENCODE_JSONL = "\n".join([
    json.dumps({"type": "step_start", "sessionID": "s-1"}),
    json.dumps({"type": "text", "sessionID": "s-1", "part": {"text": "pong"}}),
    json.dumps({
        "type": "step_finish",
        "sessionID": "s-1",
        "part": {
            "reason": "stop",
            "cost": 0.0021,
            "tokens": {"input": 40, "output": 7, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}},
        },
    }),
])


# ---------------------------------------------------------------------------
# Provider detection / routing
# ---------------------------------------------------------------------------


def test_detect_cli_providers():
    assert detect_provider("codex:gpt-5") == Provider.CODEX_CLI
    assert detect_provider("codex:gpt-5-mini") == Provider.CODEX_CLI
    assert detect_provider("opencode:openai/gpt-5") == Provider.OPENCODE_CLI
    assert detect_provider("opencode:zhipuai/glm-4.6") == Provider.OPENCODE_CLI


def test_detect_generic_prefixes_unaffected():
    # The new "<cli>:" prefixes must not shadow existing API models.
    assert detect_provider("gpt-4o") == Provider.OPENAI
    assert detect_provider("claude-code:sonnet") == Provider.CLAUDE_CODE
    assert detect_provider("claude-sonnet-4-20250514") == Provider.ANTHROPIC


def test_resolve_cli_provider_beats_custom_base_url():
    settings = FakeSettings()
    settings.custom_base_url = "https://my-endpoint.example.com/v1"
    c = LLMClient(settings=settings, cost_tracker=None)
    assert c._resolve_provider("codex:gpt-5") == Provider.CODEX_CLI
    assert c._resolve_provider("opencode:openai/gpt-5") == Provider.OPENCODE_CLI


def test_sentinel_api_key_path():
    s = FakeSettings()
    assert s.get_api_key_for_provider("codex_cli") == "no-key-required"
    assert s.get_api_key_for_provider("opencode_cli") == "no-key-required"
    # And the real settings class carries the same sentinel.
    from kompany.config.settings import KompanySettings

    real = KompanySettings()
    assert real.get_api_key_for_provider("codex_cli") == "no-key-required"
    assert real.get_api_key_for_provider("opencode_cli") == "no-key-required"


# ---------------------------------------------------------------------------
# codex adapter
# ---------------------------------------------------------------------------


def test_codex_happy_path():
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        return_value=_completed("codex", CODEX_JSONL),
    ) as run:
        text, tin, tout = run_cli_completion(
            "codex", "codex:gpt-5", "be terse", "ping", 60.0
        )
    assert (text, tin, tout) == ("pong", 24763, 122)
    cmd = run.call_args.args[0]
    assert cmd[:3] == ["codex", "exec", "--json"]
    assert cmd[cmd.index("--model") + 1] == "gpt-5"
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in cmd
    assert "--ignore-user-config" in cmd
    assert "--ignore-rules" in cmd
    # System prompt folded into the prompt (no system flag in exec mode).
    assert cmd[-1] == "be terse\n\nping"


def test_codex_nonzero_exit_includes_stderr_tail():
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        return_value=_completed("codex", "", returncode=1, stderr="login required"),
    ):
        with pytest.raises(RuntimeError, match="exited 1.*login required"):
            run_cli_completion("codex", "codex:gpt-5", "s", "p", 60.0)


def test_codex_timeout():
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=60),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            run_cli_completion("codex", "codex:gpt-5", "s", "p", 60.0)


def test_codex_not_installed():
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        side_effect=FileNotFoundError("codex"),
    ):
        with pytest.raises(RuntimeError, match="not found on PATH"):
            run_cli_completion("codex", "codex:gpt-5", "s", "p", 60.0)


def test_codex_no_agent_message_raises():
    out = json.dumps({"type": "turn.failed", "error": "boom"})
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        return_value=_completed("codex", out),
    ):
        with pytest.raises(RuntimeError, match="no completion text"):
            run_cli_completion("codex", "codex:gpt-5", "s", "p", 60.0)


# ---------------------------------------------------------------------------
# opencode adapter
# ---------------------------------------------------------------------------


def test_opencode_happy_path():
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        return_value=_completed("opencode", OPENCODE_JSONL),
    ) as run:
        text, tin, tout = run_cli_completion(
            "opencode", "opencode:openai/gpt-5", "be terse", "ping", 60.0
        )
    assert (text, tin, tout) == ("pong", 40, 7)
    cmd = run.call_args.args[0]
    assert cmd[:2] == ["opencode", "run"]
    # provider/model passthrough
    assert cmd[cmd.index("--model") + 1] == "openai/gpt-5"
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd[2] == "be terse\n\nping"
    # Hygiene env: no founder CLAUDE.md/skills leakage, tools denied.
    env = run.call_args.kwargs["env"]
    assert env["OPENCODE_DISABLE_CLAUDE_CODE"] == "true"
    assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "true"
    perm = json.loads(env["OPENCODE_PERMISSION"])
    assert perm["bash"] == "deny"
    assert perm["edit"] == "deny"


def test_opencode_nonzero_exit():
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        return_value=_completed("opencode", "", returncode=2, stderr="no auth"),
    ):
        with pytest.raises(RuntimeError, match="exited 2.*no auth"):
            run_cli_completion("opencode", "opencode:openai/gpt-5", "s", "p", 60.0)


def test_opencode_timeout():
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="opencode", timeout=60),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            run_cli_completion("opencode", "opencode:openai/gpt-5", "s", "p", 60.0)


def test_unknown_cli_rejected():
    with pytest.raises(RuntimeError, match="unknown CLI provider"):
        run_cli_completion("aider", "aider:x", "s", "p", 60.0)


# ---------------------------------------------------------------------------
# End-to-end through LLMClient.call(): dispatch + ledger (spec rule 6)
# ---------------------------------------------------------------------------


def _world(tmp_path, source: ModelSource | None = None):
    db = Database(tmp_path)
    ledger = Ledger(db)
    ledger.record(amount=100.0, description="Capital", category=LedgerCategory.INCOME)
    tracker = CostTracker(
        ledger, settings=SimpleNamespace(model_source=source)
    )
    return ledger, tracker


@pytest.mark.parametrize(
    ("model", "stdout", "expected_text"),
    [
        ("codex:gpt-5", CODEX_JSONL, "pong"),
        ("opencode:openai/gpt-5", OPENCODE_JSONL, "pong"),
    ],
)
def test_call_records_real_ledger_cost(tmp_path, model, stdout, expected_text):
    ledger, tracker = _world(tmp_path)
    c = LLMClient(settings=FakeSettings(), cost_tracker=tracker)
    cli = model.split(":", 1)[0]
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        return_value=_completed(cli, stdout),
    ):
        resp = c.call(model, "sys", "hi", agent_name="CEO")
    assert resp.text == expected_text
    assert resp.cost_usd > 0
    # The spec rule: ledger must have decreased after the call.
    assert ledger.get_balance() == pytest.approx(100.0 - resp.cost_usd)


def test_codex_shadow_under_openai_subscription(tmp_path):
    """codex:* calls under an openai_subscription source are shadow-only."""
    source = ModelSource(kind="openai_subscription", monthly_fee_usd=20.0)
    assert source.is_subscription_call("codex:gpt-5")
    assert not source.is_subscription_call("gpt-5")
    assert not source.is_subscription_call("claude-code:sonnet")

    ledger, tracker = _world(tmp_path, source)
    c = LLMClient(settings=FakeSettings(), cost_tracker=tracker)
    with mock.patch(
        "kompany.llm.cli_providers.subprocess.run",
        return_value=_completed("codex", CODEX_JSONL),
    ):
        resp = c.call("codex:gpt-5", "sys", "hi", agent_name="CEO")
    assert resp.cost_usd == 0.0
    assert ledger.get_balance() == pytest.approx(100.0)


def test_claude_code_not_shadowed_under_openai_subscription():
    source = ModelSource(kind="openai_subscription", monthly_fee_usd=20.0)
    assert not source.is_subscription_call("claude-code:sonnet")


# ---------------------------------------------------------------------------
# Pricing rows (spec: "Adding a New Provider" step 5)
# ---------------------------------------------------------------------------


def test_pricing_exact_rows_exist():
    for model in ("codex:gpt-5", "codex:gpt-5-mini", "opencode:openai/gpt-5"):
        assert model in PRICING


def test_pricing_prefix_fallbacks():
    # Unknown codex model → gpt-5-tier fallback, not Sonnet default.
    assert estimate_cost("codex:gpt-6-preview", 1_000_000, 0) == pytest.approx(1.25)
    # Unknown opencode model → conservative Sonnet-tier.
    assert estimate_cost("opencode:foo/bar", 1_000_000, 0) == pytest.approx(3.0)
