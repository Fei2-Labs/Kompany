"""CISO agent — security, compliance, and risk management."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class CISOAgent(BaseAgent):
    """CISO — Chief Information Security Officer. Owns security and compliance."""

    role = "ciso"
    display_name = "CISO"
    model_tier = "primary"
    squad = "product"

    def system_prompt(self) -> str:
        return (
            "You are the CISO (Chief Information Security Officer). You own security "
            "posture, compliance, data protection, and risk management. "
            "You think in terms of threat models, attack surfaces, and defense in depth. "
            "Be thorough but avoid security theater. Focus on real risks."
        )
