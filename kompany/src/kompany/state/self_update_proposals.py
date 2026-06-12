"""Self-update proposal store (06-12-self-update-pipeline PRD D3).

One row per propose attempt: instruction, clone branch, post-session
tier, diff evidence, test results, and lifecycle status. Schema lives in
``kompany/state/database.py`` ``_migrate()`` (shadow_costs precedent).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from kompany.state.database import Database

PROPOSAL_STATUSES: frozenset[str] = frozenset({
    "running",
    "proposed",
    "approved",
    "rejected",
    "aborted_t3",
    "failed",
})

# Columns writable through ``update()`` — everything except the id and
# the insert-time timestamps.
_UPDATABLE_FIELDS: frozenset[str] = frozenset({
    "instruction",
    "branch",
    "tier",
    "files_changed",
    "diff_stat",
    "test_summary",
    "session_id",
    "vehicle",
    "status",
    "approval_id",
    "cost_usd",
})


class SelfUpdateProposalStore:
    """SQLite-backed store for ``self_update_proposals`` rows."""

    def __init__(self, db: Database):
        self.db = db

    def create(
        self, instruction: str, branch: str = "", vehicle: str = ""
    ) -> str:
        """Insert a new ``running`` proposal; returns its short id.

        An empty ``branch`` defaults to ``self-update/<id>`` (the branch
        name embeds the id, which only exists after generation here).
        """
        proposal_id = uuid4().hex[:8]
        if not branch:
            branch = f"self-update/{proposal_id}"
        self.db.execute(
            """INSERT INTO self_update_proposals
                   (id, instruction, branch, vehicle, status)
               VALUES (?, ?, ?, ?, 'running')""",
            (proposal_id, instruction, branch, vehicle),
        )
        self.db.commit()
        return proposal_id

    def update(self, proposal_id: str, **fields: Any) -> dict | None:
        """Update writable fields; returns the fresh row dict."""
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(
                f"unknown self_update_proposals fields: {sorted(unknown)}"
            )
        status = fields.get("status")
        if status is not None and status not in PROPOSAL_STATUSES:
            raise ValueError(
                f"invalid status {status!r}; expected one of "
                f"{sorted(PROPOSAL_STATUSES)}"
            )
        if not fields:
            return self.get(proposal_id)
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key == "files_changed" and not isinstance(value, (str, type(None))):
                value = json.dumps(list(value))
            sets.append(f"{key} = ?")
            params.append(value)
        sets.append("updated_at = datetime('now')")
        params.append(proposal_id)
        self.db.execute(
            f"UPDATE self_update_proposals SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        self.db.commit()
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM self_update_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, limit: int = 20) -> list[dict]:
        """Newest-first proposal rows."""
        rows = self.db.execute(
            "SELECT * FROM self_update_proposals "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: Any) -> dict:
        result = dict(row)
        raw = result.get("files_changed")
        try:
            files = json.loads(raw) if raw else []
            if not isinstance(files, list):
                files = [files]
        except (TypeError, ValueError):
            files = [raw]
        result["files_changed"] = files
        return result


__all__ = ["PROPOSAL_STATUSES", "SelfUpdateProposalStore"]
