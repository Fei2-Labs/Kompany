"""UI preferences — founder-controlled dashboard appearance settings.

The theme system (feature A) keeps a fast localStorage cache in the WebView for
first-paint, but the DB is the source of truth so a reinstall / second device
sees the same look (decision #7). Three fields:

* ``theme_id``      — which base theme (opaque string; the web UI owns the
  catalogue and falls back to its default if it sees an unknown id, so the
  backend stores it without hard-coding the theme list)
* ``auto_enabled``  — ambient auto-mode on/off (feature #5)
* ``reduce_motion`` — ``"auto"`` (follow OS) | ``"on"`` | ``"off"`` (decision #10)

Storage is the existing ``company_config`` key-value table under a single
``ui_preferences`` JSON row — same pattern as :mod:`kompany.state.targets`, so
no schema migration is needed. ``extra="forbid"`` makes typo'd keys fail loudly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kompany.state.database import Database

_KEY = "ui_preferences"

ReduceMotion = Literal["auto", "on", "off"]


class UIPreferences(BaseModel):
    """Founder's dashboard appearance preferences.

    Defaults mirror the web UI's own defaults so a fresh install and an
    un-synced WebView agree: cyberpunk theme, ambient off, motion follows OS.
    """

    model_config = ConfigDict(extra="forbid")

    # Opaque to the backend on purpose — the web UI validates against its theme
    # catalogue and degrades to its default for unknown ids. We only guard
    # against empty / absurdly long values.
    theme_id: str = Field(default="cyberpunk", min_length=1, max_length=40)
    auto_enabled: bool = False
    reduce_motion: ReduceMotion = "auto"


def _read_config(db: Database, key: str) -> str | None:
    row = db.execute(
        "SELECT value FROM company_config WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def _write_config(db: Database, key: str, value: str) -> None:
    db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value,
             updated_at = datetime('now')""",
        (key, value),
    )


def get_preferences(db: Database) -> UIPreferences:
    """Return stored preferences, or defaults if unset/corrupt. Never raises."""
    raw = _read_config(db, _KEY)
    if not raw:
        return UIPreferences()
    try:
        return UIPreferences.model_validate_json(raw)
    except Exception:
        # Corrupt / out-of-date row → safe defaults rather than a 500.
        return UIPreferences()


def set_preferences(
    db: Database,
    *,
    theme_id: str | None = None,
    auto_enabled: bool | None = None,
    reduce_motion: str | None = None,
) -> UIPreferences:
    """Patch the given fields (others untouched) and persist. Returns the result.

    Raises ``ValueError`` on an invalid ``reduce_motion`` so the REST layer can
    map it to a 422.
    """
    prefs = get_preferences(db)
    if theme_id is not None:
        prefs.theme_id = theme_id
    if auto_enabled is not None:
        prefs.auto_enabled = auto_enabled
    if reduce_motion is not None:
        if reduce_motion not in ("auto", "on", "off"):
            raise ValueError(
                "reduce_motion must be one of 'auto', 'on', 'off'; "
                f"got {reduce_motion!r}"
            )
        prefs.reduce_motion = reduce_motion
    # Re-validate the whole model (catches a bad theme_id length, etc.).
    prefs = UIPreferences.model_validate(prefs.model_dump())
    _write_config(db, _KEY, prefs.model_dump_json())
    db.commit()
    return prefs


__all__ = ["UIPreferences", "ReduceMotion", "get_preferences", "set_preferences"]
