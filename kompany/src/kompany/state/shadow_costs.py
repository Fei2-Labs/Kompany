"""Shadow cost store — API-equivalent value of subscription-billed calls.

PRD ``06-11-harness-execution-leg`` D2: when the active ModelSource bills
by subscription, the real marginal cost of an LLM call is 0 (the monthly
fee is the real expense), but we still record tokens + the API-equivalent
**shadow value** for quota pacing and worth-it retrospectives.

Hard invariant: rows in ``shadow_costs`` must NEVER affect the company
balance. This store has no ledger access by design — it can only insert
into and read from its own table.
"""

from __future__ import annotations

from kompany.core.run_context import current_run_id
from kompany.state.database import Database


class ShadowCostStore:
    """Queryable record of shadow (never-balance-affecting) LLM spend."""

    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        *,
        model: str,
        tokens_in: int,
        tokens_out: int,
        shadow_value_usd: float,
        description: str,
        run_id: str | None = None,
    ) -> dict:
        """Insert one shadow entry. Returns the stored row as a dict."""
        rid = run_id if run_id is not None else current_run_id()
        cursor = self.db.execute(
            """INSERT INTO shadow_costs
               (run_id, model, tokens_in, tokens_out,
                shadow_value_usd, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                rid,
                model,
                int(tokens_in or 0),
                int(tokens_out or 0),
                float(shadow_value_usd or 0.0),
                description,
            ),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT * FROM shadow_costs WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)

    def get_recent(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM shadow_costs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def total(self, run_id: str | None = None) -> float:
        """Total shadow value in USD — overall, or for one run.

        Feeds the subscription-mode cap story (turns / shadow-value caps
        per PRD D2) and worth-it retrospectives.
        """
        if run_id is not None:
            row = self.db.execute(
                "SELECT COALESCE(SUM(shadow_value_usd), 0.0) AS total "
                "FROM shadow_costs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        else:
            row = self.db.execute(
                "SELECT COALESCE(SUM(shadow_value_usd), 0.0) AS total "
                "FROM shadow_costs"
            ).fetchone()
        return float(row["total"] or 0.0) if row else 0.0


__all__ = ["ShadowCostStore"]
