"""Anima diary store — one first-person entry per calendar day.

PRD ``06-12-anima-persona`` D3: the diary intent distills the last 24h
into a <=200-word first-person entry whose tone reflects the current
(valence, energy). One row per date (date is the PRIMARY KEY — the
once-per-day gate is structural, not just logical). ``cost`` records
the economy-tier LLM spend; the booking itself goes through the normal
cost path (``action_type="anima_diary"``), never this table.

This is a T0 internal artifact: writing it needs no founder approval.
Publishing it anywhere external is PRD 5's approval gate.
"""

from __future__ import annotations

from kompany.state.database import Database


class AnimaDiaryStore:
    """Date-keyed diary entries written by the Anima diary tick intent."""

    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        *,
        date: str,
        entry: str,
        valence: float,
        energy: float,
        cost: float = 0.0,
    ) -> dict:
        """Insert (or overwrite — restore/replay safety) one day's entry."""
        self.db.execute(
            """INSERT INTO anima_diary (date, entry, valence, energy, cost)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 entry = excluded.entry,
                 valence = excluded.valence,
                 energy = excluded.energy,
                 cost = excluded.cost""",
            (date, entry, float(valence), float(energy), float(cost or 0.0)),
        )
        self.db.commit()
        return self.get(date) or {}

    def get(self, date: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM anima_diary WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def list_entries(self, limit: int = 30) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM anima_diary ORDER BY date DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]


__all__ = ["AnimaDiaryStore"]
