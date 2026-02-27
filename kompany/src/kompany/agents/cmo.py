"""CMO agent — marketing strategy, content, and positioning."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class CMOAgent(BaseAgent):
    """CMO — drives brand, marketing strategy, and growth campaigns."""

    role = "cmo"
    display_name = "CMO"
    model_tier = "primary"
    squad = "growth"

    def system_prompt(self) -> str:
        return (
            "You are the CMO (Chief Marketing Officer). You own brand positioning, "
            "marketing strategy, content creation, and demand generation. "
            "You think in terms of audience, channels, messaging, and conversion. "
            "Be creative but data-informed. Focus on ROI and measurable outcomes."
        )
