"""Integration: full ``kompany onboard --yes`` flow against a real engine.

These tests build a real :class:`KompanyEngine` against a tmp data dir,
short-circuit the LLM ping via ``KOMPANY_TEST_MODE=1``, and assert that
the on-disk artefacts (``kompany.db``, vault rows, ``company_config``)
end up in the state the PRD requires.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from kompany.installer import run_onboard
from kompany.installer.onboard import PROVIDER_ENV_VARS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Test mode → LLM ping is bypassed, no network calls.
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    # Wipe every provider env var so the headless path under test is
    # deterministic.
    for var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    # Provide a vault master key so credentials can encrypt the API key
    # at rest (otherwise we'd take the env-only fallback path, which is
    # covered separately in the unit suite).
    monkeypatch.setenv("KOMPANY_VAULT_KEY", Fernet.generate_key().decode("utf-8"))
    # KOMPANY_DATA_DIR is reset by the wizard itself; we just make sure
    # we don't inherit a stale value from the host shell.
    monkeypatch.delenv("KOMPANY_DATA_DIR", raising=False)


def _read_config(db_path: Path, key: str) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value FROM company_config WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _vault_names(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM credential_vault ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_full_headless_flow_creates_db_vault_and_config(tmp_path: Path) -> None:
    data_dir = tmp_path / "kompany-home"
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="sk-ant-test-integration",
        template="blank",
        data_dir=data_dir,
    )

    assert result.status == "completed"
    assert result.provider == "anthropic"
    assert result.template_id == "blank"
    assert result.api_key_storage == "vault"
    assert result.ping_status == "skipped_test_mode"

    db_path = data_dir / "kompany.db"
    assert db_path.exists(), "engine init should have created kompany.db"
    assert _read_config(db_path, "template_id") == "blank"
    assert "anthropic_api_key" in _vault_names(db_path)


def test_full_headless_flow_with_saas_startup_template(tmp_path: Path) -> None:
    data_dir = tmp_path / "saas-home"
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="sk-ant-saas",
        template="saas-startup",
        data_dir=data_dir,
    )
    assert result.template_id == "saas-startup"
    db_path = data_dir / "kompany.db"
    assert _read_config(db_path, "template_id") == "saas-startup"
    # SaaS template ships a non-trivial initial budget; the row must be
    # written so the player has cash on day 1.
    budget_str = _read_config(db_path, "initial_budget")
    assert budget_str is not None
    assert float(budget_str) > 0


def test_rerun_with_same_data_dir_reuses_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "reuse-home"
    first = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="sk-first-run",
        template="blank",
        data_dir=data_dir,
    )
    assert first.status == "completed"

    second = run_onboard(
        yes=True,
        # Pass nothing — the reuse path must work with no flags.
        data_dir=data_dir,
    )
    assert second.status == "reused"
    assert second.template_id == "blank"

    # The DB still exists; we didn't wipe it.
    assert (data_dir / "kompany.db").exists()


def test_headless_with_directive_invokes_engine_process_directive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``--directive`` is supplied, the wizard calls
    ``engine.process_directive`` exactly once and records the outcome.

    We monkey-patch ``KompanyEngine.process_directive`` so the test does
    not depend on the full CEO debate pipeline; the integration point we
    actually want to cover is "directive flag → engine method called →
    result captured", not "directive plan synthesis works".
    """
    from kompany.core.engine import KompanyEngine

    seen: dict[str, str] = {}

    class _Outcome:
        status = "ok"
        message = "CEO acknowledges your directive.\nReady to plan."
        project_id = None
        approval_id = None
        total_ai_cost = 0.0
        agents_used = []

    def _fake_process_directive(self, raw_input: str):
        seen["directive"] = raw_input
        return _Outcome()

    monkeypatch.setattr(KompanyEngine, "process_directive", _fake_process_directive)

    data_dir = tmp_path / "directive-home"
    result = run_onboard(
        yes=True,
        provider="anthropic",
        api_key="sk-fake",
        template="saas-startup",
        directive="Launch a Discord community",
        data_dir=data_dir,
    )
    assert result.status == "completed"
    assert seen["directive"] == "Launch a Discord community"
    assert result.directive_text == "Launch a Discord community"
    assert result.directive_status == "ok"


def test_headless_rejects_unknown_template(tmp_path: Path) -> None:
    import typer

    data_dir = tmp_path / "bad-template"
    with pytest.raises(typer.Exit) as excinfo:
        run_onboard(
            yes=True,
            provider="anthropic",
            api_key="sk-fake",
            template="nonexistent-template-id",
            data_dir=data_dir,
        )
    assert excinfo.value.exit_code == 2
