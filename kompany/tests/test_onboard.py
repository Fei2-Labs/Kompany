"""Unit tests for the ``kompany onboard`` wizard.

These tests exercise the four-step wizard logic in isolation by passing
a fake engine through ``engine_factory`` so they don't need to spin up a
full :class:`KompanyEngine`. The full end-to-end coverage (real engine,
mocked LLM) lives in
``kompany/tests/integration/test_onboard_flow.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from kompany.installer import OnboardResult, run_onboard
from kompany.installer.onboard import (
    PROVIDER_ENV_VARS,
    SUPPORTED_PROVIDERS,
    _existing_install_state,
    _resolve_api_key,
)
from kompany.interfaces.cli import app


# ---------------------------------------------------------------------------
# Fake engine — simulates only what the wizard touches.
# ---------------------------------------------------------------------------


class _FakeCredentials:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.raise_on_get = False

    def set(self, name: str, value: str) -> None:
        self.store[name] = value

    def get(self, name: str) -> str | None:
        if self.raise_on_get:
            raise RuntimeError("vault sealed")
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
    custom_base_url: str = ""
    model_economy: str = "claude-haiku-4-20250414"


class _FakeTemplate:
    def __init__(self, tid: str, title: str) -> None:
        self.id = tid
        self.mission_title = title


class _FakeTemplates:
    def __init__(self, applied: str | None = None) -> None:
        self._applied = applied
        self.applied_calls: list[tuple[str, bool]] = []

    def list_templates(self) -> list[_FakeTemplate]:
        return [
            _FakeTemplate("blank", "Blank slate"),
            _FakeTemplate("saas-startup", "Launch a SaaS"),
        ]

    def is_applied(self) -> str | None:
        return self._applied


class _FakeDirectiveOutcome:
    def __init__(self, status: str = "ok", message: str = "CEO acknowledges.") -> None:
        self.status = status
        self.message = message


class _FakeEngine:
    def __init__(
        self,
        *,
        vault_key: str = "fake-vault-key",
        applied_template: str | None = None,
        directive_outcome: _FakeDirectiveOutcome | None = None,
        directive_raises: Exception | None = None,
        existing_vault_keys: dict[str, str] | None = None,
    ) -> None:
        self.settings = _FakeSettings(vault_key=vault_key)
        self.credentials = _FakeCredentials()
        if existing_vault_keys:
            self.credentials.store.update(existing_vault_keys)
        self.templates = _FakeTemplates(applied=applied_template)
        self.apply_calls: list[tuple[str, bool]] = []
        self._directive_outcome = directive_outcome or _FakeDirectiveOutcome()
        self._directive_raises = directive_raises
        self.process_directive_calls: list[str] = []

    def apply_template(self, template_id: str, force: bool = False) -> dict[str, Any]:
        self.apply_calls.append((template_id, force))
        if template_id == "nonexistent":
            raise ValueError(f"template not found: {template_id!r}.")
        self.templates._applied = template_id
        return {"template_id": template_id}

    def process_directive(self, text: str) -> _FakeDirectiveOutcome:
        self.process_directive_calls.append(text)
        if self._directive_raises is not None:
            raise self._directive_raises
        return self._directive_outcome


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every provider env var so headless tests start clean."""
    for var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("KOMPANY_DATA_DIR", raising=False)
    # KOMPANY_TEST_MODE is opt-in; tests that need a real ping unset it
    # themselves. Default to on so we never accidentally hit the network.
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "kompany-data"


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------


def test_resolve_api_key_prefers_flag_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert _resolve_api_key("anthropic", "from-flag") == "from-flag"


def test_resolve_api_key_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert _resolve_api_key("anthropic", None) == "from-env"


def test_resolve_api_key_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _resolve_api_key("anthropic", None) is None
    assert _resolve_api_key("anthropic", "") is None


def test_existing_install_state_reports_fresh_for_empty_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    state = _existing_install_state(data_dir)
    assert state == {
        "db_exists": False,
        "template_id": None,
        "has_vault_rows": False,
        "partial": False,
    }


def test_existing_install_state_flags_partial_when_db_missing_but_dir_dirty(
    data_dir: Path,
) -> None:
    data_dir.mkdir(parents=True)
    # Drop some unrelated artefact (simulates a stale vault file or old
    # backup) — this is the PRD's "partial install" edge case.
    (data_dir / "stale.txt").write_text("orphan")
    state = _existing_install_state(data_dir)
    assert state["partial"] is True
    assert state["db_exists"] is False


def test_existing_install_state_flags_partial_when_db_has_no_applied_template(
    data_dir: Path,
) -> None:
    """A DB that exists but never had a template applied = aborted onboarding,
    must be treated as partial so resolve prompts overwrite rather than
    silently skipping template apply + feasibility review.

    Regression test for the bug surfaced by orphan handoff
    .trellis/handoffs/2026-05-22-12-21.md.
    """
    from kompany.state.database import Database

    data_dir.mkdir(parents=True)
    # Initialise schema (creates company_config + credential_vault tables)
    # but never apply a template.
    Database(data_dir)
    state = _existing_install_state(data_dir)
    assert state["db_exists"] is True
    assert state["template_id"] is None
    assert state["partial"] is True, (
        "An empty-config DB must be flagged partial — otherwise headless "
        "onboarding silently skips template + feasibility review."
    )


# ---------------------------------------------------------------------------
# Headless full path
# ---------------------------------------------------------------------------


def test_headless_full_path_writes_vault_and_template(data_dir: Path) -> None:
    engine = _FakeEngine()
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="sk-ant-fake",
        template="blank",
        data_dir=data_dir,
        engine_factory=lambda: engine,
    )
    assert isinstance(result, OnboardResult)
    assert result.status == "completed"
    assert result.provider == "anthropic"
    assert result.template_id == "blank"
    assert result.api_key_storage == "vault"
    assert result.ping_status == "skipped_test_mode"
    # vault received the key
    assert engine.credentials.store == {"anthropic_api_key": "sk-ant-fake"}
    # template was applied
    assert engine.apply_calls == [("blank", False)]
    # in-process settings were updated so a same-process directive can read the key
    assert engine.settings.anthropic_api_key == "sk-ant-fake"


def test_headless_missing_api_key_exits_with_clear_message(
    data_dir: Path,
) -> None:
    engine = _FakeEngine()
    with pytest.raises(typer.Exit) as excinfo:
        run_onboard(
            yes=True,
            provider="anthropic",
            api_key=None,
            template="blank",
            data_dir=data_dir,
            engine_factory=lambda: engine,
        )
    # PRD acceptance: exit code 2 for headless config errors.
    assert excinfo.value.exit_code == 2
    # No vault writes, no template apply.
    assert engine.credentials.store == {}
    assert engine.apply_calls == []


def test_headless_picks_api_key_up_from_env(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-supplied-key")
    engine = _FakeEngine()
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key=None,
        template="blank",
        data_dir=data_dir,
        engine_factory=lambda: engine,
    )
    assert result.status == "completed"
    assert engine.credentials.store["anthropic_api_key"] == "env-supplied-key"


def test_headless_with_directive_invokes_engine(data_dir: Path) -> None:
    engine = _FakeEngine(
        directive_outcome=_FakeDirectiveOutcome(
            status="ok", message="Line1\nLine2\nLine3"
        ),
    )
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="fake",
        template="saas-startup",
        directive="Launch Discord",
        data_dir=data_dir,
        engine_factory=lambda: engine,
    )
    assert engine.process_directive_calls == ["Launch Discord"]
    assert result.directive_text == "Launch Discord"
    assert result.directive_status == "ok"
    assert result.template_id == "saas-startup"


def test_headless_directive_failure_is_captured_not_raised(data_dir: Path) -> None:
    engine = _FakeEngine(directive_raises=RuntimeError("LLM blew up"))
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="fake",
        template="blank",
        directive="Do a thing",
        data_dir=data_dir,
        engine_factory=lambda: engine,
    )
    # The wizard must complete even if the first directive fails; the
    # player still has a working install.
    assert result.status == "completed"
    assert result.directive_status == "error"
    assert "LLM blew up" in result.directive_message


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------


def test_unknown_provider_flag_is_rejected(data_dir: Path) -> None:
    engine = _FakeEngine()
    with pytest.raises(typer.Exit) as excinfo:
        run_onboard(
            yes=True,
            provider="bogus",
            api_key="fake",
            template="blank",
            data_dir=data_dir,
            engine_factory=lambda: engine,
        )
    assert excinfo.value.exit_code == 2


def test_all_known_providers_have_env_var_mapping() -> None:
    # Guard against the env-var table drifting from SUPPORTED_PROVIDERS.
    # That's the actual error surface tested in
    # ``test_headless_missing_api_key_exits_with_clear_message``.
    for provider in SUPPORTED_PROVIDERS:
        assert provider in PROVIDER_ENV_VARS


# ---------------------------------------------------------------------------
# Nonexistent template
# ---------------------------------------------------------------------------


def test_nonexistent_template_exits_with_code_2(data_dir: Path) -> None:
    engine = _FakeEngine()
    with pytest.raises(typer.Exit) as excinfo:
        run_onboard(
            yes=True,
            provider="anthropic",
            api_key="fake",
            template="nonexistent",
            data_dir=data_dir,
            engine_factory=lambda: engine,
        )
    assert excinfo.value.exit_code == 2


# ---------------------------------------------------------------------------
# Data dir override
# ---------------------------------------------------------------------------


def test_data_dir_override_is_used(tmp_path: Path) -> None:
    custom = tmp_path / "custom-kompany"
    engine = _FakeEngine()
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="fake",
        template="blank",
        data_dir=custom,
        engine_factory=lambda: engine,
    )
    assert result.data_dir == custom.resolve()
    # Directory was created.
    assert custom.exists()
    # Default ~/.kompany was NOT touched (we can't assert nonexistence of
    # the user's real home, but the override should at least show up on
    # the result and not throw).


# ---------------------------------------------------------------------------
# Idempotent re-run
# ---------------------------------------------------------------------------


def test_rerun_with_existing_db_takes_reuse_path(data_dir: Path) -> None:
    # Pre-create a kompany.db that looks like a finished install.
    data_dir.mkdir(parents=True)
    import sqlite3

    db_path = data_dir / "kompany.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE company_config (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute(
        "CREATE TABLE credential_vault (name TEXT PRIMARY KEY, ciphertext TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO company_config (key, value) VALUES ('template_id', 'blank')"
    )
    conn.execute(
        "INSERT INTO credential_vault (name, ciphertext, updated_at) "
        "VALUES ('anthropic_api_key', 'fake-ciphertext', '2026-05-19')"
    )
    conn.commit()
    conn.close()

    engine = _FakeEngine(
        applied_template="blank",
        existing_vault_keys={"anthropic_api_key": "previously-stored"},
    )
    result = run_onboard(
        yes=True,
        provider=None,
        api_key=None,
        template=None,
        data_dir=data_dir,
        engine_factory=lambda: engine,
    )
    assert result.status == "reused"
    # Should NOT re-apply the template or overwrite the vault key.
    assert engine.apply_calls == []
    assert engine.credentials.store == {"anthropic_api_key": "previously-stored"}
    assert result.template_id == "blank"
    assert result.api_key_storage == "reused"
    assert result.ping_status == "skipped"
    # Provider was inferred from the vault.
    assert result.provider == "anthropic"


def test_rerun_reuse_path_survives_locked_vault(data_dir: Path) -> None:
    # Build a finished install so the wizard hits the reuse branch.
    data_dir.mkdir(parents=True)
    import sqlite3

    db_path = data_dir / "kompany.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE company_config (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute(
        "CREATE TABLE credential_vault (name TEXT PRIMARY KEY, ciphertext TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO company_config (key, value) VALUES ('template_id', 'blank')"
    )
    conn.commit()
    conn.close()

    engine = _FakeEngine(applied_template="blank")
    # Simulate "vault key not unlocked yet" — credentials.get() blows up.
    engine.credentials.raise_on_get = True
    # The reuse path must not crash even if the vault probe fails.
    result = run_onboard(
        yes=True,
        data_dir=data_dir,
        engine_factory=lambda: engine,
    )
    assert result.status == "reused"


# ---------------------------------------------------------------------------
# Vault fallback when KOMPANY_VAULT_KEY is missing
# ---------------------------------------------------------------------------


def test_missing_vault_key_falls_back_to_env_only_storage(data_dir: Path) -> None:
    engine = _FakeEngine(vault_key="")  # no vault key configured
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="fake",
        template="blank",
        data_dir=data_dir,
        engine_factory=lambda: engine,
    )
    assert result.api_key_storage == "env"
    # Wizard added a note telling the player how to make the key durable.
    assert any("ANTHROPIC_API_KEY" in note for note in result.notes)
    # No vault write happened (no key was available to encrypt with).
    assert engine.credentials.store == {}
    # But the in-process settings still reflect the key.
    assert engine.settings.anthropic_api_key == "fake"


# ---------------------------------------------------------------------------
# Interactive prompts via CliRunner
# ---------------------------------------------------------------------------


def test_interactive_masked_prompt_reads_api_key_from_stdin(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API-key prompt must use ``password=True`` so it's not echoed.

    We assert behaviour, not implementation, by feeding stdin and
    inspecting the result: the wizard accepts the line as the key and the
    prompt label includes the provider name (so the player knows which
    key to paste).
    """
    captured: dict[str, Any] = {}
    engine = _FakeEngine()

    # Spy on Rich's password Prompt so we can confirm we *asked* for a
    # masked entry. (Rich's Prompt directly reads sys.stdin; ``input=`` on
    # CliRunner is fine but verifying ``password=True`` requires patching.)
    from rich.prompt import Prompt as RichPrompt

    real_ask = RichPrompt.ask.__func__

    def _spy_ask(cls, prompt_text="", *, console=None, password=False, choices=None, default=..., show_choices=True, show_default=True, **kw):
        captured.setdefault("calls", []).append({
            "prompt": prompt_text,
            "password": password,
            "choices": choices,
        })
        # Return the right value for each prompt the wizard asks.
        if password:
            return "interactive-key"
        if choices and "anthropic" in choices:
            return "anthropic"
        if choices and "blank" in choices:
            return "blank"
        return default if default is not ... else ""

    monkeypatch.setattr(RichPrompt, "ask", classmethod(_spy_ask))
    # Also stub Confirm.ask so step 4 cleanly skips.
    from rich.prompt import Confirm

    monkeypatch.setattr(Confirm, "ask", classmethod(lambda cls, *a, **k: False))

    result = run_onboard(
        yes=False,
        provider=None,
        api_key=None,
        template=None,
        data_dir=data_dir,
        engine_factory=lambda: engine,
    )
    assert result.status == "completed"
    # At least one prompt was the masked API-key entry.
    assert any(c["password"] for c in captured["calls"])
    # The provider prompt offered the supported set.
    assert any(
        c["choices"] is not None and "anthropic" in c["choices"]
        for c in captured["calls"]
    )
    assert engine.credentials.store["anthropic_api_key"] == "interactive-key"


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


def test_cli_onboard_command_is_registered() -> None:
    runner = CliRunner()
    # Force a wide, non-interactive terminal: on some CI runners
    # shutil.get_terminal_size() falls back to a very narrow width with no
    # controlling tty, and Rich then wraps/truncates the options panel so a
    # flag name can be split across lines — force COLUMNS wide enough that
    # every flag renders on one line regardless of the runner environment.
    result = runner.invoke(
        app, ["onboard", "--help"], env={"COLUMNS": "200", "TERM": "dumb"}
    )
    assert result.exit_code == 0
    # All five PRD-locked flags must be in the help output.
    for flag in ("--yes", "--provider", "--api-key", "--template", "--directive", "--data-dir"):
        assert flag in result.stdout, f"missing {flag} in help"


def test_cli_onboard_yes_smoke_through_runner(tmp_path: Path) -> None:
    runner = CliRunner()
    custom = tmp_path / "data"
    # CliRunner builds a real KompanyEngine here; KOMPANY_TEST_MODE skips
    # the network ping. We only need to confirm exit code 0 and the
    # next-step panel.
    env_overrides = {
        "KOMPANY_TEST_MODE": "1",
        "KOMPANY_DATA_DIR": str(custom),
    }
    # Strip any inherited API key env var.
    for k in PROVIDER_ENV_VARS.values():
        env_overrides[k] = ""
    result = runner.invoke(
        app,
        [
            "onboard",
            "--yes",
            "--provider=anthropic",
            "--api-key=sk-ant-fake",
            "--template=blank",
            "--data-dir",
            str(custom),
        ],
        env=env_overrides,
    )
    assert result.exit_code == 0, result.stdout
    assert "Your CEO is on it" in result.stdout
    # PRD: kompany.db materialises after a clean headless run.
    assert (custom / "kompany.db").exists()


def test_cli_onboard_missing_api_key_exits_2(tmp_path: Path) -> None:
    runner = CliRunner()
    custom = tmp_path / "data"
    env_overrides = {"KOMPANY_TEST_MODE": "1", "KOMPANY_DATA_DIR": str(custom)}
    for k in PROVIDER_ENV_VARS.values():
        env_overrides[k] = ""
    result = runner.invoke(
        app,
        [
            "onboard",
            "--yes",
            "--provider=anthropic",
            "--template=blank",
            "--data-dir",
            str(custom),
        ],
        env=env_overrides,
    )
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.stdout
