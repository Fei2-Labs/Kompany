"""Audit log persistence for engine-level events."""

from __future__ import annotations

import json
from typing import Any

from kompany.core.event_hub import get_event_hub
from kompany.core.run_context import current_run_id
from kompany.state.database import Database

# SSE payload cap: large detail blobs get truncated to keep the live feed
# from carrying multi-MB audit dumps. The on-disk row keeps the full JSON.
_SSE_DETAIL_MAX_CHARS = 2048


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

        # Fan out to live SSE clients. Truncate large detail payloads so
        # the browser feed stays under ~2KB per event.
        truncated_detail = detail_text
        if truncated_detail and len(truncated_detail) > _SSE_DETAIL_MAX_CHARS:
            truncated_detail = truncated_detail[:_SSE_DETAIL_MAX_CHARS] + "...[truncated]"
        try:
            get_event_hub().publish(
                f"audit.{event_type}",
                {
                    "event_type": event_type,
                    "action": action,
                    "agent_role": agent_role,
                    "detail": truncated_detail,
                    "directive_id": directive_id,
                    "project_id": project_id,
                    "run_id": rid,
                },
            )
        except Exception:  # pragma: no cover — best-effort live feed
            pass

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
