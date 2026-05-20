"""Tests for the onboard-v2 ``glossary_overrides`` knob.

PRD: ``.trellis/tasks/05-19-onboard-v2-flow/prd.md``. The wizard's
Mission Briefing step lets the founder inline-edit the template's
default glossary; the edits ride along ``POST /onboarding/complete``
in ``OnboardingCompleteRequest.glossary_overrides`` and land in
``onboard_headless`` which forwards them onto ``engine.glossary``.

We test three layers:

1. The Pydantic request model accepts the field + rejects bad shapes.
2. ``onboard_headless`` invokes the glossary service in the right order
   (template apply first, then overrides).
3. End-to-end against a real engine, an overridden term's new
   definition is the one a downstream reader gets back.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kompany.installer import onboard_headless
from kompany.installer.onboard import PROVIDER_ENV_VARS


# ---------------------------------------------------------------------------
# Fakes — same seam used by test_onboard_targets.py
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


@dataclass
class _RecordedGlossaryCall:
    op: str
    term: str
    definition: str | None = None


class _FakeGlossaryService:
    def __init__(self) -> None:
        # Pre-seeded with one term to model the template's bulk_install:
        # the founder editing "customer" should call update(), not add().
        self.terms: dict[str, str] = {"customer": "template default for customer"}
        self.calls: list[_RecordedGlossaryCall] = []

    def get(self, term: str) -> Any:
        if term in self.terms:
            # Loose stub — only needs ``term`` attr.
            class _E:
                pass

            e = _E()
            e.term = term  # type: ignore[attr-defined]
            e.definition = self.terms[term]  # type: ignore[attr-defined]
            return e
        return None

    def update(self, term: str, *, definition: str | None = None, **_: Any) -> Any:
        if definition is not None:
            self.terms[term] = definition
        self.calls.append(_RecordedGlossaryCall("update", term, definition))
        return self.get(term)

    def add(self, *, term: str, definition: str, **_: Any) -> Any:
        self.terms[term] = definition
        self.calls.append(_RecordedGlossaryCall("add", term, definition))
        return self.get(term)


class _FakeEngine:
    def __init__(self) -> None:
        self.settings = _FakeSettings()
        self.credentials = _FakeCredentials()
        self.templates = _FakeTemplates()
        self.glossary = _FakeGlossaryService()
        self.apply_calls: list[dict[str, Any]] = []

    def apply_template(self, template_id: str, **kwargs: Any) -> dict[str, Any]:
        self.apply_calls.append({"template_id": template_id, **kwargs})
        self.templates._applied = template_id
        return {"template_id": template_id}

    def run_target_feasibility_review(self) -> dict[str, Any]:
        return {"id": "apr_fake"}

    def process_directive(self, text: str) -> Any:
        class _Out:
            status = "ok"
            message = "ack"

        return _Out()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
# 1. Pydantic request model
# ---------------------------------------------------------------------------


def test_request_model_accepts_glossary_overrides() -> None:
    from kompany.interfaces.api import OnboardingCompleteRequest

    req = OnboardingCompleteRequest(
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        glossary_overrides={"customer": "a paying buyer", "MRR": "monthly subscription run-rate"},
    )
    assert req.glossary_overrides is not None
    assert req.glossary_overrides["customer"] == "a paying buyer"


def test_request_model_glossary_overrides_optional() -> None:
    from kompany.interfaces.api import OnboardingCompleteRequest

    req = OnboardingCompleteRequest(
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
    )
    assert req.glossary_overrides is None


# ---------------------------------------------------------------------------
# 2. onboard_headless wiring — overrides land on engine.glossary
# ---------------------------------------------------------------------------


def test_glossary_overrides_update_existing_term(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        glossary_overrides={"customer": "buyer with active subscription"},
        engine_factory=lambda: engine,
    )
    assert engine.glossary.terms["customer"] == "buyer with active subscription"
    assert any(
        c.op == "update" and c.term == "customer" for c in engine.glossary.calls
    )


def test_glossary_overrides_add_new_term(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        glossary_overrides={"design_partner": "a customer who shapes the product"},
        engine_factory=lambda: engine,
    )
    assert engine.glossary.terms["design_partner"] == "a customer who shapes the product"
    assert any(
        c.op == "add" and c.term == "design_partner" for c in engine.glossary.calls
    )


def test_glossary_overrides_skipped_when_none(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        engine_factory=lambda: engine,
    )
    assert engine.glossary.calls == []


def test_glossary_overrides_skipped_when_empty_dict(data_dir: Path) -> None:
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        glossary_overrides={},
        engine_factory=lambda: engine,
    )
    assert engine.glossary.calls == []


def test_glossary_overrides_ignore_blank_values(data_dir: Path) -> None:
    """Empty-string definitions must not blow away a real term."""
    engine = _FakeEngine()
    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        glossary_overrides={"customer": ""},
        engine_factory=lambda: engine,
    )
    # Original value preserved; no update call should have fired.
    assert engine.glossary.terms["customer"] == "template default for customer"
    assert engine.glossary.calls == []


def test_glossary_failure_is_non_fatal(data_dir: Path) -> None:
    """A glossary write that raises must not abort the onboard."""
    engine = _FakeEngine()

    def boom(*_args: Any, **_kw: Any) -> Any:
        raise RuntimeError("glossary table is locked")

    engine.glossary.update = boom  # type: ignore[assignment]
    result = onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk",
        template_id="saas-startup",
        glossary_overrides={"customer": "buyer"},
        engine_factory=lambda: engine,
    )
    assert result.status == "completed"
    assert any("glossary overrides skipped" in n for n in result.notes)


# ---------------------------------------------------------------------------
# 3. End-to-end via real engine + Pydantic request → SQLite glossary
# ---------------------------------------------------------------------------


def test_real_engine_glossary_override_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the template-installed 'customer' definition, then read it
    back through the same GlossaryService. The override beats the template
    default.

    We let ``onboard_headless`` construct the engine via the engine_factory
    closure so the existing-install-state check sees a fresh data dir and
    runs the full template-apply + override path (the alternative — building
    the engine first — looks like a reused install and skips overrides).
    """
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    data_dir = tmp_path / "fresh"

    holder: dict[str, object] = {}

    def make_engine() -> object:
        from kompany.core.engine import KompanyEngine

        eng = KompanyEngine()
        holder["engine"] = eng
        return eng

    onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk-test",
        template_id="saas-startup",
        glossary_overrides={"customer": "a paying buyer with an active account"},
        engine_factory=make_engine,
    )
    engine = holder["engine"]
    # The GlossaryService should now return the founder's text.
    entry = engine.glossary.get("customer")  # type: ignore[attr-defined]
    assert entry is not None
    assert entry.definition == "a paying buyer with an active account"
