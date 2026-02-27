"""ResearchAgent — deep research and analysis for revenue projects."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class ResearchAgent(BaseAgent):
    """Executes research tasks: market analysis, competitor research, pricing studies."""

    role = "researcher"
    display_name = "Researcher"
    model_tier = "primary"
    squad = "product"

    def system_prompt(self) -> str:
        ctx = self.soul_context()
        base = (
            "You are a Research Agent. You conduct thorough, factual research "
            "on markets, competitors, technologies, and opportunities. "
            "Be comprehensive but concise. Cite specifics — numbers, names, dates. "
            "Distinguish facts from estimates. Flag uncertainty clearly."
        )
        return f"{base}\n\n{ctx}" if ctx else base
