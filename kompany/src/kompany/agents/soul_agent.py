"""YAML-driven agent runner for Pro / community ``AgentSoul`` plugins.

A Pro plugin declares an ``AgentSoul`` (see :mod:`kompany.plugins.contract`)
with a ``soul_yaml`` path. The engine loads that YAML and runs the role via
:class:`SoulAgent` — no extra Python class needed for the 80 % case.

Pro authors only write Python (by subclassing ``AgentSoul.run``) when YAML
cannot express the behavior (e.g. specialized debate scoring). The default
``AgentSoul.run`` is a stub that delegates to a ``SoulAgent`` instance built
by the engine at registration time.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from kompany.agents.base import BaseAgent

if TYPE_CHECKING:
    from kompany.config.settings import KompanySettings
    from kompany.llm.client import LLMClient
    from kompany.plugins.contract import AgentSoul


class SoulNotFound(FileNotFoundError):
    """Raised when a soul YAML path does not resolve."""


class SoulInvalid(ValueError):
    """Raised when a soul YAML is malformed or missing required fields."""


_REQUIRED_FIELDS = ("role", "display_name")
_RESERVED_CORE_ROLES = frozenset({
    "ceo", "cfo", "cto", "cpo", "cmo", "cro",
    "coo", "csa", "ciso", "cos", "cv",
    "analyst", "builder", "procurement", "researcher", "writer",
})


def _load_yaml(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise SoulNotFound(f"Soul YAML not found: {p}")
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise SoulInvalid(f"Soul YAML must be a mapping: {p}")
    for field in _REQUIRED_FIELDS:
        if not data.get(field):
            raise SoulInvalid(f"Soul YAML missing required field '{field}': {p}")
    if data["role"] in _RESERVED_CORE_ROLES:
        raise SoulInvalid(
            f"Soul role '{data['role']}' collides with a Core C-suite/subagent. "
            f"Pro souls must add NEW roles, never replace Core. See ADR-0001."
        )
    return data


def _build_system_prompt(soul: dict[str, Any]) -> str:
    """Compose a system prompt from soul YAML fields."""
    lines: list[str] = []

    role = soul["role"]
    display = soul["display_name"]
    tier = soul.get("tier", "c_level")
    squad = soul.get("squad", "")

    lines.append(f"You are the {display} ({role}) of this company.")
    if tier == "c_level" and squad:
        lines.append(f"You belong to the {squad} squad.")
    elif tier == "subagent":
        lines.append("You are a subagent executing focused tasks for a C-suite agent.")

    p = soul.get("personality") or {}
    if p:
        if p.get("tone"):
            lines.append(f"Tone: {p['tone']}.")
        if p.get("decision_style"):
            lines.append(f"Decision style: {p['decision_style']}.")
        if p.get("risk_tolerance"):
            lines.append(f"Risk tolerance: {p['risk_tolerance']}.")
        if p.get("communication"):
            lines.append(f"Communication: {p['communication']}.")
        if p.get("priorities"):
            lines.append("Priorities (in order):")
            for prio in p["priorities"]:
                lines.append(f"  - {prio}")

    traits = soul.get("traits") or []
    if traits:
        lines.append("Behavioral traits:")
        for t in traits:
            lines.append(f"  - {t}")

    debate = soul.get("debate_behavior") or {}
    if debate.get("participates") is False:
        lines.append(
            "You do NOT participate in cross-agent debate rounds; you receive "
            "synthesized briefs and decide."
        )
    elif debate.get("style"):
        lines.append(f"Debate style: {debate['style']}.")

    return "\n".join(lines)


class SoulAgent(BaseAgent):
    """Generic agent driven by a YAML soul.

    Construct from either an :class:`AgentSoul` plugin instance (the
    common path — engine builds these from entry-point discoveries) or
    directly from a YAML path (useful for tests and ad-hoc loading).

    Subclassing this is fine for niche needs, but the YAML-only path is
    the documented contract for Pro authors; see
    ``docs/context/plugin-contract.md``.
    """

    def __init__(
        self,
        soul_source: "AgentSoul | Path | str | dict[str, Any]",
        llm: LLMClient,
        settings: KompanySettings,
    ):
        if hasattr(soul_source, "soul_yaml") and soul_source.soul_yaml:  # AgentSoul
            soul_data = _load_yaml(soul_source.soul_yaml)
        elif isinstance(soul_source, (str, Path)):
            soul_data = _load_yaml(soul_source)
        elif isinstance(soul_source, dict):
            soul_data = dict(soul_source)
            for field in _REQUIRED_FIELDS:
                if not soul_data.get(field):
                    raise SoulInvalid(f"Soul dict missing required field '{field}'")
        else:
            raise TypeError(
                f"SoulAgent expects AgentSoul / path / dict, got {type(soul_source).__name__}"
            )

        self.role = soul_data["role"]
        self.display_name = soul_data["display_name"]
        self.model_tier = soul_data.get("model_tier", "primary")
        self.squad = soul_data.get("squad", "")
        self.tier = soul_data.get("tier", "c_level")
        self.soul = soul_data

        self.llm = llm
        self.settings = settings
        self.cost_accumulated: float = 0.0

        self._cached_system_prompt = _build_system_prompt(soul_data)

    def system_prompt(self) -> str:
        return self._cached_system_prompt

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        """Glob patterns from soul YAML restricting which Tools this role may call.

        Enforcement happens at WorkflowRunner / tool-invocation time, not here.
        SoulAgent only exposes the declared allowlist.
        """
        tools = self.soul.get("allowed_tools") or []
        return tuple(tools)
