"""Kompany MCP Server — expose Kompany as MCP tools for Claude Code and similar."""

from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from kompany.core.engine import KompanyEngine
from kompany.core.harness_execution import permission_gate
from kompany.interfaces import mcp_proxy
from kompany.interfaces.mcp_dispatch import UnknownToolError, dispatch_tool
from kompany.interfaces.mcp_tools import (
    company_channel as _company_channel,
    governance_credentials as _governance_credentials,
    model_source_founder as _model_source_founder,
    observability_health as _observability_health,
    ops_status as _ops_status,
    projects_execution as _projects_execution,
    templates_glossary as _templates_glossary,
)

server = Server("kompany")
_engine: KompanyEngine | None = None


def get_engine() -> KompanyEngine:
    global _engine
    if _engine is None:
        _engine = KompanyEngine()
    return _engine


def _json_response(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


# TOOLS — concatenation of the domain modules in the ORIGINAL order.
# Tool ordering is part of the client-visible contract; do not reorder.
TOOLS: list[Tool] = (
    _company_channel.TOOLS
    + _ops_status.TOOLS
    + _projects_execution.TOOLS
    + _observability_health.TOOLS
    + _governance_credentials.TOOLS
    + _templates_glossary.TOOLS
    + _model_source_founder.TOOLS
)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    # Permission gate context (PRD D5): the claude session launches this
    # server with the task identity in env (see permission_gate's
    # ``build_permission_routing_args``); merge it into the arguments
    # BEFORE the proxy/dispatch split so the sidecar — a different
    # process without these env vars — receives the full context too.
    if name == permission_gate.GATE_TOOL_NAME:
        arguments = permission_gate.enrich_gate_arguments(arguments)
    # Proxy-first: when the desktop app's sidecar is alive, execute the
    # tool inside its engine so the app panel receives live SSE events
    # and every euro books in the one cost ledger. Discovery runs per
    # call (the app can start/stop mid-session); mcp_proxy caches the
    # verdict ~5s. The blocking HTTP call mirrors the previous blocking
    # synchronous engine work — the stdio server handles one call at a
    # time either way.
    sidecar = mcp_proxy.discover_sidecar()
    if sidecar is not None:
        try:
            result = mcp_proxy.proxy_tool_call(
                sidecar.get("port", 0),
                name,
                arguments,
                base_url=sidecar.get("url"),
            )
        except mcp_proxy.SidecarProxyError as exc:
            # Never fall back to in-process here: the sidecar may have
            # started real work before the call died (double-execution
            # risk). Surface the error instead.
            return [TextContent(type="text", text=f"Sidecar proxy error: {exc}")]
        return _json_response(result)

    # No sidecar — headless mode, lazy in-process engine (legacy path).
    try:
        return _json_response(dispatch_tool(get_engine(), name, arguments))
    except UnknownToolError:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


def main():
    """Run the Kompany MCP server over stdio."""
    import asyncio

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            init_options = server.create_initialization_options()
            await server.run(read_stream, write_stream, init_options)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
