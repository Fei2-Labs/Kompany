"""Tests for the four target knobs across CLI flag / REST body / template default.

Mission-targets task 05-19. Each test isolates one decision branch:

* Explicit ``initial_budget`` / ``revenue_target`` / ... beat the template manifest.
* Manifest defaults beat the all-zeros fallback.
* Empty/None values leave the manifest defaults intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from kompany.installer import OnboardResult, onboard_headless
from kompany.installer.onboard import PROVIDER_ENV_VARS


# ---------------------------------------------------------------------------
# Fakes — mirror the seam used by test_onboard_headless.py
# ---------------------------------------------------------------------------


class _FakeCredentials:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        self.store[name] = value

    def get(self, name: str) -> str | None:
        return self.store.get(name)


@dataclass
class _FakeSettings:
    vault_key: str = "fake-vault-key"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    glm_api_key: str = ""
    kimi_api_key: str = ""
    custom_api_key: str = ""
    model_economy: str = "claude-haiku-4-20250414"


class _FakeTemplates:
    def __init__(self, applied: str | None = None) -> None:
        self._applied = applied

    def is_applied(self) -> str | None:
        return self._applied


class _FakeEngine:
    """Captures every apply_template call so tests can assert the override
    contract without going through the real Pydantic + filesystem path."""

    def __init__(self) -> None:
        self.settings = _FakeSettings()
        self.credentials = _FakeCredentials()
        self.templates = _FakeTemplates()
        self.apply_calls: list[dict[str, Any]] = []
        self.review_calls: int = 0

    def apply_template(self, template_id: str, **kwargs: Any) -> dict[str, Any]:
        self.apply_calls.append({"template_id": template_id, **kwargs})
        self.templates._applied = template_id
        return {"template_id": template_id}

    def process_directive(self, text: str) -> Any:
        class _Out:
            status = "ok"
            message = "ack"

        return _Out()

    def run_target_feasibility_review(self) -> dict[str, Any]:
        self.review_calls += 1
        return {"id": "apr_fake", "status": "pending"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("KOMPANY_DATA_DIR", raising=False)
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "kompany-data"


# ---------------------------------------------------------------------------
# Headless flow — explicit overrides win
# ---------------------------------------------------------------------------


def test_explicit_budget_passes_through_as_override(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        initial_budget=12345.0,
        engine_factory=lambda: engine,
    )
    assert engine.apply_calls
    assert engine.apply_calls[0]["override_budget"] == 12345.0


def test_explicit_revenue_target_passes_through(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        revenue_target=8000.0,
        engine_factory=lambda: engine,
    )
    assert engine.apply_calls[0]["override_revenue_target"] == 8000.0


def test_explicit_customer_target_passes_through(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        customer_target=42,
        engine_factory=lambda: engine,
    )
    assert engine.apply_calls[0]["override_customer_target"] == 42


def test_explicit_deadline_passes_through(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        deadline="2026-09-30",
        engine_factory=lambda: engine,
    )
    assert engine.apply_calls[0]["override_deadline"] == "2026-09-30"


def test_all_four_passes_through_together(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="indie-tool",
        initial_budget=2000.0,
        revenue_target=4000.0,
        customer_target=100,
        deadline="2026-12-01",
        engine_factory=lambda: engine,
    )
    call = engine.apply_calls[0]
    assert call["override_budget"] == 2000.0
    assert call["override_revenue_target"] == 4000.0
    assert call["override_customer_target"] == 100
    assert call["override_deadline"] == "2026-12-01"


def test_no_overrides_leaves_apply_call_clean(data_dir: Path) -> None:
    """When nothing is passed, the engine receives just force flag — letting
    the template manifest fill the slots."""
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        engine_factory=lambda: engine,
    )
    call = engine.apply_calls[0]
    assert "override_budget" not in call
    assert "override_revenue_target" not in call
    assert "override_customer_target" not in call
    assert "override_deadline" not in call


def test_feasibility_review_fires_after_onboarding(data_dir: Path) -> None:
    """A fresh onboard must kick off one review and surface the id."""
    engine = _FakeEngine()
    result = onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        engine_factory=lambda: engine,
    )
    assert isinstance(result, OnboardResult)
    assert engine.review_calls == 1
    assert result.targets_review_id == "apr_fake"


def test_feasibility_review_failure_is_non_fatal(data_dir: Path) -> None:
    """Onboarding still succeeds when ``run_target_feasibility_review`` raises."""
    engine = _FakeEngine()

    def boom() -> dict[str, Any]:
        raise RuntimeError("LLM offline")

    engine.run_target_feasibility_review = boom  # type: ignore[method-assign]
    result = onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        engine_factory=lambda: engine,
    )
    assert result.status == "completed"
    assert any("review skipped" in n for n in result.notes)


# ---------------------------------------------------------------------------
# REST body — verifies the Pydantic request model accepts the 4 fields
# ---------------------------------------------------------------------------


def test_rest_request_model_accepts_four_fields() -> None:
    from kompany.interfaces.api import OnboardingCompleteRequest

    req = OnboardingCompleteRequest(
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        initial_budget=2500.0,
        revenue_target=8000.0,
        customer_target=42,
        deadline="2026-09-30",
    )
    assert req.initial_budget == 2500.0
    assert req.revenue_target == 8000.0
    assert req.customer_target == 42
    assert req.deadline == "2026-09-30"


def test_rest_request_model_omits_targets_when_absent() -> None:
    from kompany.interfaces.api import OnboardingCompleteRequest

    req = OnboardingCompleteRequest(
        provider="anthropic", api_key="sk", template_id="blank",
    )
    assert req.initial_budget is None
    assert req.revenue_target is None
    assert req.customer_target is None
    assert req.deadline is None


def test_rest_request_rejects_negative_revenue() -> None:
    from kompany.interfaces.api import OnboardingCompleteRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OnboardingCompleteRequest(
            provider="anthropic",
            api_key="sk",
            template_id="blank",
            revenue_target=-1.0,
        )


# ---------------------------------------------------------------------------
# Template default propagation — real Templates service, real db
# ---------------------------------------------------------------------------


def _build_real_engine(tmp_path: Path) -> Any:
    """Spin up a real engine on a temp data dir for the template-default test."""
    import os

    os.environ["KOMPANY_DATA_DIR"] = str(tmp_path)
    from kompany.core.engine import KompanyEngine

    return KompanyEngine()


def test_template_manifest_revenue_target_lands_in_company_config(tmp_path: Path) -> None:
    """Without overrides, the saas-startup manifest's revenue_target ends up
    in the company_config table for downstream readers."""
    engine = _build_real_engine(tmp_path)
    engine.apply_template("saas-startup")
    targets = engine.get_targets()
    # Manifest preset for saas-startup is 10000.
    assert targets.revenue_target == 10000.0
    assert targets.customer_target == 50


def test_explicit_override_beats_manifest(tmp_path: Path) -> None:
    """``--revenue-target 5000`` overrides the manifest's 10000 preset."""
    engine = _build_real_engine(tmp_path)
    engine.apply_template(
        "saas-startup",
        override_revenue_target=5000.0,
        override_customer_target=20,
        override_deadline="2099-01-01",
    )
    targets = engine.get_targets()
    assert targets.revenue_target == 5000.0
    assert targets.customer_target == 20
    assert targets.deadline is not None and targets.deadline.startswith("2099-01-01")
