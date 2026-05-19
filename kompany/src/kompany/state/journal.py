"""Decision journal — logs every directive outcome."""

from __future__ import annotations

import json

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import Decision


class Journal:
    """Decision journal backed by SQLite."""

    def __init__(self, db: Database):
        self.db = db

    def log(self, decision: Decision, run_id: str | None = None) -> None:
        rid = run_id if run_id is not None else current_run_id()
        self.db.execute(
            """INSERT INTO decisions
               (id, directive_id, directive_type, raw_input,
                classification, result, agents_involved,
                total_ai_cost, duration_seconds, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision.id,
                decision.directive_id,
                decision.directive_type,
                decision.raw_input,
                json.dumps(decision.classification),
                json.dumps(decision.result),
                json.dumps(decision.agents_involved),
                decision.total_ai_cost,
                decision.duration_seconds,
                rid,
            ),
        )
        self.db.commit()
