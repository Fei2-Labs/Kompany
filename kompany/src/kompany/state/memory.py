"""Agent memory — per-agent learning stored in SQLite."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from kompany.core.run_context import current_run_id
from kompany.state.database import Database

# Utility-weighted retrieval tuning (ported from Keel's mechanism #6).
# Memories created/updated/accessed within this window get RECENCY_BONUS,
# matching Keel's "+2 if touched in the last 7 days".
RECENCY_WINDOW = timedelta(days=7)
RECENCY_BONUS = 2.0
# Distilled patterns carry metadata.confidence in [0, 1]; weighting by 2
# lets a high-confidence pattern offset a missing recency bonus.
CONFIDENCE_WEIGHT = 2.0
# Scoring happens in Python, so cap the candidate rows fetched per recall.
CANDIDATE_POOL = 200


def _parse_ts(value: str | None) -> datetime | None:
    """Parse a SQLite ``datetime('now')`` timestamp; None if unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _query_tokens(query: str | None) -> list[str]:
    """Lowercase alphanumeric tokens of length >= 3, deduplicated."""
    if not query:
        return []
    seen: dict[str, None] = {}
    for tok in re.findall(r"[a-z0-9]+", query.lower()):
        if len(tok) >= 3:
            seen[tok] = None
    return list(seen)


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
        query: str | None = None,
        track_access: bool = False,
    ) -> list[dict]:
        """Retrieve memories for an agent, ranked by utility.

        Utility-weighted retrieval (ported from Keel's mechanism #6)::

            score = keyword_hits * 1.0       # query tokens found in content
                  + recency_bonus            # touched within 7 days: +2
                  + log1p(access_count)      # how often this memory was used
                  + 2 * metadata.confidence  # distilled-pattern confidence

        Ties fall back to ``created_at`` DESC, so with no ``query`` and no
        access history the ordering matches the legacy recency behaviour.
        Each returned row carries its ``score`` so ranking stays transparent.

        ``track_access=True`` bumps ``access_count`` / ``last_accessed_at``
        on the returned rows. Only the prompt-injection path should set it;
        browse/list callers must not distort the usage signal.

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
        params.append(max(limit, CANDIDATE_POOL))
        sql = (
            "SELECT id, content, category, knowledge_type, context, "
            "directive_id, created_at, valid_until, updated_at, metadata, "
            "access_count, last_accessed_at "
            "FROM agent_memories WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT ?"
        )
        rows = [dict(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

        tokens = _query_tokens(query)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for row in rows:
            row["score"] = self._utility_score(row, tokens, now)
        # Stable sorts: recency first (id breaks same-second ties), then
        # score, so equal scores keep the legacy created_at DESC order.
        rows.sort(key=lambda r: (r["created_at"] or "", r["id"]), reverse=True)
        rows.sort(key=lambda r: r["score"], reverse=True)
        top = rows[:limit]

        if track_access and top:
            ids = [r["id"] for r in top]
            placeholders = ",".join("?" * len(ids))
            self.db.execute(
                "UPDATE agent_memories SET access_count = access_count + 1, "
                "last_accessed_at = datetime('now') "
                f"WHERE id IN ({placeholders})",
                tuple(ids),
            )
            self.db.commit()
        return top

    @staticmethod
    def _utility_score(
        row: dict, tokens: list[str], now: datetime
    ) -> float:
        score = 0.0
        if tokens:
            content = (row.get("content") or "").lower()
            for tok in tokens:
                score += content.count(tok)
        last_touch = max(
            filter(
                None,
                (
                    _parse_ts(row.get("last_accessed_at")),
                    _parse_ts(row.get("updated_at")),
                    _parse_ts(row.get("created_at")),
                ),
            ),
            default=None,
        )
        if last_touch is not None and now - last_touch <= RECENCY_WINDOW:
            score += RECENCY_BONUS
        score += math.log1p(row.get("access_count") or 0)
        metadata = row.get("metadata")
        if metadata:
            try:
                confidence = float(json.loads(metadata).get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            score += CONFIDENCE_WEIGHT * max(0.0, min(1.0, confidence))
        return score

    def mark_stale(self, memory_id: int) -> None:
        """Mark a memory stale by setting ``valid_until`` to now."""
        self.db.execute(
            "UPDATE agent_memories SET valid_until = datetime('now') WHERE id = ?",
            (memory_id,),
        )
        self.db.commit()

    def recall_text(
        self, agent_role: str, limit: int = 5, query: str | None = None
    ) -> str:
        """Return memories formatted as context text for prompt injection.

        Pass the task description as ``query`` so retrieval favours relevant
        memories over merely recent ones. This path tracks access so memories
        that keep getting injected rank higher over time.
        """
        memories = self.recall(
            agent_role, limit=limit, query=query, track_access=True
        )
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
