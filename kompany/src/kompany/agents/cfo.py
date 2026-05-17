"""CFO agent — mechanical financial operations with LLM fallback."""

from __future__ import annotations

from kompany.agents.base import BaseAgent
from kompany.state.ledger import Ledger


class CFOAgent(BaseAgent):
    """CFO — deterministic budget math, LLM for complex reasoning."""

    role = "cfo"
    display_name = "CFO"
    model_tier = "primary"
    squad = "strategy"

    def __init__(self, llm, settings, ledger: Ledger):
        super().__init__(llm, settings)
        self.ledger = ledger

    def system_prompt(self) -> str:
        prompt = (
            "You are the CFO. You optimize for financial health, "
            "unit economics, and runway preservation. "
            "Be precise with numbers. No vague answers."
        )
        return self.with_soul_context(prompt)

    def check_budget(self, amount: float) -> dict:
        """Mechanical: check if budget is sufficient."""
        balance = self.ledger.get_balance()
        return {
            "requested": amount,
            "available": balance,
            "sufficient": balance >= amount,
            "shortfall": max(0, amount - balance),
        }

    def get_balance(self) -> float:
        """Mechanical: get current balance."""
        return self.ledger.get_balance()

    def get_summary(self) -> dict:
        """Mechanical: get financial summary."""
        totals = self.ledger.get_totals()
        return {
            "balance": self.ledger.get_balance(),
            "total_income": totals.get("income", 0),
            "total_expenses": totals.get("expense", 0),
            "total_ai_costs": totals.get("ai_cost", 0),
        }
