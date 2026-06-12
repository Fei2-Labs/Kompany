"""Onboarding model-source step (06-11-harness-execution-leg PR5b).

Covers the new Step 2.5 helpers in ``installer/onboard.py``: agent-CLI
detection is best-effort (absent or crashing probes never break
onboarding), choosing a detected subscription yields a valid
ModelSource payload, headless / ``--yes`` / explicit ``--provider``
always take the API-key (``custom_api``) path, and the persist helper
degrades to a note instead of raising.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from rich.console import Console

from kompany.installer import OnboardResult
from kompany.installer.onboard import (
    _choose_model_source,
    _detected_subscription_kinds,
    _write_model_source,
)


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False)


# ---------------------------------------------------------------------------
# _detected_subscription_kinds
# ---------------------------------------------------------------------------


def test_detected_kinds_filters_to_found_subscriptions(monkeypatch) -> None:
    monkeypatch.setattr(
        "kompany.core.model_source_ops.detect_agent_clis",
        lambda **kw: {
            "claude": {
                "found": True,
                "path": "/x/claude",
                "version": "2.1.173",
                "source_kind": "claude_subscription",
            },
            "codex": {
                "found": False,
                "path": None,
                "version": None,
                "source_kind": "openai_subscription",
            },
            # custom_api is API-key, never a zero-key subscription choice.
            "opencode": {
                "found": True,
                "path": "/x/oc",
                "version": None,
                "source_kind": "custom_api",
            },
        },
    )
    detected = _detected_subscription_kinds()
    assert set(detected) == {"claude_subscription"}
    assert detected["claude_subscription"]["cli"] == "claude"


# ---------------------------------------------------------------------------
# _choose_model_source
# ---------------------------------------------------------------------------


def test_headless_yes_and_provider_flag_skip_detection(monkeypatch) -> None:
    def _boom() -> dict[str, Any]:
        raise AssertionError("detection must not run in headless mode")

    monkeypatch.setattr(
        "kompany.installer.onboard._detected_subscription_kinds", _boom
    )
    assert _choose_model_source(_console(), yes=True, provider_flag=None) == (
        "custom_api",
        None,
    )
    assert _choose_model_source(
        _console(), yes=False, provider_flag="anthropic"
    ) == ("custom_api", None)


def test_detection_failure_degrades_to_api_key_path(monkeypatch) -> None:
    def _boom() -> dict[str, Any]:
        raise OSError("PATH probe exploded")

    monkeypatch.setattr(
        "kompany.installer.onboard._detected_subscription_kinds", _boom
    )
    # Best-effort: a crashing probe never breaks onboarding.
    assert _choose_model_source(_console(), yes=False, provider_flag=None) == (
        "custom_api",
        None,
    )


def test_no_detected_clis_keeps_api_key_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "kompany.installer.onboard._detected_subscription_kinds", lambda: {}
    )
    assert _choose_model_source(_console(), yes=False, provider_flag=None) == (
        "custom_api",
        None,
    )


def test_picking_detected_subscription_returns_kind_and_fee(monkeypatch) -> None:
    monkeypatch.setattr(
        "kompany.installer.onboard._detected_subscription_kinds",
        lambda: {
            "claude_subscription": {
                "cli": "claude",
                "found": True,
                "version": "2.1.173",
                "source_kind": "claude_subscription",
            }
        },
    )
    answers = iter(["claude-subscription", "25"])
    monkeypatch.setattr(
        "kompany.installer.onboard.Prompt.ask",
        lambda *a, **kw: next(answers),
    )
    assert _choose_model_source(_console(), yes=False, provider_flag=None) == (
        "claude_subscription",
        25.0,
    )


def test_garbage_fee_input_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "kompany.installer.onboard._detected_subscription_kinds",
        lambda: {
            "openai_subscription": {
                "cli": "codex",
                "found": True,
                "version": None,
                "source_kind": "openai_subscription",
            }
        },
    )
    answers = iter(["openai-subscription", "twenty bucks"])
    monkeypatch.setattr(
        "kompany.installer.onboard.Prompt.ask",
        lambda *a, **kw: next(answers),
    )
    kind, fee = _choose_model_source(_console(), yes=False, provider_flag=None)
    assert kind == "openai_subscription"
    assert fee == 20.0


def test_founder_keeps_api_key_despite_detected_clis(monkeypatch) -> None:
    monkeypatch.setattr(
        "kompany.installer.onboard._detected_subscription_kinds",
        lambda: {
            "claude_subscription": {
                "cli": "claude",
                "found": True,
                "version": None,
                "source_kind": "claude_subscription",
            }
        },
    )
    monkeypatch.setattr(
        "kompany.installer.onboard.Prompt.ask", lambda *a, **kw: "api-key"
    )
    assert _choose_model_source(_console(), yes=False, provider_flag=None) == (
        "custom_api",
        None,
    )


# ---------------------------------------------------------------------------
# _write_model_source
# ---------------------------------------------------------------------------


class _RecordingEngine:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any] | None] = []

    def set_model_source(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        self.payloads.append(payload)
        return {"source": payload}


def test_write_model_source_persists_and_marks_result() -> None:
    engine = _RecordingEngine()
    result = OnboardResult(status="completed", data_dir=Path("."))
    _write_model_source(
        _console(),
        engine=engine,
        result=result,
        kind="claude_subscription",
        monthly_fee=20.0,
    )
    assert engine.payloads == [
        {"kind": "claude_subscription", "monthly_fee_usd": 20.0}
    ]
    assert result.model_source_kind == "claude_subscription"


def test_write_model_source_skips_engines_without_setter() -> None:
    result = OnboardResult(status="completed", data_dir=Path("."))
    _write_model_source(  # stub/fake engines (tests, reuse path) are fine
        _console(), engine=object(), result=result, kind="custom_api", monthly_fee=None
    )
    assert result.model_source_kind is None
    assert result.notes == []


def test_write_model_source_failure_becomes_note_not_crash() -> None:
    class _Exploding:
        def set_model_source(self, payload: dict[str, Any] | None) -> None:
            raise ValueError("monthly_fee_usd is required")

    result = OnboardResult(status="completed", data_dir=Path("."))
    _write_model_source(
        _console(),
        engine=_Exploding(),
        result=result,
        kind="claude_subscription",
        monthly_fee=None,
    )
    assert result.model_source_kind is None
    assert any("model source not saved" in note for note in result.notes)
