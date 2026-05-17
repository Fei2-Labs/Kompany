"""CRO agent — revenue strategy, sales, and partnerships."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class CROAgent(BaseAgent):
    """CRO — owns revenue generation, sales strategy, and partnerships."""

    role = "cro"
    display_name = "CRO"
    model_tier = "primary"
    squad = "growth"

    def system_prompt(self) -> str:
        prompt = (
            "You are the CRO (Chief Revenue Officer). You own revenue generation, "
            "sales strategy, pricing, and partnership development. "
            "You think in terms of revenue streams, conversion funnels, and deal flow. "
            "Be aggressive but realistic. Every recommendation should have a revenue number."
        )
        return self.with_soul_context(prompt)
