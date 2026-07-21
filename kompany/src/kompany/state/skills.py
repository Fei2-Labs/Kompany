"""Learned-skill store — genericagent-style compounding memory (06-16 P5).

A *skill* is a crystallized SOP: the distilled "what worked" of a successful
agentic session, stored so the next similar task reuses it instead of
re-deriving it. This is the L3 "task skills" layer from the GenericAgent
5-layer no-embedding memory model, plus its L1 trigger-word INDEX.

Design (deliberately NOT embeddings / vectors):

- **Store** (``agent_skills`` table): one row per skill, each carrying
  ``name``, ``trigger_words`` (the keywords that route a task to it),
  ``when_to_use``, the ``sop`` markdown body, an optional ``code`` snippet,
  ``provenance`` (run_id / session_id), and ``created_count`` /
  ``used_count`` so refinement and retrieval-by-usage stay observable.
- **L1 INDEX**: :meth:`SkillStore.index_lines` builds a <=~30-line
  ``keyword -> skill names`` map, scanned cheaply at task start. No vector
  search; matching is plain trigger-word overlap.
- **Retrieval**: :meth:`SkillStore.retrieve` tokenizes a task/request and
  returns the top skills by trigger-word overlap (capped count), bumping
  ``used_count`` so reused skills surface first on ties.

This EXTENDS the existing memory layers (``AgentMemory`` rows /
``Episodes``) — it does not duplicate them: episodes are per-project
retrospective payloads; agent_memories are per-agent learnings/reflections;
skills are reusable cross-task *procedures* keyed by trigger words. A skill
also keeps a pointer back to its provenance run, never a copy of the episode.
"""

from __future__ import annotations

import json
import re
from typing import Any

from kompany.core.run_context import current_run_id
from kompany.state.database import Database

__all__ = ["SkillStore", "tokenize", "INDEX_MAX_LINES"]

# Hard cap on the L1 trigger-word index, matching GenericAgent's <=30-line
# discipline: the index must stay cheap to scan every task start.
INDEX_MAX_LINES = 30
# Retrieval defaults: cap how many skills and how much SOP body we inject so
# the "relevant past skills" block stays token-bounded.
DEFAULT_RETRIEVE_LIMIT = 3
SOP_INJECT_CHARS = 800


def tokenize(text: str | None) -> list[str]:
    """Lowercase alphanumeric tokens of length >= 3, deduplicated.

    Mirrors ``state.memory._query_tokens`` so trigger-word matching uses the
    same notion of a "word" as the rest of the memory layer.
    """
    if not text:
        return []
    seen: dict[str, None] = {}
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if len(tok) >= 3:
            seen[tok] = None
    return list(seen)


def _normalize_triggers(triggers: Any) -> list[str]:
    """Coerce a trigger spec (list or comma/space string) to clean tokens."""
    if triggers is None:
        return []
    if isinstance(triggers, str):
        raw = re.split(r"[,\s]+", triggers)
    else:
        raw = list(triggers)
    out: dict[str, None] = {}
    for item in raw:
        for tok in tokenize(str(item)):
            out[tok] = None
    return list(out)


class SkillStore:
    """Store and retrieve crystallized skills (trigger-word retrieval)."""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------

    def save(
        self,
        agent_role: str,
        name: str,
        trigger_words: Any,
        when_to_use: str,
        sop: str,
        code: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new skill or refine an existing one keyed by ``name``.

        Dedupe is by ``(agent_role, name)``: re-crystallizing the same skill
        does NOT fan out a duplicate row — it refreshes the SOP/triggers and
        bumps ``created_count`` (how many times the agent re-derived it), so
        repeated solving of one task type sharpens a single skill instead of
        littering near-identical ones.

        Returns ``{id, action}`` where ``action`` is ``"inserted"`` or
        ``"updated"``.
        """
        if not name:
            raise ValueError("skill name must be non-empty")
        triggers = _normalize_triggers(trigger_words) or tokenize(name)
        triggers_text = json.dumps(triggers)
        rid = run_id if run_id is not None else current_run_id()

        existing = self.db.execute(
            "SELECT id, created_count FROM agent_skills "
            "WHERE agent_role = ? AND name = ?",
            (agent_role, name),
        ).fetchone()

        if existing is not None:
            self.db.execute(
                """UPDATE agent_skills
                   SET trigger_words = ?, when_to_use = ?, sop = ?, code = ?,
                       run_id = ?, session_id = ?,
                       created_count = created_count + 1,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (triggers_text, when_to_use, sop, code, rid, session_id,
                 int(existing["id"])),
            )
            self.db.commit()
            return {"id": int(existing["id"]), "action": "updated"}

        cursor = self.db.execute(
            """INSERT INTO agent_skills
               (agent_role, name, trigger_words, when_to_use, sop, code,
                run_id, session_id, created_count, used_count, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, datetime('now'))""",
            (agent_role, name, triggers_text, when_to_use, sop, code,
             rid, session_id),
        )
        self.db.commit()
        return {"id": int(cursor.lastrowid), "action": "inserted"}

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    def get(self, agent_role: str, name: str) -> dict[str, Any] | None:
        """Fetch one skill by name, with ``trigger_words`` decoded to a list."""
        row = self.db.execute(
            "SELECT * FROM agent_skills WHERE agent_role = ? AND name = ?",
            (agent_role, name),
        ).fetchone()
        return self._hydrate(row)

    def list(self, agent_role: str) -> list[dict[str, Any]]:
        """All skills for an agent, newest first."""
        rows = self.db.execute(
            "SELECT * FROM agent_skills WHERE agent_role = ? "
            "ORDER BY created_at DESC",
            (agent_role,),
        ).fetchall()
        return [self._hydrate(r) for r in rows if r is not None]

    def count(self, agent_role: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS c FROM agent_skills WHERE agent_role = ?",
            (agent_role,),
        ).fetchone()
        return int(row["c"])

    def index_lines(self, agent_role: str) -> list[str]:
        """Build the L1 trigger-word INDEX (<= ~30 lines, no embeddings).

        One line per trigger word -> the skill names it routes to. The map is
        sorted by usage so the most-reused triggers appear first, then capped
        at :data:`INDEX_MAX_LINES` to keep it cheap to scan at task start.
        """
        keyword_to_skills: dict[str, list[str]] = {}
        keyword_weight: dict[str, int] = {}
        for skill in self.list(agent_role):
            weight = int(skill.get("used_count") or 0)
            for kw in skill.get("trigger_words") or []:
                keyword_to_skills.setdefault(kw, [])
                if skill["name"] not in keyword_to_skills[kw]:
                    keyword_to_skills[kw].append(skill["name"])
                keyword_weight[kw] = max(keyword_weight.get(kw, 0), weight)
        ordered = sorted(
            keyword_to_skills,
            key=lambda k: (-keyword_weight.get(k, 0), k),
        )
        lines: list[str] = []
        for kw in ordered[:INDEX_MAX_LINES]:
            lines.append(f"{kw}: {', '.join(keyword_to_skills[kw])}")
        return lines

    def retrieve(
        self,
        agent_role: str,
        query: str | None,
        limit: int = DEFAULT_RETRIEVE_LIMIT,
        track_use: bool = False,
    ) -> list[dict[str, Any]]:
        """Top skills matching ``query`` by trigger-word overlap.

        NO embeddings: tokenize the request, score each skill by how many of
        its trigger words appear in the request (ties broken by ``used_count``
        then recency), and return up to ``limit`` matches. A skill with zero
        overlap is never returned. ``track_use=True`` bumps ``used_count`` on
        the returned skills (only the loop-injection path sets it).
        """
        tokens = set(tokenize(query))
        if not tokens:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for skill in self.list(agent_role):
            triggers = set(skill.get("trigger_words") or [])
            overlap = len(tokens & triggers)
            if overlap:
                scored.append((overlap, skill))
        scored.sort(
            key=lambda pair: (
                pair[0],
                int(pair[1].get("used_count") or 0),
                pair[1].get("created_at") or "",
            ),
            reverse=True,
        )
        top = [skill for _, skill in scored[:limit]]
        if track_use and top:
            ids = [s["id"] for s in top]
            placeholders = ",".join("?" * len(ids))
            self.db.execute(
                "UPDATE agent_skills SET used_count = used_count + 1, "
                "last_used_at = datetime('now') "
                f"WHERE id IN ({placeholders})",
                tuple(ids),
            )
            self.db.commit()
            for s in top:
                s["used_count"] = int(s.get("used_count") or 0) + 1
        return top

    def retrieve_text(
        self, agent_role: str, query: str | None,
        limit: int = DEFAULT_RETRIEVE_LIMIT,
    ) -> str:
        """Render matching skills as a token-bounded prompt block.

        This is what the agentic loop injects as "relevant past skills". Each
        skill shows its name, when-to-use, and a length-capped SOP body so a
        long playbook never blows the context budget.
        """
        skills = self.retrieve(agent_role, query, limit=limit, track_use=True)
        if not skills:
            return ""
        lines = ["Relevant past skills (reuse these — you solved this before):"]
        for s in skills:
            body = (s.get("sop") or "").strip()
            if len(body) > SOP_INJECT_CHARS:
                body = body[:SOP_INJECT_CHARS] + " …[truncated]"
            lines.append(f"\n## SKILL: {s['name']}")
            if s.get("when_to_use"):
                lines.append(f"When to use: {s['when_to_use']}")
            lines.append(body)
            if s.get("code"):
                lines.append(f"```\n{s['code']}\n```")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hydrate(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        triggers = result.get("trigger_words")
        if isinstance(triggers, str):
            try:
                result["trigger_words"] = json.loads(triggers)
            except (TypeError, ValueError):
                result["trigger_words"] = []
        return result
