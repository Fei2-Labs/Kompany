"""Base agent classes for Kompany."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Type, TypeVar

import yaml
from pydantic import BaseModel

from kompany.llm.client import LLMClient, LLMResponse

T = TypeVar("T", bound=BaseModel)

_SOULS_DIR = Path(__file__).parent / "souls"


def load_soul(role: str) -> dict:
    """Load a soul.yaml file for the given role. Returns empty dict if not found."""
    path = _SOULS_DIR / f"{role}.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text()) or {}
    return {}


class BaseAgent(ABC):
    """Base class for all LLM-powered agents."""

    role: str = ""
    display_name: str = ""
    model_tier: str = "primary"
    squad: str = ""

    def __init__(self, llm: LLMClient, settings):
        self.llm = llm
        self.settings = settings
        self.cost_accumulated: float = 0.0
        self.soul: dict = load_soul(self.role)

    def soul_context(self) -> str:
        """Build personality context from soul.yaml for injection into prompts."""
        if not self.soul:
            return ""
        p = self.soul.get("personality", {})
        if not p:
            return ""
        parts = []
        if p.get("tone"):
            parts.append(f"Tone: {p['tone']}")
        if p.get("decision_style"):
            parts.append(f"Decision style: {p['decision_style']}")
        if p.get("risk_tolerance"):
            parts.append(f"Risk tolerance: {p['risk_tolerance']}")
        if p.get("priorities"):
            parts.append("Priorities: " + "; ".join(p["priorities"]))
        return "\n".join(parts)

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the agent's system prompt."""
        ...

    def call(
        self,
        prompt: str,
        directive_id: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Make a freeform LLM call."""
        model = self.settings.get_model_for_tier(self.model_tier)
        resp = self.llm.call(
            model=model,
            system=self.system_prompt(),
            prompt=prompt,
            agent_name=self.display_name,
            directive_id=directive_id,
            max_tokens=max_tokens,
        )
        self.cost_accumulated += resp.cost_usd
        return resp

    def call_structured(
        self,
        prompt: str,
        output_schema: Type[T],
        directive_id: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Make an LLM call with structured JSON output."""
        model = self.settings.get_model_for_tier(self.model_tier)
        resp = self.llm.call_structured(
            model=model,
            system=self.system_prompt(),
            prompt=prompt,
            output_schema=output_schema,
            agent_name=self.display_name,
            directive_id=directive_id,
            max_tokens=max_tokens,
        )
        self.cost_accumulated += resp.cost_usd
        return resp
