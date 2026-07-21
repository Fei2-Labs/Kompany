"""Agentic-chat tool registry (06-16 P2).

Builds the ``ToolRegistry`` the CEO chat loop runs with:

- the P1 read-only web tools (``web_fetch`` / ``web_search``) — run inline,
- the P4 Edge-CDP browser tools (navigate/read/find/screenshot inline;
  click/type gated) — degrade gracefully when Playwright/Edge are absent,
- the P4 external MCP client tools (``mcp__<server>__<tool>``) — read-only by
  config else gated; servers that are unreachable are skipped with a note,
- every founder-installed integration tool (``email.send`` etc.), wrapped as
  an in-loop :class:`Tool` classified by its ``side_effect`` so the registry
  routes side-effecting calls through the founder approval seam
  (``engine.propose_action`` → inbox ``tool_action``).

Workspace file/shell tools are intentionally LEFT OUT of the chat registry —
the chat loop has no project workspace; it researches and proposes, it does
not edit a checkout. File work still belongs to the background project runner.
"""

from __future__ import annotations

import json
from typing import Any

from kompany.core.agent_tools.base import SideEffect, ToolContext
from kompany.core.agent_tools.browser_tools import browser_tools
from kompany.core.agent_tools.mcp_client import load_mcp_tools
from kompany.core.agent_tools.memory_tools import memory_tools
from kompany.core.agent_tools.registry import ToolRegistry
from kompany.core.agent_tools.web_tools import web_tools
from kompany.llm.client_parts._types import ToolSpec

__all__ = ["build_chat_registry", "IntegrationToolAdapter"]


class IntegrationToolAdapter:
    """Wrap a founder-facing plugin ``Tool`` as an in-loop agent tool.

    The adapter exposes the plugin tool's native JSON schema to the model
    and classifies it by ``side_effect`` so the :class:`ToolRegistry` gate
    decides inline-vs-approval. Read-only plugin tools are executed inline
    through ``engine.execute_tool``; side-effecting ones are never run here
    — the registry routes them to ``engine.propose_action``.
    """

    def __init__(self, plugin_tool: Any) -> None:
        self._tool = plugin_tool

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return self._tool.description

    @property
    def parameters(self) -> dict[str, Any]:
        schema_cls = getattr(self._tool, "input_schema", None)
        if schema_cls is not None and hasattr(schema_cls, "model_json_schema"):
            schema = schema_cls.model_json_schema()
            # Strip pydantic ``$defs``/``title`` noise the providers ignore.
            schema.pop("title", None)
            return schema
        return {"type": "object", "properties": {}}

    @property
    def side_effect(self) -> SideEffect:
        # The plugin contract's SideEffect is the same enum re-exported by
        # agent_tools.base, so the registry's inline classification applies.
        return self._tool.side_effect

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Inline path for READ plugin tools (gated ones never reach here)."""
        engine = getattr(ctx, "engine", None)
        if engine is None or not hasattr(engine, "execute_tool"):
            return f"ERROR: {self.name} has no engine to execute against."
        result = engine.execute_tool(self.name, args)
        return json.dumps(result, ensure_ascii=False, default=str)

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )


def build_chat_registry(engine: Any) -> ToolRegistry:
    """Compose the CEO chat tool registry.

    Sources (P1 web + P4 browser/MCP/integrations): each source is added
    defensively — a missing optional backend (Playwright, mcp) or a broken
    integration degrades to fewer tools, never an exception that blocks chat.
    """
    reg = ToolRegistry(list(web_tools()))

    # P5 in-loop memory tools — recall (READ, inline), remember / save_skill
    # (WRITE_LOCAL, inline). Local-state only; never reach the approval seam.
    for tool in memory_tools():
        reg.register(tool)

    # P4 Edge-CDP browser tools. Registering is pure (no Edge connection
    # happens until a tool runs), so this never needs a backend present.
    for tool in browser_tools():
        reg.register(tool)

    # P4 external MCP client tools — namespaced ``mcp__<server>__<tool>``.
    try:
        settings = getattr(engine, "settings", None)
        mcp_tools, notes = load_mcp_tools(settings)
        for tool in mcp_tools:
            reg.register(tool)
        for note in notes:
            _health_note(engine, note)
    except Exception:  # noqa: BLE001 — MCP loading can't block chat
        pass

    # P3/P2 integration tools — every registered kompany.integrations tool,
    # side-effect-classified and gated through propose_action by the registry.
    try:
        from kompany.core import tool_actions

        for entry in tool_actions.tool_registry(engine).values():
            plugin_tool = entry.get("tool")
            if plugin_tool is None:
                continue
            reg.register(IntegrationToolAdapter(plugin_tool))
    except Exception:  # noqa: BLE001 — a broken integration can't block chat
        pass
    return reg


def _health_note(engine: Any, note: str) -> None:
    """Record an MCP health note via the engine audit log, best-effort."""
    audit = getattr(engine, "audit", None)
    if audit is None or not hasattr(audit, "record"):
        return
    try:
        audit.record("mcp.health", note, detail={}, agent_role="ceo")
    except Exception:  # noqa: BLE001 — note is best-effort
        pass
