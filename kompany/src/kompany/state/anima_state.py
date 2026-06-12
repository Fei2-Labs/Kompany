"""Anima emotion state store — single-row (valence, energy) persistence.

PRD ``06-12-anima-persona`` D2: the company persona ("Anima",
provisional name) carries an explicit 2-axis emotional state —
``valence`` in [-1, 1] (negative ↔ positive) and ``energy`` in [0, 1]
(depleted ↔ energized). The row also tracks ``last_diary_date`` for the
once-per-calendar-day diary gate (D3).

Hard invariant (D2 / CONSTITUTION honest-assessment clause): this state
shapes Anima's VOICE only (diary tone, summary phrasing, idle-intent
priority). It must never be injected into C-suite / classification /
debate prompts — this store therefore has no prompt-rendering helpers.
"""

from __future__ import annotations

from kompany.state.database import Database

ROW_ID = 1
DEFAULT_VALENCE = 0.0
DEFAULT_ENERGY = 0.5


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


class AnimaStateStore:
    """Single-row emotion state (valence, energy, last_diary_date)."""

    def __init__(self, db: Database):
        self.db = db

    def get(self) -> dict:
        """Return the state row, inserting defaults on first read."""
        row = self.db.execute(
            "SELECT * FROM anima_state WHERE id = ?", (ROW_ID,)
        ).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO anima_state (id, valence, energy) VALUES (?, ?, ?)",
                (ROW_ID, DEFAULT_VALENCE, DEFAULT_ENERGY),
            )
            self.db.commit()
            row = self.db.execute(
                "SELECT * FROM anima_state WHERE id = ?", (ROW_ID,)
            ).fetchone()
        return dict(row)

    def set(self, *, valence: float, energy: float) -> dict:
        """Persist a clamped (valence, energy) pair; bumps ``updated_at``."""
        self.get()  # ensure the row exists
        self.db.execute(
            "UPDATE anima_state SET valence = ?, energy = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (
                _clamp(valence, -1.0, 1.0),
                _clamp(energy, 0.0, 1.0),
                ROW_ID,
            ),
        )
        self.db.commit()
        return self.get()

    def set_last_diary_date(self, date_iso: str) -> None:
        """Record the calendar date (YYYY-MM-DD) of the latest diary entry."""
        self.get()  # ensure the row exists
        self.db.execute(
            "UPDATE anima_state SET last_diary_date = ? WHERE id = ?",
            (date_iso, ROW_ID),
        )
        self.db.commit()


__all__ = ["AnimaStateStore", "DEFAULT_ENERGY", "DEFAULT_VALENCE", "ROW_ID"]
