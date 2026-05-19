"""Audit log persistence for engine-level events."""

from __future__ import annotations

import json
from typing import Any

from kompany.core.run_context import current_run_id
from kompany.state.database import Database


class AuditLog:
    """Append-only audit log backed by SQLite."""

    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        event_type: str,
        action: str,
        detail: dict[str, Any] | str | None = None,
        agent_role: str | None = None,
        directive_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        detail_text = json.dumps(detail) if isinstance(detail, dict) else detail
        rid = run_id if run_id is not None else current_run_id()
        self.db.execute(
            """INSERT INTO audit_log
               (event_type, agent_role, action, detail,
                directive_id, project_id, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_type, agent_role, action, detail_text,
             directive_id, project_id, rid),
        )
        self.db.commit()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
