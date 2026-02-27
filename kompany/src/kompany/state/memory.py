"""Agent memory — per-agent learning stored in SQLite."""

from __future__ import annotations

from kompany.state.database import Database


class AgentMemory:
    """Store and retrieve per-agent learnings across directives."""

    def __init__(self, db: Database):
        self.db = db

    def remember(
        self,
        agent_role: str,
        content: str,
        category: str = "observation",
        context: str | None = None,
        directive_id: str | None = None,
    ) -> None:
        """Store a memory for an agent."""
        self.db.execute(
            """INSERT INTO agent_memories (agent_role, category, content, context, directive_id)
               VALUES (?, ?, ?, ?, ?)""",
            (agent_role, category, content, context, directive_id),
        )
        self.db.commit()

    def recall(
        self,
        agent_role: str,
        limit: int = 10,
        category: str | None = None,
    ) -> list[dict]:
        """Retrieve recent memories for an agent."""
        if category:
            rows = self.db.execute(
                """SELECT content, category, context, directive_id, created_at
                   FROM agent_memories WHERE agent_role = ? AND category = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (agent_role, category, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT content, category, context, directive_id, created_at
                   FROM agent_memories WHERE agent_role = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (agent_role, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def recall_text(self, agent_role: str, limit: int = 5) -> str:
        """Return memories formatted as context text for prompt injection."""
        memories = self.recall(agent_role, limit=limit)
        if not memories:
            return ""
        lines = [f"- [{m['category']}] {m['content']}" for m in memories]
        return "Prior learnings:\n" + "\n".join(lines)

    def count(self, agent_role: str) -> int:
        """Count total memories for an agent."""
        row = self.db.execute(
            "SELECT COUNT(*) as c FROM agent_memories WHERE agent_role = ?",
            (agent_role,),
        ).fetchone()
        return int(row["c"])
