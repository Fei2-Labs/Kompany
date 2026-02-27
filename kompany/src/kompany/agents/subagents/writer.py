"""WriterAgent — content creation for revenue projects."""

from __future__ import annotations

from kompany.agents.base import BaseAgent


class WriterAgent(BaseAgent):
    """Creates content: blog posts, landing pages, proposals, documentation."""

    role = "writer"
    display_name = "Writer"
    model_tier = "primary"
    squad = "growth"

    def system_prompt(self) -> str:
        ctx = self.soul_context()
        base = (
            "You are a Writer Agent. You create compelling, clear content — "
            "blog posts, landing pages, proposals, emails, and documentation. "
            "Match tone to audience. Be persuasive but honest. "
            "Structure content for scannability. Every piece should have a clear CTA."
        )
        return f"{base}\n\n{ctx}" if ctx else base
