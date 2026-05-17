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
        knowledge_type: str = "experiential",
        valid_until: str | None = None,
    ) -> int:
        """Store a memory for an agent. Returns the inserted memory id."""
        cursor = self.db.execute(
            """INSERT INTO agent_memories
               (agent_role, category, knowledge_type, content, context,
                directive_id, valid_until)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (agent_role, category, knowledge_type, content, context,
             directive_id, valid_until),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def recall(
        self,
        agent_role: str,
        limit: int = 10,
        category: str | None = None,
        include_stale: bool = False,
        knowledge_type: str | None = None,
    ) -> list[dict]:
        """Retrieve recent memories for an agent.

        By default excludes memories whose ``valid_until`` is set and has
        already passed. Pass ``include_stale=True`` to include them.
        """
        clauses = ["agent_role = ?"]
        params: list = [agent_role]
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        if knowledge_type is not None:
            clauses.append("knowledge_type = ?")
            params.append(knowledge_type)
        if not include_stale:
            clauses.append("(valid_until IS NULL OR valid_until > datetime('now'))")
        params.append(limit)
        sql = (
            "SELECT id, content, category, knowledge_type, context, "
            "directive_id, created_at, valid_until "
            "FROM agent_memories WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT ?"
        )
        rows = self.db.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def mark_stale(self, memory_id: int) -> None:
        """Mark a memory stale by setting ``valid_until`` to now."""
        self.db.execute(
            "UPDATE agent_memories SET valid_until = datetime('now') WHERE id = ?",
            (memory_id,),
        )
        self.db.commit()

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
