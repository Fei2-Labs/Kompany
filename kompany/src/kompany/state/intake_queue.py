"""Intake queue store — the dev-inbox layer of the concurrent runtime.

ADR-0005 decision 1: an append-only queue of incoming work that survives
restarts and is triaged/routed to a lane. ``pop_for_lane`` atomically
claims the oldest ``queued`` row (status ``queued`` -> ``assigned``) so
two concurrent lanes never claim the same item — the lane-worker
contract's "two lanes never write the same record".

Thin SQLite wrapper mirroring :class:`kompany.state.daemon_ticks.
DaemonTickStore`. Table created in
``kompany.state.database_parts.migrations``.
"""

from __future__ import annotations

import json
from typing import Any

from kompany.state.database import Database

# Status enum (kept as plain strings — SQL writes pass the raw value).
STATUS_QUEUED = "queued"
STATUS_ASSIGNED = "assigned"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class IntakeQueueStore:
    """Durable producer/consumer queue of pending autonomous work."""

    def __init__(self, db: Database):
        self.db = db

    def enqueue(
        self,
        project_id: str | None,
        task_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Append one work item (status ``queued``). Returns its id."""
        cursor = self.db.execute(
            """INSERT INTO intake_work_items
                   (project_id, task_id, status, detail_json)
               VALUES (?, ?, ?, ?)""",
            (
                project_id,
                task_id,
                STATUS_QUEUED,
                json.dumps(detail) if detail is not None else None,
            ),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def pop_for_lane(self, lane_id: str) -> dict[str, Any] | None:
        """Atomically claim the oldest ``queued`` item for ``lane_id``.

        Uses a conditional UPDATE keyed on the row id selected inside the
        same statement so two concurrent ``pop_for_lane`` calls cannot
        both flip the same row: SQLite serializes writes, and the
        ``status = 'queued'`` predicate means the second writer's UPDATE
        matches zero rows. Returns the claimed row dict, or ``None`` when
        the queue is empty.
        """
        cursor = self.db.execute(
            """UPDATE intake_work_items
                   SET status = ?, lane_id = ?, assigned_at = datetime('now')
               WHERE id = (
                   SELECT id FROM intake_work_items
                   WHERE status = ?
                   ORDER BY id ASC LIMIT 1
               )""",
            (STATUS_ASSIGNED, lane_id, STATUS_QUEUED),
        )
        self.db.commit()
        if not cursor.rowcount:
            return None
        row = self.db.execute(
            """SELECT * FROM intake_work_items
               WHERE status = ? AND lane_id = ?
               ORDER BY assigned_at DESC, id DESC LIMIT 1""",
            (STATUS_ASSIGNED, lane_id),
        ).fetchone()
        return self._decode(dict(row)) if row else None

    def mark_completed(
        self, item_id: int, outcome: dict[str, Any] | None = None
    ) -> None:
        """Mark a claimed item completed, merging ``outcome`` into detail."""
        self._finish(item_id, STATUS_COMPLETED, outcome)

    def mark_failed(
        self, item_id: int, reason: str | dict[str, Any] | None = None
    ) -> None:
        """Mark a claimed item failed. ADR-0005: a lane that cannot do work
        because the model is down records FAILURE here, never silent
        completion."""
        detail = {"reason": reason} if isinstance(reason, str) else reason
        self._finish(item_id, STATUS_FAILED, detail)

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        """All items (oldest-first), optionally filtered by ``status``."""
        if status is None:
            rows = self.db.execute(
                "SELECT * FROM intake_work_items ORDER BY id ASC"
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM intake_work_items WHERE status = ? "
                "ORDER BY id ASC",
                (status,),
            ).fetchall()
        return [self._decode(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _finish(
        self, item_id: int, status: str, detail: dict[str, Any] | None
    ) -> None:
        self.db.execute(
            """UPDATE intake_work_items
                   SET status = ?, completed_at = datetime('now'),
                       detail_json = COALESCE(?, detail_json)
               WHERE id = ?""",
            (
                status,
                json.dumps(detail) if detail is not None else None,
                int(item_id),
            ),
        )
        self.db.commit()

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("detail_json")
        if raw:
            try:
                row["detail"] = json.loads(raw)
            except (TypeError, ValueError):
                row["detail"] = None
        else:
            row["detail"] = None
        return row


__all__ = [
    "IntakeQueueStore",
    "STATUS_QUEUED",
    "STATUS_ASSIGNED",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
]
