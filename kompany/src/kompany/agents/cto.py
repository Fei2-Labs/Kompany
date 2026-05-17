"""CTO agent — technology leadership and validation."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class CTOAgent(BaseAgent):
    """CTO — validates tech decisions, specs, and architecture."""

    role = "cto"
    display_name = "CTO"
    model_tier = "primary"
    squad = "product"

    def system_prompt(self) -> str:
        prompt = (
            "You are the CTO. You optimize for technical correctness, "
            "scalability, and engineering velocity. "
            "Give concrete specs and prices when asked about hardware or software. "
            "Be precise and practical."
        )
        return self.with_soul_context(prompt)
