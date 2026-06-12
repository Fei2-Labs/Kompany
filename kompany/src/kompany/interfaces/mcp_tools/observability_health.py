"""Observability, runtime, backup, health, episodes, distillation, and memory tools.

Split out of ``mcp_server.py`` (ADR-0003). Entries are verbatim moves;
``mcp_server.TOOLS`` concatenates the domain lists back in the original
order — tool ordering is part of the client-visible contract.
"""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
    Tool(
        name="kompany_observability",
        description="Return an operational observability/RPG snapshot.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_runtime_status",
        description="Return engine runtime state (running | suspended).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_heartbeat",
        description="Run one heartbeat check and return notification-ready events.",
        inputSchema={
            "type": "object",
            "properties": {
                "dispatch": {"type": "boolean", "default": False},
                "adapter": {"type": "string", "default": "dry-run"},
            },
        },
    ),
    Tool(
        name="kompany_notifications_dispatch",
        description="Dispatch notification events through a configured adapter.",
        inputSchema={
            "type": "object",
            "properties": {
                "events": {"type": "array", "items": {"type": "object"}},
                "adapter": {"type": "string", "default": "dry-run"},
            },
            "required": ["events"],
        },
    ),
    Tool(
        name="kompany_runtime_suspend",
        description="Suspend the engine. Subsequent directives short-circuit until resumed.",
        inputSchema={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "default": "manual"},
            },
        },
    ),
    Tool(
        name="kompany_runtime_resume",
        description="Resume the engine.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_backup_create",
        description="Create a labeled SQLite snapshot of the live database.",
        inputSchema={
            "type": "object",
            "properties": {
                "label": {"type": "string", "default": "manual"},
            },
        },
    ),
    Tool(
        name="kompany_backups",
        description="List SQLite snapshots, newest first.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_backup_restore",
        description="Restore a SQLite snapshot. Auto-creates a pre-restore backup.",
        inputSchema={
            "type": "object",
            "properties": {
                "backup_id": {"type": "string"},
            },
            "required": ["backup_id"],
        },
    ),
    Tool(
        name="kompany_retrospective",
        description="Run or replay a CoS retrospective for a project.",
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project id"},
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="kompany_health_list",
        description="List watchdog health events (silent_run, stranded, recovered, retry_exhausted).",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "open | resolved | snoozed | dismissed"},
                "kind": {"type": "string", "description": "silent_run | recovered | retry_exhausted | stranded_in_progress | stranded_todo"},
                "project_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
        },
    ),
    Tool(
        name="kompany_health_get",
        description="Fetch one watchdog health event by id.",
        inputSchema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
            },
            "required": ["event_id"],
        },
    ),
    Tool(
        name="kompany_health_resolve",
        description="Apply a player action to a health event (continue / snooze / dismiss).",
        inputSchema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "action": {"type": "string", "description": "continue | snooze | dismiss"},
                "snooze_minutes": {"type": "integer"},
                "resolved_by": {"type": "string", "default": "player"},
            },
            "required": ["event_id", "action"],
        },
    ),
    Tool(
        name="kompany_episodes_list",
        description="List materialized project-episode records (self-learning).",
        inputSchema={
            "type": "object",
            "properties": {
                "retention_tier": {
                    "type": "string",
                    "description": "Optional filter: 'full' or 'summary'.",
                },
            },
        },
    ),
    Tool(
        name="kompany_episodes_get",
        description="Fetch one project episode by project id (includes payload_json if full).",
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="kompany_episodes_rebuild",
        description="Re-materialize a project's episode payload from source tables.",
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="kompany_distill",
        description=(
            "Run CoS cross-episode distillation. Loads recent project "
            "episodes, asks CoS to extract durable patterns, and UPSERTs "
            "them into agent_memories as 'experiential' rows."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": "Lookback window like '30d' / '12h' / '45m'. Defaults to 30d.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true the LLM runs but no memories are written.",
                    "default": False,
                },
                "episode_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit project ids; bypasses 'since' and the 50-episode cap.",
                },
            },
        },
    ),
    Tool(
        name="kompany_memories",
        description="List memories for an agent role with optional stale and knowledge_type filters.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_role": {"type": "string", "description": "Agent role (e.g. coo, cfo, researcher)"},
                "limit": {"type": "integer", "default": 20},
                "include_stale": {"type": "boolean", "default": False},
                "knowledge_type": {"type": "string", "description": "experiential | factual"},
                "category": {"type": "string"},
            },
            "required": ["agent_role"],
        },
    ),
]
