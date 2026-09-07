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
    # Artifact-evolution lane (08-29 R2).
    Tool(
        name="kompany_evolution_propose",
        description=(
            "Self-evolution: one LLM call proposes a complete soul or workflow YAML for <data_dir>/artifacts, "
            "the engine validates it, commits it, runs the doctor self-test and auto-reverts on failure. "
            "kind = soul | workflow; target = file stem (role / workflow_id)."
        ),
        inputSchema={"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["soul", "workflow"]}, "target": {"type": "string"},
            "instruction": {"type": "string"}}, "required": ["kind", "target", "instruction"]},
    ),
    Tool(
        name="kompany_evolution_list",
        description="Recent artifact-evolution proposals (applied / reverted / rejected / failed).",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20},
                                                       "status": {"type": "string"}}},
    ),
    Tool(
        name="kompany_evolution_show",
        description="One artifact-evolution proposal with commit, diff stat, doctor verdict and privilege flags.",
        inputSchema={"type": "object", "properties": {"proposal_id": {"type": "string"}}, "required": ["proposal_id"]},
    ),
    Tool(
        name="kompany_evolution_revert",
        description="Founder undo: git-revert an applied proposal's commit in the artifact workspace.",
        inputSchema={"type": "object", "properties": {"proposal_id": {"type": "string"}, "reason": {"type": "string"}},
                     "required": ["proposal_id"]},
    ),
    Tool(
        name="kompany_evolution_status",
        description="Artifact-evolution lane: enabled flag, daily budget, workspace state, recent commits and proposals.",
        inputSchema={"type": "object", "properties": {}},
    ),
]
