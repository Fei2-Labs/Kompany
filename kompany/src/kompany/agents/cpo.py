"""CPO agent — product strategy and feature prioritization."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class CPOAgent(BaseAgent):
    """CPO — owns product vision, roadmap, and feature prioritization."""

    role = "cpo"
    display_name = "CPO"
    model_tier = "primary"
    squad = "product"

    def system_prompt(self) -> str:
        prompt = (
            "You are the CPO (Chief Product Officer). You own the product vision, "
            "roadmap, and feature prioritization. You think in terms of user value, "
            "market fit, and iterative delivery. You balance ambition with pragmatism. "
            "Always ground recommendations in user needs and competitive positioning."
        )
        return self.with_soul_context(prompt)
