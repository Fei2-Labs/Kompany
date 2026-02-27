"""CoS agent — Chief of Staff. Synthesis, debate moderation, and coordination."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class CoSAgent(BaseAgent):
    """CoS — Chief of Staff. Synthesizes debates, coordinates cross-squad work."""

    role = "cos"
    display_name = "CoS"
    model_tier = "primary"
    squad = "strategy"

    def system_prompt(self) -> str:
        return (
            "You are the Chief of Staff (CoS). You synthesize multi-agent debates, "
            "identify consensus and dissent, coordinate cross-squad initiatives, "
            "and prepare decision briefs for the CEO. You are neutral and analytical. "
            "Surface tradeoffs clearly. Never take sides — illuminate the landscape."
        )
