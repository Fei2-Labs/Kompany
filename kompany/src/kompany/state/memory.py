"""Agent memory — per-agent learning stored in SQLite."""

from __future__ import annotations

import json
from typing import Any

from kompany.core.run_context import current_run_id
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
        run_id: str | None = None,
    ) -> int:
        """Store a memory for an agent. Returns the inserted memory id."""
        rid = run_id if run_id is not None else current_run_id()
        cursor = self.db.execute(
            """INSERT INTO agent_memories
               (agent_role, category, knowledge_type, content, context,
                directive_id, valid_until, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent_role, category, knowledge_type, content, context,
             directive_id, valid_until, rid),
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

    def upsert_by_pattern_key(
        self,
        agent_role: str,
        pattern_key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        category: str = "experiential",
        knowledge_type: str = "experiential",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """UPSERT an experiential memory keyed by ``(agent_role, pattern_key)``.

        Used by the P1 distillation flow: each :class:`DistilledPattern`
        carries a stable ``pattern_key`` so re-running ``kompany distill``
        on overlapping evidence refreshes the existing memory row instead
        of fanning out duplicates.

        Returns a dict ``{id, action}`` where ``action`` is ``"inserted"``
        for a brand-new pattern or ``"updated"`` when an existing row was
        refreshed. ``content`` / ``metadata`` / ``run_id`` / ``updated_at``
        are overwritten on update; ``created_at`` is preserved.
        """
        if not pattern_key:
            raise ValueError("pattern_key must be non-empty")
        rid = run_id if run_id is not None else current_run_id()
        metadata_text = json.dumps(metadata) if metadata is not None else None

        existing = self.db.execute(
            "SELECT id FROM agent_memories WHERE agent_role = ? AND pattern_key = ?",
            (agent_role, pattern_key),
        ).fetchone()

        if existing is not None:
            self.db.execute(
                """UPDATE agent_memories
                   SET content = ?, category = ?, knowledge_type = ?,
                       metadata = ?, run_id = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (content, category, knowledge_type, metadata_text, rid,
                 int(existing["id"])),
            )
            self.db.commit()
            return {"id": int(existing["id"]), "action": "updated"}

        cursor = self.db.execute(
            """INSERT INTO agent_memories
               (agent_role, category, knowledge_type, content,
                pattern_key, metadata, run_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (agent_role, category, knowledge_type, content,
             pattern_key, metadata_text, rid),
        )
        self.db.commit()
        return {"id": int(cursor.lastrowid), "action": "inserted"}

    def get_by_pattern_key(
        self,
        agent_role: str,
        pattern_key: str,
    ) -> dict[str, Any] | None:
        """Fetch one pattern-keyed memory or ``None`` if absent."""
        row = self.db.execute(
            """SELECT id, agent_role, category, knowledge_type, content,
                      context, directive_id, pattern_key, metadata,
                      created_at, updated_at, valid_until, run_id
               FROM agent_memories
               WHERE agent_role = ? AND pattern_key = ?""",
            (agent_role, pattern_key),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("metadata"):
            try:
                result["metadata"] = json.loads(result["metadata"])
            except (TypeError, ValueError):
                pass
        return result

    def count(self, agent_role: str) -> int:
        """Count total memories for an agent."""
        row = self.db.execute(
            "SELECT COUNT(*) as c FROM agent_memories WHERE agent_role = ?",
            (agent_role,),
        ).fetchone()
        return int(row["c"])
