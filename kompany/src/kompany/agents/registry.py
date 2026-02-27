"""Agent registry — lookup agents by role."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kompany.agents.base import BaseAgent
    from kompany.config.settings import KompanySettings
    from kompany.llm.client import LLMClient
    from kompany.state.ledger import Ledger


class AgentRegistry:
    """Creates and caches agent instances by role."""

    def __init__(
        self,
        llm: LLMClient,
        settings: KompanySettings,
        ledger: Ledger,
        projects=None,
    ):
        self._llm = llm
        self._settings = settings
        self._ledger = ledger
        self._projects = projects
        self._cache: dict[str, BaseAgent] = {}

    def get(self, role: str, company_state: dict | None = None):
        """Get or create an agent by role."""
        if role in self._cache:
            return self._cache[role]

        agent = self._create(role, company_state)
        self._cache[role] = agent
        return agent

    def list_by_squad(self, squad: str) -> list[str]:
        """Return role names belonging to a squad."""
        return [r for r, s in _SQUAD_MAP.items() if s == squad]

    def _create(self, role: str, company_state: dict | None = None):
        from kompany.agents.ceo import CEOAgent
        from kompany.agents.cfo import CFOAgent
        from kompany.agents.ciso import CISOAgent
        from kompany.agents.cmo import CMOAgent
        from kompany.agents.coo import COOAgent
        from kompany.agents.cpo import CPOAgent
        from kompany.agents.cro import CROAgent
        from kompany.agents.cos import CoSAgent
        from kompany.agents.csa import CSAAgent
        from kompany.agents.cto import CTOAgent
        from kompany.agents.cv import CVAgent
        from kompany.agents.subagents.analyst import AnalystAgent
        from kompany.agents.subagents.builder import BuilderAgent
        from kompany.agents.subagents.procurement import ProcurementAgent
        from kompany.agents.subagents.researcher import ResearchAgent
        from kompany.agents.subagents.writer import WriterAgent

        factories = {
            "ceo": lambda: CEOAgent(self._llm, self._settings, company_state),
            "cfo": lambda: CFOAgent(self._llm, self._settings, self._ledger),
            "cto": lambda: CTOAgent(self._llm, self._settings),
            "cpo": lambda: CPOAgent(self._llm, self._settings),
            "cmo": lambda: CMOAgent(self._llm, self._settings),
            "cro": lambda: CROAgent(self._llm, self._settings),
            "coo": lambda: COOAgent(self._llm, self._settings, self._projects),
            "csa": lambda: CSAAgent(self._llm, self._settings),
            "ciso": lambda: CISOAgent(self._llm, self._settings),
            "cos": lambda: CoSAgent(self._llm, self._settings),
            "cv": lambda: CVAgent(self._llm, self._settings),
            "researcher": lambda: ResearchAgent(self._llm, self._settings),
            "writer": lambda: WriterAgent(self._llm, self._settings),
            "analyst": lambda: AnalystAgent(self._llm, self._settings),
            "builder": lambda: BuilderAgent(self._llm, self._settings),
            "procurement": lambda: ProcurementAgent(self._llm, self._settings),
        }

        factory = factories.get(role)
        if not factory:
            raise ValueError(f"Unknown agent role: {role}")
        return factory()


# Squad membership map
_SQUAD_MAP: dict[str, str] = {
    "ceo": "strategy",
    "cfo": "strategy",
    "coo": "strategy",
    "cos": "strategy",
    "cto": "product",
    "cpo": "product",
    "csa": "product",
    "ciso": "product",
    "cmo": "growth",
    "cro": "growth",
    "cv": "growth",
}
