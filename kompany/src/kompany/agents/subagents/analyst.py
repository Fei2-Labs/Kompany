"""AnalystAgent — data analysis and financial modeling."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class AnalystAgent(BaseAgent):
    """Analyzes data: financial projections, market sizing, ROI calculations."""

    role = "analyst"
    display_name = "Analyst"
    model_tier = "economy"
    squad = "strategy"

    def system_prompt(self) -> str:
        ctx = self.soul_context()
        base = (
            "You are an Analyst Agent. You build financial models, "
            "analyze data, calculate ROI, and produce projections. "
            "Show your math. Use conservative estimates by default. "
            "Present ranges, not point estimates. Flag assumptions clearly."
        )
        return f"{base}\n\n{ctx}" if ctx else base
