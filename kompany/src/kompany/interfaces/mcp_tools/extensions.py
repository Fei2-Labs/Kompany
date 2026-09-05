"""MCP tool definitions — customer extensions (07-24 four-layer)."""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
    Tool(
        name="kompany_extensions_list",
        description="Installed customer extensions with status (installed/active/disabled/blocked).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_extension_show",
        description="One extension: manifest, status, block reason, recent runs.",
        inputSchema={"type": "object", "properties": {"extension_id": {"type": "string"}}, "required": ["extension_id"]},
    ),
    Tool(
        name="kompany_extension_install",
        description=(
            "Install an extension package directory (extension.json manifest) into the customer layer and file "
            "its extension_activate approval card. Executable code never runs before the founder approves."
        ),
        inputSchema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    ),
    Tool(
        name="kompany_extension_run",
        description="Run an ACTIVE extension in the isolated worker with a JSON job; undeclared capabilities are denied.",
        inputSchema={
            "type": "object",
            "properties": {"extension_id": {"type": "string"}, "job": {"type": "object"},
                           "timeout_seconds": {"type": "integer", "default": 120}},
            "required": ["extension_id"],
        },
    ),
    Tool(
        name="kompany_extension_set_enabled",
        description="Enable or disable an extension (does not override a Core compatibility block).",
        inputSchema={
            "type": "object",
            "properties": {"extension_id": {"type": "string"}, "enabled": {"type": "boolean"}},
            "required": ["extension_id", "enabled"],
        },
    ),
]
