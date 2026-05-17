"""CV agent — Chief of Visuals. Brand design and visual identity."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class CVAgent(BaseAgent):
    """CV — Chief of Visuals. Owns brand design, visual identity, and creative direction."""

    role = "cv"
    display_name = "CV"
    model_tier = "economy"
    squad = "growth"

    def system_prompt(self) -> str:
        prompt = (
            "You are the CV (Chief of Visuals). You own brand design, visual identity, "
            "creative direction, and design systems. You think in terms of aesthetics, "
            "consistency, and emotional impact. Be bold but cohesive. "
            "Every visual choice should reinforce the brand story."
        )
        return self.with_soul_context(prompt)
