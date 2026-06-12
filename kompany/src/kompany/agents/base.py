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

    def with_soul_context(self, prompt: str) -> str:
        """Append soul context to a system prompt when available."""
        ctx = self.soul_context()
        if not ctx:
            return prompt
        return f"{prompt}\n\nAgent soul:\n{ctx}"

    def founder_context(self) -> str:
        """Founder profile + soft-rules prompt block (#6/#7).

        Built from ``settings.founder_profile`` (address / comms style /
        language / …) and ``settings.founder_rules['soft']`` — ONE
        shared helper, pure string formatting, no LLM. Constitution
        invariant: comms style shapes PHRASING only; it must never
        soften or alter the substance of an honest assessment.
        """
        from kompany.core.founder_config import founder_context_block

        return founder_context_block(
            getattr(self.settings, "founder_profile", None),
            getattr(self.settings, "founder_rules", None),
        )

    def with_founder_context(self, prompt: str) -> str:
        """Append the founder context block to a system prompt."""
        ctx = self.founder_context()
        if not ctx:
            return prompt
        return f"{prompt}\n\n{ctx}"

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the agent's system prompt."""
        ...

    def call(
        self,
        prompt: str,
        directive_id: str | None = None,
        max_tokens: int = 4096,
        action_type: str | None = None,
    ) -> LLMResponse:
        """Make a freeform LLM call.

        ``action_type`` is the call-site label used in the SSE
        ``llm.spend`` payload (see ``05-19-cost-visibility-discipline``).
        """
        model = self.settings.get_model_for_tier(self.model_tier)
        resp = self.llm.call(
            model=model,
            system=self.with_founder_context(self.system_prompt()),
            prompt=prompt,
            agent_name=self.display_name,
            directive_id=directive_id,
            max_tokens=max_tokens,
            action_type=action_type,
        )
        self.cost_accumulated += resp.cost_usd
        return resp

    def call_structured(
        self,
        prompt: str,
        output_schema: Type[T],
        directive_id: str | None = None,
        max_tokens: int = 4096,
        action_type: str | None = None,
    ) -> LLMResponse:
        """Make an LLM call with structured JSON output.

        ``action_type`` is the call-site label used in the SSE
        ``llm.spend`` payload (see ``05-19-cost-visibility-discipline``).
        """
        model = self.settings.get_model_for_tier(self.model_tier)
        resp = self.llm.call_structured(
            model=model,
            system=self.with_founder_context(self.system_prompt()),
            prompt=prompt,
            output_schema=output_schema,
            agent_name=self.display_name,
            directive_id=directive_id,
            max_tokens=max_tokens,
            action_type=action_type,
        )
        self.cost_accumulated += resp.cost_usd
        return resp
