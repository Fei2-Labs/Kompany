"""Unit tests for the headless ``onboard_headless`` pure function.

This is the function the REST endpoint and Tauri shell call. It
collapses the interactive wizard's branching into a deterministic
contract: pass everything in, get an ``OnboardResult`` back, or an
``OnboardError`` raised. No typer / rich anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from kompany.installer import (
    OnboardError,
    OnboardResult,
    is_onboarded,
    onboard_headless,
)
from kompany.installer.onboard import PROVIDER_ENV_VARS


# ---------------------------------------------------------------------------
# Fakes — mirrors the seam used by test_onboard.py.
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
        self.calls: list[tuple[str, bool]] = []

    def is_applied(self) -> str | None:
        return self._applied


class _FakeDirectiveOutcome:
    def __init__(self, status: str = "ok", message: str = "ack") -> None:
        self.status = status
        self.message = message


class _FakeEngine:
    def __init__(
        self,
        *,
        vault_key: str = "fake-vault-key",
        applied_template: str | None = None,
        directive_raises: Exception | None = None,
        template_raises: Exception | None = None,
    ) -> None:
        self.settings = _FakeSettings(vault_key=vault_key)
        self.credentials = _FakeCredentials()
        self.templates = _FakeTemplates(applied=applied_template)
        self.apply_calls: list[tuple[str, bool]] = []
        self._template_raises = template_raises
        self._directive_raises = directive_raises
        self.process_directive_calls: list[str] = []

    def apply_template(self, template_id: str, force: bool = False) -> dict[str, Any]:
        if self._template_raises is not None:
            raise self._template_raises
        if template_id == "nonexistent":
            raise ValueError(f"template not found: {template_id!r}")
        self.apply_calls.append((template_id, force))
        self.templates._applied = template_id
        return {"template_id": template_id}

    def process_directive(self, text: str) -> _FakeDirectiveOutcome:
        self.process_directive_calls.append(text)
        if self._directive_raises is not None:
            raise self._directive_raises
        return _FakeDirectiveOutcome()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("KOMPANY_DATA_DIR", raising=False)
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "kompany-data"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_headless_happy_path_writes_vault_and_applies_template(data_dir: Path) -> None:
    engine = _FakeEngine()
    result = onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="sk-ant-fake",
        template_id="blank",
        engine_factory=lambda: engine,
    )
    assert isinstance(result, OnboardResult)
    assert result.status == "completed"
    assert result.provider == "anthropic"
    assert result.template_id == "blank"
    assert result.api_key_storage == "vault"
    assert result.ping_status == "skipped_test_mode"
    assert engine.credentials.store == {"anthropic_api_key": "sk-ant-fake"}
    assert engine.apply_calls == [("blank", False)]


def test_headless_with_directive_invokes_engine(data_dir: Path) -> None:
    engine = _FakeEngine()
    result = onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="fake",
        template_id="saas-startup",
        directive="Launch Discord",
        engine_factory=lambda: engine,
    )
    assert engine.process_directive_calls == ["Launch Discord"]
    assert result.directive_text == "Launch Discord"
    assert result.directive_status == "ok"


def test_headless_directive_failure_is_captured(data_dir: Path) -> None:
    engine = _FakeEngine(directive_raises=RuntimeError("LLM blew up"))
    result = onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="fake",
        template_id="blank",
        directive="Do a thing",
        engine_factory=lambda: engine,
    )
    # The pure function must complete even if the first directive fails;
    # the install itself succeeded.
    assert result.status == "completed"
    assert result.directive_status == "error"
    assert "LLM blew up" in result.directive_message


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_missing_api_key_raises(data_dir: Path) -> None:
    with pytest.raises(OnboardError) as excinfo:
        onboard_headless(
            data_dir=data_dir,
            provider="anthropic",
            api_key="",
            template_id="blank",
            engine_factory=lambda: _FakeEngine(),
        )
    assert excinfo.value.code == "missing_api_key"


def test_missing_provider_raises(data_dir: Path) -> None:
    with pytest.raises(OnboardError) as excinfo:
        onboard_headless(
            data_dir=data_dir,
            provider="",
            api_key="x",
            template_id="blank",
            engine_factory=lambda: _FakeEngine(),
        )
    assert excinfo.value.code == "missing_provider"


def test_unknown_provider_raises(data_dir: Path) -> None:
    with pytest.raises(OnboardError) as excinfo:
        onboard_headless(
            data_dir=data_dir,
            provider="bogus",
            api_key="x",
            template_id="blank",
            engine_factory=lambda: _FakeEngine(),
        )
    assert excinfo.value.code == "unknown_provider"


def test_missing_template_raises(data_dir: Path) -> None:
    with pytest.raises(OnboardError) as excinfo:
        onboard_headless(
            data_dir=data_dir,
            provider="anthropic",
            api_key="x",
            template_id="",
            engine_factory=lambda: _FakeEngine(),
        )
    assert excinfo.value.code == "missing_template"


def test_unknown_template_raises(data_dir: Path) -> None:
    with pytest.raises(OnboardError) as excinfo:
        onboard_headless(
            data_dir=data_dir,
            provider="anthropic",
            api_key="x",
            template_id="nonexistent",
            engine_factory=lambda: _FakeEngine(),
        )
    assert excinfo.value.code == "template_error"


# ---------------------------------------------------------------------------
# Reuse path
# ---------------------------------------------------------------------------


def _seed_install(dir_: Path, template_id: str = "saas-startup") -> None:
    """Create a kompany.db that looks like a completed install."""
    import sqlite3

    dir_.mkdir(parents=True, exist_ok=True)
    db = dir_ / "kompany.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE company_config (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE credential_vault (name TEXT PRIMARY KEY, ciphertext TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO company_config (key, value) VALUES ('template_id', ?)",
        (template_id,),
    )
    conn.execute(
        "INSERT INTO credential_vault (name, ciphertext, updated_at) "
        "VALUES ('anthropic_api_key', 'ciphertext', '2026-05-19')"
    )
    conn.commit()
    conn.close()


def test_reuse_path_does_not_overwrite_existing_install(data_dir: Path) -> None:
    _seed_install(data_dir, template_id="saas-startup")
    engine = _FakeEngine(applied_template="saas-startup")
    result = onboard_headless(
        data_dir=data_dir,
        provider="anthropic",
        api_key="new-key-that-should-be-ignored",
        template_id="indie-tool",  # different from applied
        engine_factory=lambda: engine,
    )
    assert result.status == "reused"
    assert result.template_id == "saas-startup"
    # No vault writes — the reuse path must not clobber stored creds.
    assert engine.credentials.store == {}
    # No template applies either.
    assert engine.apply_calls == []
    assert result.api_key_storage == "reused"


# ---------------------------------------------------------------------------
# is_onboarded helper
# ---------------------------------------------------------------------------


def test_is_onboarded_false_for_fresh_dir(data_dir: Path) -> None:
    snap = is_onboarded(data_dir)
    assert snap == {"onboarded": False, "template_id": None, "provider": None}


def test_is_onboarded_true_after_install(data_dir: Path) -> None:
    _seed_install(data_dir, template_id="indie-tool")
    snap = is_onboarded(data_dir)
    assert snap["onboarded"] is True
    assert snap["template_id"] == "indie-tool"
    assert snap["provider"] == "anthropic"


def test_is_onboarded_handles_partial_install(data_dir: Path) -> None:
    # Stale artefact but no kompany.db → not onboarded.
    data_dir.mkdir(parents=True)
    (data_dir / "stale.txt").write_text("orphan")
    snap = is_onboarded(data_dir)
    assert snap["onboarded"] is False
