"""Health-event store for the resilience watchdog.

Each row is one observation by the watchdog: an LLM call went silent, a
task was forgotten, a retry exhausted, or a previously-silent run came
back to life. Rows are first-class and persist for episode
materialization, replay, and operator review.

See ``.trellis/tasks/05-18-resilience-foundation/prd.md`` for the
design and ``kompany/state/database.py`` for the schema migration.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from kompany.core.run_context import current_run_id
from kompany.state.database import Database

# Valid enum-like values. Kept as module-level constants rather than
# Enums so SQL writes can pass the raw string without a ``.value``
# conversion at every call site.
HEALTH_KINDS: frozenset[str] = frozenset({
    "stranded_todo",
    "stranded_in_progress",
    "silent_run",
    "recovered",
    "retry_exhausted",
    # Fires when projected burn through ``deadline`` exceeds available cash.
    "runway_alert",
    # Fires when CoS retrospective detects forbidden-synonym usage against
    # the company glossary. See ``cos_glossary_scan`` + the
    # ``glossary_review`` approval flow.
    "glossary_drift_alert",
})

HEALTH_STATUSES: frozenset[str] = frozenset({
    "open",
    "resolved",
    "snoozed",
    "dismissed",
})

PLAYER_ACTIONS: frozenset[str] = frozenset({
    "continue",
    "snooze",
    "dismiss",
})


def _event_id() -> str:
    """Short, URL-safe health event id (``he_<hex>``)."""
    return f"he_{secrets.token_hex(6)}"


class HealthEvents:
    """SQLite-backed store for ``health_events`` rows."""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record(
        self,
        kind: str,
        task_id: str | None = None,
        project_id: str | None = None,
        detail: dict[str, Any] | None = None,
        run_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert one event and return its row dict.

        ``run_id`` defaults to the active :func:`current_run_id` so the
        scanner thread and the inline LLM wrapper both land the same id
        when they share a scope.
        """
        if kind not in HEALTH_KINDS:
            raise ValueError(
                f"invalid health event kind {kind!r}; expected one of {sorted(HEALTH_KINDS)}"
            )
        eid = event_id or _event_id()
        rid = run_id if run_id is not None else current_run_id()
        detail_json = json.dumps(detail) if detail else None
        self.db.execute(
            """INSERT INTO health_events
                   (id, kind, task_id, project_id, run_id, detail_json, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            (eid, kind, task_id, project_id, rid, detail_json),
        )
        self.db.commit()
        row = self.get(eid)
        assert row is not None  # just inserted
        return row

    def resolve(
        self,
        event_id: str,
        action: str,
        resolved_by: str = "player",
        snooze_minutes: int | None = None,
    ) -> dict[str, Any] | None:
        """Apply a player action to an event.

        - ``continue`` -> status='resolved' (caller is responsible for any
          re-trigger work; this only marks the bookkeeping).
        - ``snooze``   -> status='snoozed' and ``snoozed_until`` set to
          ``snooze_minutes`` from now (must be > 0).
        - ``dismiss``  -> status='dismissed' (operator says false-positive).

        Returns the updated row, or ``None`` if the event doesn't exist.
        Idempotent: re-resolving an already-resolved event with the same
        action is a no-op (current row is returned).
        """
        if action not in PLAYER_ACTIONS:
            raise ValueError(
                f"invalid action {action!r}; expected one of {sorted(PLAYER_ACTIONS)}"
            )
        existing = self.get(event_id)
        if existing is None:
            return None
        if action == "continue":
            new_status = "resolved"
            self.db.execute(
                """UPDATE health_events
                       SET status = ?, resolved_by = ?,
                           resolved_at = datetime('now'),
                           snoozed_until = NULL
                   WHERE id = ?""",
                (new_status, resolved_by, event_id),
            )
        elif action == "snooze":
            if snooze_minutes is None or snooze_minutes <= 0:
                raise ValueError("snooze_minutes must be > 0 for snooze action")
            new_status = "snoozed"
            self.db.execute(
                """UPDATE health_events
                       SET status = ?, resolved_by = ?,
                           snoozed_until = datetime('now', ?),
                           resolved_at = NULL
                   WHERE id = ?""",
                (new_status, resolved_by, f"+{int(snooze_minutes)} minutes", event_id),
            )
        else:  # dismiss
            new_status = "dismissed"
            self.db.execute(
                """UPDATE health_events
                       SET status = ?, resolved_by = ?,
                           resolved_at = datetime('now'),
                           snoozed_until = NULL
                   WHERE id = ?""",
                (new_status, resolved_by, event_id),
            )
        self.db.commit()
        return self.get(event_id)

    def close_open(
        self,
        kind: str,
        task_id: str | None,
        run_id: str | None,
    ) -> int:
        """Mark all ``open`` events of ``kind`` for ``(task_id, run_id)`` resolved.

        Used when a previously-silent LLM call recovers: the watchdog
        writes a ``recovered`` event and then calls ``close_open`` to
        flip the matching ``silent_run`` row to ``resolved`` (without
        attribution — system-resolved, not player-resolved).
        Returns the number of rows updated.
        """
        cur = self.db.execute(
            """UPDATE health_events
                   SET status = 'resolved',
                       resolved_by = COALESCE(resolved_by, 'system'),
                       resolved_at = datetime('now')
               WHERE kind = ? AND status = 'open'
                 AND (task_id IS ? OR task_id = ?)
                 AND (run_id IS ? OR run_id = ?)""",
            (kind, task_id, task_id, run_id, run_id),
        )
        self.db.commit()
        return cur.rowcount or 0

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, event_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM health_events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(
        self,
        status: str | None = None,
        project_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List events, newest-first, optionally filtered."""
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            if status not in HEALTH_STATUSES:
                raise ValueError(
                    f"invalid status {status!r}; expected one of {sorted(HEALTH_STATUSES)}"
                )
            clauses.append("status = ?")
            params.append(status)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if kind is not None:
            if kind not in HEALTH_KINDS:
                raise ValueError(
                    f"invalid kind {kind!r}; expected one of {sorted(HEALTH_KINDS)}"
                )
            clauses.append("kind = ?")
            params.append(kind)
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = self.db.execute(
            f"SELECT * FROM health_events{where_sql} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        """All events tied to ``project_id`` (used by the episode materializer)."""
        rows = self.db.execute(
            """SELECT * FROM health_events WHERE project_id = ?
               ORDER BY created_at ASC, id ASC""",
            (project_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def find_active_snoozed(
        self,
        kind: str,
        task_id: str | None,
    ) -> dict[str, Any] | None:
        """Return a still-snoozed event matching ``(kind, task_id)``, or None.

        Scanner uses this to skip re-warning during a snooze window.
        """
        row = self.db.execute(
            """SELECT * FROM health_events
               WHERE kind = ? AND status = 'snoozed'
                 AND (task_id IS ? OR task_id = ?)
                 AND snoozed_until IS NOT NULL
                 AND snoozed_until > datetime('now')
               ORDER BY created_at DESC LIMIT 1""",
            (kind, task_id, task_id),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        try:
            detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
            if not isinstance(detail, dict):
                detail = {"value": detail}
        except (TypeError, ValueError):
            detail = {"raw": row["detail_json"]}
        return {
            "id": row["id"],
            "kind": row["kind"],
            "task_id": row["task_id"],
            "project_id": row["project_id"],
            "run_id": row["run_id"],
            "detail": detail,
            "status": row["status"],
            "resolved_by": row["resolved_by"],
            "resolved_at": row["resolved_at"],
            "snoozed_until": row["snoozed_until"],
            "created_at": row["created_at"],
        }


__all__ = [
    "HealthEvents",
    "HEALTH_KINDS",
    "HEALTH_STATUSES",
    "PLAYER_ACTIONS",
]
