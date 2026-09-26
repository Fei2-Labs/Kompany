"""Evolution probation store (09-26-evolution-probation).

One row per applied soul proposal under trial. The doctor proves an
evolved soul *loads*; probation is the first gate that measures whether
it *performs*: the role's task failure rate over the next
``trial_target`` finished tasks is compared with the pre-evolution
baseline. Logic lives in ``core/artifact_evolution/probation.py``.
"""

from __future__ import annotations

from typing import Any

from kompany.state.database import Database

STATUSES: frozenset[str] = frozenset({"probation", "passed", "reverted", "inconclusive", "closed"})


class ArtifactProbationStore:
    def __init__(self, db: Database):
        self.db = db

    def start(
        self,
        *,
        proposal_id: str,
        role: str,
        baseline_failed: int,
        baseline_total: int,
        trial_target: int,
    ) -> dict[str, Any]:
        self.db.execute(
            """INSERT INTO artifact_probations
               (proposal_id, role, baseline_failed, baseline_total, trial_target)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(proposal_id) DO NOTHING""",
            (proposal_id, role, int(baseline_failed), int(baseline_total), int(trial_target)),
        )
        self.db.commit()
        return self.get(proposal_id) or {}

    def progress(self, proposal_id: str, *, trial_failed: int, trial_total: int) -> None:
        self.db.execute(
            "UPDATE artifact_probations SET trial_failed = ?, trial_total = ? "
            "WHERE proposal_id = ?",
            (int(trial_failed), int(trial_total), proposal_id),
        )
        self.db.commit()

    def decide(self, proposal_id: str, status: str, note: str = "") -> dict[str, Any] | None:
        if status not in STATUSES:
            raise ValueError(f"invalid probation status {status!r}")
        self.db.execute(
            "UPDATE artifact_probations SET status = ?, note = ?, decided_at = datetime('now') "
            "WHERE proposal_id = ?",
            (status, note, proposal_id),
        )
        self.db.commit()
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM artifact_probations WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM artifact_probations"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [dict(r) for r in self.db.execute(sql, tuple(params)).fetchall()]


__all__ = ["ArtifactProbationStore", "STATUSES"]
