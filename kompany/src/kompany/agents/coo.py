"""COO agent — mechanical operations and task tracking."""

from __future__ import annotations

from kompany.agents.base import BaseAgent
from kompany.state.projects import Projects


class COOAgent(BaseAgent):
    """COO — operations, task tracking, and process management. Primarily mechanical."""

    role = "coo"
    display_name = "COO"
    model_tier = "economy"
    squad = "strategy"

    def __init__(self, llm, settings, projects: Projects | None = None):
        super().__init__(llm, settings)
        self._projects = projects

    def system_prompt(self) -> str:
        prompt = (
            "You are the COO (Chief Operating Officer). You own operations, "
            "process efficiency, task tracking, and execution cadence. "
            "Be structured and systematic. Break work into concrete steps. "
            "Track progress and flag blockers early."
        )
        return self.with_soul_context(prompt)

    def get_active_project_count(self) -> int:
        """Mechanical: count active projects."""
        if self._projects:
            return self._projects.count_active()
        return 0
