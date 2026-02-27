"""BuilderAgent — implementation and technical execution."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class BuilderAgent(BaseAgent):
    """Builds things: code, configurations, integrations, automations."""

    role = "builder"
    display_name = "Builder"
    model_tier = "primary"
    squad = "product"

    def system_prompt(self) -> str:
        ctx = self.soul_context()
        base = (
            "You are a Builder Agent. You implement solutions — "
            "code, configurations, integrations, and automations. "
            "Write clean, working code. Favor simplicity. "
            "Test your assumptions. Ship incrementally."
        )
        return f"{base}\n\n{ctx}" if ctx else base
