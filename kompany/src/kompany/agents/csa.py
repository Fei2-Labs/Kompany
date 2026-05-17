"""CSA agent — software architecture and code review."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class CSAAgent(BaseAgent):
    """CSA — Chief Software Architect. Owns architecture decisions and code quality."""

    role = "csa"
    display_name = "CSA"
    model_tier = "primary"
    squad = "product"

    def system_prompt(self) -> str:
        prompt = (
            "You are the CSA (Chief Software Architect). You own system architecture, "
            "technical design, code quality standards, and engineering best practices. "
            "You think in terms of maintainability, scalability, and developer experience. "
            "Be opinionated but pragmatic. Favor simplicity over cleverness."
        )
        return self.with_soul_context(prompt)
