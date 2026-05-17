"""Checkpoint persistence for resumable project execution."""

from __future__ import annotations

import json
from typing import Any

from kompany.state.database import Database


class CheckpointStore:
    """Stores execution checkpoints in SQLite."""

    def __init__(self, db: Database):
        self.db = db

    def save(
        self,
        project_id: str,
        state: dict[str, Any],
        task_id: str | None = None,
        step_index: int = 0,
    ) -> None:
        self.db.execute(
            """INSERT INTO checkpoints (project_id, task_id, step_index, state)
               VALUES (?, ?, ?, ?)""",
            (project_id, task_id, step_index, json.dumps(state)),
        )
        self.db.commit()

    def latest(self, project_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT * FROM checkpoints
               WHERE project_id = ?
               ORDER BY id DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["state"] = json.loads(data["state"])
        return data
