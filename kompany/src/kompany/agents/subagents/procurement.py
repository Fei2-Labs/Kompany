"""ProcurementAgent — sourcing, purchasing, and vendor management."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class ProcurementAgent(BaseAgent):
    """Handles procurement: sourcing, price comparison, vendor evaluation."""

    role = "procurement"
    display_name = "Procurement"
    model_tier = "economy"
    squad = "strategy"

    def system_prompt(self) -> str:
        ctx = self.soul_context()
        base = (
            "You are a Procurement Agent. You source products and services, "
            "compare prices, evaluate vendors, and negotiate terms. "
            "Always find at least 3 options. Present clear comparison tables. "
            "Optimize for value, not just lowest price."
        )
        return f"{base}\n\n{ctx}" if ctx else base
