"""Cost tracker — records every LLM call as a real expense in the ledger."""

from __future__ import annotations

from kompany.core.run_context import current_run_id
from kompany.llm.models import estimate_cost


class CostTracker:
    """Tracks AI costs and records them to the company ledger."""

    def __init__(self, ledger=None):
        self.ledger = ledger
        self.session_total: float = 0.0

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        description: str,
        directive_id: str | None = None,
        run_id: str | None = None,
    ) -> float:
        """Record an LLM call cost. Returns the USD cost."""
        cost = estimate_cost(model, input_tokens, output_tokens)
        self.session_total += cost
        if self.ledger:
            self.ledger.record_ai_cost(
                amount_usd=cost,
                description=description,
                directive_id=directive_id,
                run_id=run_id if run_id is not None else current_run_id(),
            )
        return cost
