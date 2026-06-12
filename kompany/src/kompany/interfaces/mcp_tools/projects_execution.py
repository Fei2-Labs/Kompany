"""Project + execution tools (project detail, ledger, debate, execute, abandon, resume, decision packet, remote).

Split out of ``mcp_server.py`` (ADR-0003). Entries are verbatim moves;
``mcp_server.TOOLS`` concatenates the domain lists back in the original
order — tool ordering is part of the client-visible contract.
"""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
    Tool(
        name="kompany_project",
        description="Get details for a specific project including tasks and revenue paths.",
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID"},
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="kompany_ledger",
        description="Get recent ledger entries showing all financial transactions.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of entries to return", "default": 10},
            },
        },
    ),
    Tool(
        name="kompany_debate",
        description="Run a full multi-agent debate on a strategic question.",
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Strategic question to debate"},
            },
            "required": ["question"],
        },
    ),
    Tool(
        name="kompany_execute",
        description="Execute a revenue project's tasks autonomously using subagents.",
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID to execute"},
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="kompany_project_abandon",
        description=(
            "Abandon a plan (#10): cancel the project, stop its unfinished "
            "tasks, withdraw its open inbox cards, release the unspent envelope."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID to abandon"},
                "reason": {"type": "string", "description": "Why the plan is abandoned"},
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="kompany_resume_project",
        description="Resume a project from persisted task/checkpoint state.",
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID to resume"},
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="kompany_execute_decision_packet",
        description="Execute an approved decision-chain packet under governance.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "Approved decision-chain approval id"},
            },
            "required": ["approval_id"],
        },
    ),
    Tool(
        name="kompany_remote_command",
        description="Handle an authenticated inbound remote command.",
        inputSchema={
            "type": "object",
            "properties": {
                "source": {"type": "string", "default": "mobile"},
                "text": {"type": "string"},
                "chat_id": {"type": "string", "default": ""},
                "bearer_token": {"type": "string", "default": ""},
                "payload": {"type": "object", "default": {}},
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="kompany_remote_replay_cleanup",
        description="Delete expired remote replay records.",
        inputSchema={
            "type": "object",
            "properties": {
                "ttl_seconds": {"type": "integer", "default": None},
            },
        },
    ),
]
