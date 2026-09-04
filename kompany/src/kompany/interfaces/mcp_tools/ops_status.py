"""Operational status tools (projects list, channels, anima, native tools, integrations, workspaces, agent summary).

Split out of ``mcp_server.py`` (ADR-0003). Entries are verbatim moves;
``mcp_server.TOOLS`` concatenates the domain lists back in the original
order — tool ordering is part of the client-visible contract.
"""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
    Tool(
        name="kompany_projects",
        description="List all active projects.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_channels_status",
        description=(
            "Channel adapter health (Telegram worker, email-in poller) "
            "and outbound-drafts outbox counts by status."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_channels_outbox",
        description=(
            "Recent channel outbox rows (outbound drafts), newest first. "
            "Drafts-only MVP: approved rows are posted manually."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return",
                    "default": 20,
                },
            },
        },
    ),
    Tool(
        name="kompany_anima_state",
        description=(
            "Anima persona state (valence, energy, derived tone, "
            "last_diary_date). Anima is the company's persona layer."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_anima_diary",
        description="Recent Anima diary entries, newest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return",
                    "default": 30,
                },
            },
        },
    ),
    Tool(
        name="kompany_tools_list",
        description=(
            "List registered native tools (action pipeline): name, "
            "side_effect, autonomy tier, paid flag, provider connection "
            "state. Side-effecting/paid tools run only via propose+approve."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_integrations",
        description=(
            "List registered integrations: id, display name, description, "
            "required credentials, connection state, tools provided. "
            "Connected = all required credentials present in the vault."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_workspaces",
        description=(
            "List workspaces (one isolated data dir per brand): active "
            "name, env_override flag, entries with data_dir + label. "
            "KOMPANY_DATA_DIR env bypasses the registry entirely."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_workspace_switch",
        description=(
            "Switch the active workspace (brand). Flips the registry; "
            "running engines must re-init to bind the new data dir — "
            "restart_required=true means this process is pinned by "
            "KOMPANY_DATA_DIR and needs a restart instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workspace name"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="kompany_tools_propose",
        description=(
            "Propose a tool action (e.g. email.send). Files a tool_action "
            "approval card — nothing executes until the founder approves. "
            "PAID actions can ONLY run through this path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Tool name, e.g. email.send"},
                "inputs": {"type": "object", "description": "Tool inputs"},
                "summary": {"type": "string", "description": "Card summary"},
                "reason": {"type": "string", "description": "Why this action"},
                "project_id": {"type": "string"},
            },
            "required": ["tool_name"],
        },
    ),
    Tool(
        name="kompany_workflows_list",
        description=(
            "List workflows (built-in + plugin): id, display name, source, "
            "steps with agent role / autonomy tier, LLM cost preview."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_workflow_run",
        description=(
            "Run a workflow by id with optional initial inputs. Steps that "
            "need the founder file inbox approval cards; nothing auto-spends "
            "beyond the agents' own ledger-booked LLM calls."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow id"},
                "inputs": {"type": "object", "description": "Initial inputs"},
                "project_id": {"type": "string"},
            },
            "required": ["workflow_id"],
        },
    ),
    Tool(
        name="kompany_agent_work_summary",
        description=(
            "Per-agent task-history summary keyed by lowercase role: "
            "delivered/completed/failed/total counts + last_active."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]
