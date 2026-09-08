"""MCP tool definitions — one-button update (Stage C step 9)."""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
    Tool(
        name="kompany_update_status",
        description="Installed vs latest GitHub release, release layout, and the phase of any running update.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_update_check",
        description="Query GitHub Releases (Core + Pro) and report whether an update is available. No spend.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_update_apply",
        description=(
            "Update the engine: verified wheels into a fresh venv under <data_dir>/releases, database backup, "
            "switch releases/current, restart; the first boot rolls back automatically if the doctor fails. "
            "Runs in the background — poll kompany_update_status."
        ),
        inputSchema={"type": "object", "properties": {"version": {"type": "string"}}},
    ),
    Tool(
        name="kompany_update_rollback",
        description="Point releases/current back at the previous release and restart.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_update_set_mode",
        description="manual (report only) or automatic_when_idle (the ticker applies updates when no agent is working).",
        inputSchema={"type": "object", "properties": {"mode": {"type": "string", "enum": ["manual", "automatic_when_idle"]}},
                     "required": ["mode"]},
    ),
]
