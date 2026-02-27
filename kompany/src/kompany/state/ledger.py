"""Company ledger — tracks all financial transactions including AI costs."""

from __future__ import annotations

from kompany.state.database import Database
from kompany.state.models import LedgerCategory, LedgerEntry


class Ledger:
    """Financial ledger backed by SQLite."""

    def __init__(self, db: Database):
        self.db = db

    def get_balance(self) -> float:
        row = self.db.execute(
            "SELECT balance_after FROM ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return float(row["balance_after"]) if row else 0.0

    def record(
        self,
        amount: float,
        description: str,
        category: LedgerCategory,
        directive_id: str | None = None,
        project_id: str | None = None,
        approved_by: str | None = None,
    ) -> LedgerEntry:
        balance = self.get_balance() + amount
        self.db.execute(
            """INSERT INTO ledger
               (amount, balance_after, description, category,
                directive_id, project_id, approved_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (amount, balance, description, category.value,
             directive_id, project_id, approved_by),
        )
        self.db.commit()
        return LedgerEntry(
            amount=amount,
            balance_after=balance,
            description=description,
            category=category,
            directive_id=directive_id,
            project_id=project_id,
            approved_by=approved_by,
        )

    def record_ai_cost(
        self, amount_usd: float, description: str, directive_id: str | None = None
    ) -> LedgerEntry:
        """Record an AI/LLM cost as a real operational expense."""
        return self.record(
            amount=-abs(amount_usd),
            description=f"AI: {description}",
            category=LedgerCategory.AI_COST,
            directive_id=directive_id,
            approved_by="auto",
        )

    def get_totals(self) -> dict[str, float]:
        """Get totals by category."""
        rows = self.db.execute(
            "SELECT category, SUM(amount) as total FROM ledger GROUP BY category"
        ).fetchall()
        return {row["category"]: float(row["total"]) for row in rows}

    def get_recent(self, limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
