"""Reset Kompany state — wipe DB + vault + backups, auto-backup first.

Safety-tiered confirmation:

- **Fresh** state (no template applied)        → no confirmation needed.
- **Onboarded, no projects + zero spend**      → y/N confirmation.
- **Live** state (≥ 1 project OR ledger spend) → must type literal
  ``RESET`` before proceeding. ``--force`` skips even this gate; use
  only when scripting against state you know is disposable.

Behavior summary printed BEFORE any destructive action, including the
backup destination path. The auto-backup is opt-out via
``--no-backup`` — useful only when you genuinely never want to recover
(e.g. CI tearing down ephemeral state).

This module is intentionally separate from ``installer/onboard.py`` so
the import graph stays simple: reset is a destructive admin operation,
not part of the regular onboarding lifecycle.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Files / directories that constitute a Kompany install. Listed here
# rather than discovered to keep the wipe deterministic — if the engine
# adds new on-disk artifacts later, this list must be updated, and the
# update lives next to the reset semantics it informs.
_INSTALL_FILES = (
    "kompany.db",
    "kompany.db-wal",
    "kompany.db-shm",
)
_INSTALL_DIRS = (
    "backups",
)


class ResetError(RuntimeError):
    """Raised when reset cannot proceed (bad confirmation, missing dir, ...)."""


@dataclass
class StateSummary:
    """What's in the data dir, used to drive confirmation strength + the
    user-facing summary printed before wipe."""

    data_dir: Path
    exists: bool = False
    template_id: str | None = None
    project_count: int = 0
    episode_count: int = 0
    total_spend_usd: float = 0.0
    deadline: str | None = None
    revenue_target_usd: float | None = None

    @property
    def is_fresh(self) -> bool:
        return not self.exists or self.template_id is None

    @property
    def is_live(self) -> bool:
        return (
            self.template_id is not None
            and (self.project_count > 0 or self.total_spend_usd > 0)
        )


@dataclass
class ResetResult:
    data_dir: Path
    backup_path: Path | None = None
    files_removed: list[str] = field(default_factory=list)
    dirs_removed: list[str] = field(default_factory=list)
    credentials_kept: bool = False
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# State inspection
# ---------------------------------------------------------------------------


def inspect_state(data_dir: Path) -> StateSummary:
    """Read enough state to drive the safety prompt + summary.

    Uses ``sqlite3`` directly to avoid constructing a full engine (which
    would write audit events that we are about to destroy). Tolerant of
    a missing or partly-initialised DB.
    """
    summary = StateSummary(data_dir=data_dir, exists=data_dir.exists())
    db_path = data_dir / "kompany.db"
    if not db_path.exists():
        return summary

    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        # Corrupt DB still gets wiped; just can't summarise it.
        return summary
    try:
        try:
            row = conn.execute(
                "SELECT value FROM company_config WHERE key = 'template_id'"
            ).fetchone()
            summary.template_id = row["value"] if row else None
        except sqlite3.OperationalError:
            pass
        try:
            row = conn.execute(
                "SELECT value FROM company_config WHERE key = 'agreed_revenue_target'"
            ).fetchone()
            if row and row["value"]:
                try:
                    summary.revenue_target_usd = float(row["value"])
                except ValueError:
                    pass
        except sqlite3.OperationalError:
            pass
        try:
            row = conn.execute(
                "SELECT value FROM company_config WHERE key = 'agreed_deadline'"
            ).fetchone()
            summary.deadline = row["value"] if row else None
        except sqlite3.OperationalError:
            pass
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()
            summary.project_count = int(row["n"]) if row else 0
        except sqlite3.OperationalError:
            pass
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM project_episodes").fetchone()
            summary.episode_count = int(row["n"]) if row else 0
        except sqlite3.OperationalError:
            pass
        try:
            # Ledger stores money out as negative-amount EXPENSE rows.
            row = conn.execute(
                "SELECT COALESCE(SUM(-amount), 0) AS n FROM ledger "
                "WHERE category = 'EXPENSE'"
            ).fetchone()
            summary.total_spend_usd = float(row["n"]) if row and row["n"] else 0.0
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()
    return summary


# ---------------------------------------------------------------------------
# Wipe + backup
# ---------------------------------------------------------------------------


def _backup_data_dir(data_dir: Path) -> Path:
    """Copy every install file/dir under ``data_dir`` to a
    ``<data_dir>.backup-<ISO>`` sibling. Returns the backup path."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_dir = data_dir.parent / f"{data_dir.name}.backup-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for name in _INSTALL_FILES:
        src = data_dir / name
        if src.exists():
            shutil.copy2(src, backup_dir / name)
    for name in _INSTALL_DIRS:
        src = data_dir / name
        if src.is_dir():
            shutil.copytree(src, backup_dir / name)
    return backup_dir


def _wipe_data_dir(data_dir: Path, *, keep_credentials: bool) -> ResetResult:
    """Remove the install files/dirs. The data dir itself is preserved
    so any user-placed config / share-state survives the operation."""
    result = ResetResult(data_dir=data_dir, credentials_kept=keep_credentials)

    if keep_credentials:
        # ``credential_vault`` rows live INSIDE kompany.db, so a "keep
        # credentials" reset can't wipe the DB. Instead we delete every
        # table except credential_vault — preserving the only state the
        # user shouldn't have to re-enter.
        import sqlite3

        db_path = data_dir / "kompany.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                try:
                    rows = conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type IN ('table', 'view') "
                        "AND name NOT LIKE 'sqlite_%' "
                        "AND name != 'credential_vault'"
                    ).fetchall()
                    for (name,) in rows:
                        conn.execute(f"DROP TABLE IF EXISTS {name}")
                    conn.commit()
                finally:
                    conn.close()
                result.notes.append(
                    "credential_vault preserved; everything else dropped from kompany.db"
                )
            except sqlite3.Error as exc:
                result.notes.append(
                    f"credentials-keep failed ({exc}); falling back to full wipe"
                )
                keep_credentials = False
                result.credentials_kept = False

    if not keep_credentials:
        for name in _INSTALL_FILES:
            target = data_dir / name
            if target.exists():
                target.unlink()
                result.files_removed.append(name)
        for name in _INSTALL_DIRS:
            target = data_dir / name
            if target.is_dir():
                shutil.rmtree(target)
                result.dirs_removed.append(name)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reset(
    data_dir: Path,
    *,
    yes: bool = False,
    force: bool = False,
    no_backup: bool = False,
    keep_credentials: bool = False,
    confirm_callback: Callable[[StateSummary, str], bool] | None = None,
) -> ResetResult:
    """Reset a Kompany install.

    ``confirm_callback`` is invoked when a confirmation is required;
    receives ``(state_summary, expected_typed_phrase_or_empty)`` and
    must return True to proceed. Pass ``yes=True`` to skip the
    fresh/onboarded gate; ``force=True`` to skip the live-state gate
    too. The callback is the integration seam for the CLI's
    interactive prompts (and for tests).
    """
    data_dir = data_dir.expanduser()
    state = inspect_state(data_dir)

    if not state.exists or (
        not (data_dir / "kompany.db").exists()
        and not any((data_dir / d).is_dir() for d in _INSTALL_DIRS)
    ):
        # Nothing to wipe. Treat as success so reset is idempotent.
        return ResetResult(data_dir=data_dir, notes=["no state to wipe"])

    # Decide what level of confirmation is required.
    # - Live  : --force OR typed-RESET callback. --yes alone is NOT enough.
    # - Onboarded but not live : --yes OR (--force) OR y/N callback.
    # - Fresh : no gate.
    if state.is_live:
        if not force:
            if confirm_callback is None:
                raise ResetError(
                    "live state detected — pass --force or a confirm_callback"
                )
            if not confirm_callback(state, "RESET"):
                raise ResetError("reset aborted by user")
    elif not state.is_fresh:
        if not yes and not force:
            if confirm_callback is None:
                raise ResetError(
                    "onboarded state detected — pass --yes or a confirm_callback"
                )
            if not confirm_callback(state, ""):
                raise ResetError("reset aborted by user")
    # Fresh state needs no confirmation.

    backup_path: Path | None = None
    if not no_backup:
        try:
            backup_path = _backup_data_dir(data_dir)
        except OSError as exc:
            raise ResetError(f"backup failed; aborting reset: {exc}") from exc

    result = _wipe_data_dir(data_dir, keep_credentials=keep_credentials)
    result.backup_path = backup_path
    return result
