"""Tests for kompany.installer.reset — wipe + backup + safety tiers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kompany.installer.reset import (
    ResetError,
    StateSummary,
    inspect_state,
    reset,
)
from kompany.interfaces.cli import app as cli_app


# ---------------------------------------------------------------------------
# Fixtures — minimal on-disk Kompany layouts
# ---------------------------------------------------------------------------


def _seed_db(data_dir: Path, *, template_id: str | None, projects: int = 0,
             expense_usd: float = 0.0) -> None:
    """Write a minimal kompany.db matching the engine's schema enough
    for inspect_state to read."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "kompany.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE company_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE projects (id TEXT PRIMARY KEY, status TEXT, type TEXT)"
        )
        conn.execute(
            "CREATE TABLE project_episodes (id TEXT PRIMARY KEY, project_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE ledger (id TEXT PRIMARY KEY, category TEXT, amount REAL)"
        )
        conn.execute(
            "CREATE TABLE credential_vault (name TEXT PRIMARY KEY, ciphertext TEXT, "
            "updated_at TEXT)"
        )
        if template_id is not None:
            conn.execute(
                "INSERT INTO company_config VALUES (?, ?, datetime('now'))",
                ("template_id", template_id),
            )
        for i in range(projects):
            conn.execute(
                "INSERT INTO projects VALUES (?, ?, ?)",
                (f"p{i}", "active", "directive"),
            )
        if expense_usd > 0:
            conn.execute(
                "INSERT INTO ledger VALUES (?, ?, ?)",
                ("l0", "EXPENSE", -float(expense_usd)),
            )
        conn.execute(
            "INSERT INTO credential_vault VALUES (?, ?, datetime('now'))",
            ("anthropic_api_key", "cipher-blob"),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# inspect_state
# ---------------------------------------------------------------------------


def test_inspect_state_missing_dir(tmp_path):
    state = inspect_state(tmp_path / "absent")
    assert state.exists is False
    assert state.is_fresh is True
    assert state.is_live is False


def test_inspect_state_fresh_dir(tmp_path):
    (tmp_path / "fresh").mkdir()
    state = inspect_state(tmp_path / "fresh")
    assert state.exists is True
    assert state.template_id is None
    assert state.is_fresh is True
    assert state.is_live is False


def test_inspect_state_onboarded_no_projects(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup")
    state = inspect_state(tmp_path)
    assert state.template_id == "saas-startup"
    assert state.project_count == 0
    assert state.total_spend_usd == 0.0
    assert state.is_fresh is False
    assert state.is_live is False


def test_inspect_state_live_with_projects(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup", projects=3)
    state = inspect_state(tmp_path)
    assert state.project_count == 3
    assert state.is_live is True


def test_inspect_state_live_with_spend(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup", expense_usd=12.50)
    state = inspect_state(tmp_path)
    assert state.total_spend_usd == pytest.approx(12.50)
    assert state.is_live is True


# ---------------------------------------------------------------------------
# reset — confirmation tiers + wipe behavior
# ---------------------------------------------------------------------------


def test_reset_fresh_no_confirm_needed(tmp_path):
    # Fresh state with leftover credential vault — wipe with no callback.
    _seed_db(tmp_path, template_id=None)
    result = reset(tmp_path, no_backup=True)
    assert "kompany.db" in result.files_removed
    assert (tmp_path / "kompany.db").exists() is False


def test_reset_onboarded_requires_confirm(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup")
    with pytest.raises(ResetError, match="onboarded state"):
        reset(tmp_path, no_backup=True)  # no callback, not --yes


def test_reset_onboarded_with_yes_skips_callback(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup")
    result = reset(tmp_path, yes=True, no_backup=True)
    assert (tmp_path / "kompany.db").exists() is False
    assert "kompany.db" in result.files_removed


def test_reset_live_requires_force_or_typed_phrase(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup", projects=2)
    # --yes alone is NOT enough for live state.
    with pytest.raises(ResetError, match="live state"):
        reset(tmp_path, yes=True, no_backup=True)


def test_reset_live_with_force_proceeds(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup", projects=2)
    result = reset(tmp_path, force=True, no_backup=True)
    assert "kompany.db" in result.files_removed


def test_reset_live_with_callback_matching_phrase(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup", projects=2)
    captured = {}

    def cb(summary, expected):
        captured["expected"] = expected
        captured["summary"] = summary
        return True  # simulate user typing RESET

    result = reset(tmp_path, no_backup=True, confirm_callback=cb)
    assert captured["expected"] == "RESET"
    assert captured["summary"].project_count == 2
    assert "kompany.db" in result.files_removed


def test_reset_live_with_callback_returning_false_aborts(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup", projects=2)
    with pytest.raises(ResetError, match="aborted by user"):
        reset(tmp_path, no_backup=True, confirm_callback=lambda s, e: False)
    # DB untouched.
    assert (tmp_path / "kompany.db").exists()


# ---------------------------------------------------------------------------
# backup behavior
# ---------------------------------------------------------------------------


def test_reset_creates_backup_by_default(tmp_path):
    target = tmp_path / "kdata"
    _seed_db(target, template_id="saas-startup")
    result = reset(target, yes=True)
    assert result.backup_path is not None
    assert result.backup_path.is_dir()
    assert (result.backup_path / "kompany.db").exists()
    # Original DB gone.
    assert (target / "kompany.db").exists() is False


def test_reset_no_backup_flag_skips_backup(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup")
    result = reset(tmp_path, yes=True, no_backup=True)
    assert result.backup_path is None
    # No sibling backup dir created.
    siblings = [p.name for p in tmp_path.parent.iterdir() if p.name.startswith(tmp_path.name + ".backup")]
    assert siblings == []


# ---------------------------------------------------------------------------
# keep_credentials behavior
# ---------------------------------------------------------------------------


def test_reset_keep_credentials_preserves_vault(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup", projects=1)
    result = reset(
        tmp_path,
        force=True,
        no_backup=True,
        keep_credentials=True,
    )
    assert result.credentials_kept is True
    db = sqlite3.connect(str(tmp_path / "kompany.db"))
    try:
        # credential_vault still has its row
        n_creds = db.execute("SELECT COUNT(*) FROM credential_vault").fetchone()[0]
        # other tables dropped
        tables = {
            r[0] for r in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        db.close()
    assert n_creds == 1
    assert tables == {"credential_vault"}


def test_reset_full_wipe_removes_vault_too(tmp_path):
    _seed_db(tmp_path, template_id="saas-startup")
    reset(tmp_path, yes=True, no_backup=True)
    assert (tmp_path / "kompany.db").exists() is False


# ---------------------------------------------------------------------------
# idempotence
# ---------------------------------------------------------------------------


def test_reset_on_empty_dir_is_idempotent(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = reset(empty, no_backup=True)
    # No state to wipe is success, not error.
    assert result.files_removed == []
    assert result.dirs_removed == []
    assert "no state to wipe" in result.notes


def test_reset_on_nonexistent_dir(tmp_path):
    absent = tmp_path / "nope"
    result = reset(absent, no_backup=True)
    assert result.files_removed == []


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_reset_fresh_dir(tmp_path):
    runner = CliRunner()
    target = tmp_path / "fresh"
    target.mkdir()
    result = runner.invoke(
        cli_app, ["reset", "--data-dir", str(target), "--no-backup"]
    )
    assert result.exit_code == 0, result.output
    assert "reset complete" in result.output


def test_cli_reset_onboarded_with_yes(tmp_path):
    runner = CliRunner()
    _seed_db(tmp_path, template_id="saas-startup")
    result = runner.invoke(
        cli_app,
        ["reset", "--data-dir", str(tmp_path), "--yes", "--no-backup"],
    )
    assert result.exit_code == 0, result.output
    assert "reset complete" in result.output
    assert (tmp_path / "kompany.db").exists() is False


def test_cli_reset_live_without_force_blocks(tmp_path):
    runner = CliRunner()
    _seed_db(tmp_path, template_id="saas-startup", projects=2)
    # No input, no --force: the typed-RESET prompt receives empty → abort.
    result = runner.invoke(
        cli_app,
        ["reset", "--data-dir", str(tmp_path), "--no-backup"],
        input="\n",
    )
    assert result.exit_code == 1
    assert "aborted" in result.output.lower()
    assert (tmp_path / "kompany.db").exists()


def test_cli_reset_live_with_typed_phrase(tmp_path):
    runner = CliRunner()
    _seed_db(tmp_path, template_id="saas-startup", projects=2)
    result = runner.invoke(
        cli_app,
        ["reset", "--data-dir", str(tmp_path), "--no-backup"],
        input="RESET\n",
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "kompany.db").exists() is False


def test_cli_reset_force_skips_typed_phrase(tmp_path):
    runner = CliRunner()
    _seed_db(tmp_path, template_id="saas-startup", projects=2)
    result = runner.invoke(
        cli_app,
        ["reset", "--data-dir", str(tmp_path), "--force", "--no-backup"],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "kompany.db").exists() is False
