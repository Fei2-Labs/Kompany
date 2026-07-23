"""Agent registry — lookup agents by role."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kompany.agents.base import BaseAgent
    from kompany.config.settings import KompanySettings
    from kompany.llm.client import LLMClient
    from kompany.state.ledger import Ledger


@dataclass(frozen=True, slots=True)
class AgentCapabilityDescriptor:
    """Stable, model-independent description used by the router."""

    role: str
    squad: str
    capabilities: tuple[str, ...]
    can_own_conversation: bool = True


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

    def descriptor(self, role: str) -> AgentCapabilityDescriptor:
        """Return the public routing descriptor for an agent role."""
        descriptor = _CAPABILITY_DESCRIPTORS.get(role)
        if descriptor is None:
            raise ValueError(f"Unknown agent role: {role}")
        return descriptor

    def descriptors(self) -> list[AgentCapabilityDescriptor]:
        """Return all public routing descriptors in stable role order."""
        return [
            _CAPABILITY_DESCRIPTORS[role]
            for role in sorted(_CAPABILITY_DESCRIPTORS)
        ]

    def candidates_for(self, capabilities: set[str]) -> list[str]:
        """Return conversation agents satisfying every required capability."""
        return [
            descriptor.role
            for descriptor in self.descriptors()
            if descriptor.can_own_conversation
            and capabilities.issubset(set(descriptor.capabilities))
        ]

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

_CAPABILITY_DESCRIPTORS: dict[str, AgentCapabilityDescriptor] = {
    "ceo": AgentCapabilityDescriptor(
        "ceo",
        "strategy",
        ("strategy", "coordination", "prioritization", "decisions"),
    ),
    "cfo": AgentCapabilityDescriptor(
        "cfo",
        "strategy",
        ("finance", "budget", "forecasting", "pricing"),
    ),
    "coo": AgentCapabilityDescriptor(
        "coo",
        "strategy",
        ("operations", "process", "delivery", "vendors"),
    ),
    "cos": AgentCapabilityDescriptor(
        "cos",
        "strategy",
        ("coordination", "planning", "reporting", "follow-up"),
    ),
    "cto": AgentCapabilityDescriptor(
        "cto",
        "product",
        ("engineering", "architecture", "infrastructure", "technical"),
    ),
    "cpo": AgentCapabilityDescriptor(
        "cpo",
        "product",
        ("product", "roadmap", "requirements", "user-research"),
    ),
    "csa": AgentCapabilityDescriptor(
        "csa",
        "product",
        ("solutions", "integrations", "customer-architecture"),
    ),
    "ciso": AgentCapabilityDescriptor(
        "ciso",
        "product",
        ("security", "privacy", "risk", "compliance"),
    ),
    "cmo": AgentCapabilityDescriptor(
        "cmo",
        "growth",
        ("marketing", "content", "campaigns", "brand"),
    ),
    "cro": AgentCapabilityDescriptor(
        "cro",
        "growth",
        ("sales", "revenue", "pipeline", "partnerships"),
    ),
    "cv": AgentCapabilityDescriptor(
        "cv",
        "growth",
        ("validation", "experiments", "market-research"),
    ),
    "researcher": AgentCapabilityDescriptor(
        "researcher",
        "support",
        ("research", "sources", "synthesis"),
        can_own_conversation=False,
    ),
    "writer": AgentCapabilityDescriptor(
        "writer",
        "support",
        ("writing", "editing", "content"),
        can_own_conversation=False,
    ),
    "analyst": AgentCapabilityDescriptor(
        "analyst",
        "support",
        ("analysis", "data", "metrics"),
        can_own_conversation=False,
    ),
    "builder": AgentCapabilityDescriptor(
        "builder",
        "support",
        ("implementation", "prototyping", "automation"),
        can_own_conversation=False,
    ),
    "procurement": AgentCapabilityDescriptor(
        "procurement",
        "support",
        ("procurement", "vendors", "purchasing"),
        can_own_conversation=False,
    ),
}
