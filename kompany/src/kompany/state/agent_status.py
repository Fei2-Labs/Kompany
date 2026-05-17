"""Agent activity status persistence."""

from __future__ import annotations

from kompany.state.database import Database


class AgentStatusStore:
    """Stores current activity status for each agent."""

    def __init__(self, db: Database):
        self.db = db

    def set(
        self,
        agent_role: str,
        status: str,
        current_task: str | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO agent_status (agent_role, status, current_task, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(agent_role) DO UPDATE SET
                   status = excluded.status,
                   current_task = excluded.current_task,
                   updated_at = datetime('now')""",
            (agent_role, status, current_task),
        )
        self.db.commit()

    def get(self, agent_role: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM agent_status WHERE agent_role = ?",
            (agent_role,),
        ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM agent_status ORDER BY agent_role",
        ).fetchall()
        return [dict(row) for row in rows]
