"""Pluggable tool abstraction for the agentic loop (06-16 P1).

A ``Tool`` is the unit the NativeRunner dispatches. It is intentionally
lighter than the founder-facing ``plugins.contract.Tool`` ABC (which
carries pydantic input/output schemas, autonomy tiers, cost estimation
and the approval pipeline): in-loop tools are described as native
tool_use JSON schemas and classified only by ``SideEffect`` so the
runner can route READ-only calls inline and gate EXTERNAL_ACTION/SPEND
calls through the approval seam (wired in P2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from kompany.llm.client_parts._types import ToolSpec
from kompany.plugins.contract import SideEffect

__all__ = ["SideEffect", "ToolContext", "Tool", "FunctionTool"]


@dataclass
class ToolContext:
    """Ambient context handed to every tool ``run``.

    ``workspace`` scopes file/shell tools. ``settings`` exposes API keys
    for keyed tools (e.g. web_search). ``engine`` is the optional handle
    side-effect tools use in P2 to route through ``propose_action``;
    None in standalone/background use.
    """

    workspace: Path | None = None
    settings: Any = None
    engine: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    """Contract for an in-loop tool."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON-schema object describing the tool's arguments."""
        ...

    @property
    def side_effect(self) -> SideEffect:
        """READ → inline; WRITE_LOCAL/EXTERNAL_ACTION/SPEND → gated seam."""
        ...

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Execute and return an observation string (never raise)."""
        ...

    def to_spec(self) -> ToolSpec:
        """Render as a provider-neutral native tool schema."""
        ...


@dataclass
class FunctionTool:
    """Concrete ``Tool`` backed by a plain callable.

    The callable receives ``(args, ctx)`` and returns an observation
    string. Exceptions are caught by the registry, not here, so a tool
    body can stay terse.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    side_effect: SideEffect
    func: Callable[[dict[str, Any], ToolContext], str]

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return self.func(args, ctx)

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )
