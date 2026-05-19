"""Debate persistence — captures full multi-agent debate transcripts.

Debates are the heaviest reasoning artefact in the system: ``CoS`` runs
multiple rounds of position/rebuttal/synthesis, then the ``CEO`` writes a
decision. Historically the engine returned a formatted text blob and threw
the structured data away. This module persists the structured form into
``debates``, so the self-learning episode materializer (and any future
"why did we decide that?" tool) can replay the reasoning chain.

A debate row is *referenced* from ``project_episodes.payload_json`` via
``debate_ids: [...]`` — never embedded — because a single strategic debate
may inform multiple directives / projects, and the raw JSON can be sizeable.
"""

from __future__ import annotations

import json
from uuid import uuid4

from kompany.core.debate_models import (
    AgentPosition,
    CEODecision,
    DebateSynthesis,
)
from kompany.core.run_context import current_run_id
from kompany.state.database import Database


def _short_id() -> str:
    """8-char hex id matching ``state.models._short_id`` convention."""
    return uuid4().hex[:8]


class Debates:
    """CRUD for the ``debates`` table."""

    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        rounds: list[list[AgentPosition]],
        synthesis: DebateSynthesis | None,
        decision: CEODecision | None,
        directive_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        """Persist a debate transcript and return its id.

        ``rounds`` is the list-of-lists structure produced by
        :class:`~kompany.core.debate.DebateEngine`. Pydantic models are
        serialized via ``model_dump`` to keep the on-disk JSON readable.
        """
        debate_id = _short_id()
        rid = run_id if run_id is not None else current_run_id()
        rounds_payload = [
            [p.model_dump(mode="json") for p in rnd] for rnd in rounds
        ]
        synthesis_payload = (
            synthesis.model_dump(mode="json") if synthesis is not None else None
        )
        decision_payload = (
            decision.model_dump(mode="json") if decision is not None else None
        )
        self.db.execute(
            """INSERT INTO debates
               (id, directive_id, project_id, rounds_json,
                synthesis_json, decision_json, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                debate_id,
                directive_id,
                project_id,
                json.dumps(rounds_payload),
                json.dumps(synthesis_payload) if synthesis_payload else None,
                json.dumps(decision_payload) if decision_payload else None,
                rid,
            ),
        )
        self.db.commit()
        return debate_id

    def get(self, debate_id: str) -> dict | None:
        """Return one debate as a dict with JSON columns parsed, or None."""
        row = self.db.execute(
            "SELECT * FROM debates WHERE id = ?", (debate_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_project(self, project_id: str) -> list[dict]:
        """Return all debates linked to a project, newest first."""
        rows = self.db.execute(
            "SELECT * FROM debates WHERE project_id = ? "
            "ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_by_directive(self, directive_id: str) -> list[dict]:
        """Return all debates linked to a directive, newest first."""
        rows = self.db.execute(
            "SELECT * FROM debates WHERE directive_id = ? "
            "ORDER BY created_at DESC",
            (directive_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row) -> dict:
        return {
            "id": row["id"],
            "directive_id": row["directive_id"],
            "project_id": row["project_id"],
            "rounds": json.loads(row["rounds_json"]) if row["rounds_json"] else [],
            "synthesis": (
                json.loads(row["synthesis_json"]) if row["synthesis_json"] else None
            ),
            "decision": (
                json.loads(row["decision_json"]) if row["decision_json"] else None
            ),
            "run_id": row["run_id"],
            "created_at": row["created_at"],
        }
