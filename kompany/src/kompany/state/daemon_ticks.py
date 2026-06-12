"""Daemon tick store — durable history of autonomous ticker passes.

PRD ``06-12-daemon-tick-loop`` D3 step 5: every ticker pass records one
row (started_at, duration_ms, actions JSON, outcome, optional detail)
so the founder can audit what the company did while nobody was watching.

Retention is enforced by the ticker's housekeeping step, which calls
:meth:`DaemonTickStore.prune` with ``keep=500`` (PRD requirement: keep
the last 500 ticks). Table creation follows the ``shadow_costs``
precedent in :meth:`kompany.state.database.Database._migrate`.
"""

from __future__ import annotations

import json
from typing import Any

from kompany.state.database import Database


class DaemonTickStore:
    """Queryable record of daemon ticker passes."""

    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        *,
        started_at: str,
        duration_ms: int,
        actions: list[str],
        outcome: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert one tick row. Returns the stored row with decoded JSON."""
        cursor = self.db.execute(
            """INSERT INTO daemon_ticks
               (started_at, duration_ms, actions, outcome, detail)
               VALUES (?, ?, ?, ?, ?)""",
            (
                started_at,
                int(duration_ms or 0),
                json.dumps(list(actions or [])),
                outcome,
                json.dumps(detail) if detail is not None else None,
            ),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT * FROM daemon_ticks WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return self._decode(dict(row))

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Newest-first tick rows, capped at ``limit``."""
        rows = self.db.execute(
            "SELECT * FROM daemon_ticks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._decode(dict(r)) for r in rows]

    def count(self) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM daemon_ticks"
        ).fetchone()
        return int(row["n"] or 0)

    def prune(self, keep: int = 500) -> int:
        """Delete all but the newest ``keep`` rows. Returns rows removed."""
        keep = max(0, int(keep))
        cursor = self.db.execute(
            """DELETE FROM daemon_ticks WHERE id NOT IN (
                   SELECT id FROM daemon_ticks ORDER BY id DESC LIMIT ?
               )""",
            (keep,),
        )
        self.db.commit()
        return int(cursor.rowcount or 0)

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        try:
            row["actions"] = json.loads(row.get("actions") or "[]")
        except (TypeError, ValueError):
            row["actions"] = []
        raw = row.get("detail")
        if raw:
            try:
                row["detail"] = json.loads(raw)
            except (TypeError, ValueError):
                row["detail"] = None
        else:
            row["detail"] = None
        return row


__all__ = ["DaemonTickStore"]
