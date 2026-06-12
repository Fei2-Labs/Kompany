"""Company lifecycle + CEO-channel tools (init, directive, sessions, status, override, decision packet).

Split out of ``mcp_server.py`` (ADR-0003). Entries are verbatim moves;
``mcp_server.TOOLS`` concatenates the domain lists back in the original
order — tool ordering is part of the client-visible contract.
"""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
    Tool(
        name="kompany_init",
        description="Initialize a new Kompany with a name, starting capital, goal, time horizon, and exclusions.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Company name"},
                "capital": {"type": "number", "description": "Starting capital in EUR", "default": 0.0},
                "goal": {"type": "string", "description": "Primary goal"},
                "time_horizon": {"type": "string", "description": "Time horizon", "default": ""},
                "exclusions": {"type": "string", "description": "Excluded domains or methods", "default": ""},
            },
            "required": ["name", "capital", "goal"],
        },
    ),
    Tool(
        name="kompany_directive",
        description=(
            "Send a natural language directive to Kompany. The CEO classifies, "
            "routes, and executes it. The result's status may be 'clarify' (the "
            "CEO asks a follow-up question — re-call with the returned session_id "
            "to continue) or 'gated' (awaiting GO — call kompany_channel_go). "
            "Pass session_id to continue an open session."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Directive in natural language"},
                "session_id": {
                    "type": "string",
                    "description": "Continue an existing channel session (clarify reply / gated context). Omit to open a fresh session.",
                },
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="kompany_channel_sessions",
        description="List CEO-channel conversation sessions, newest first. Optional state filter (open/clarifying/gated/dispatched/answered/abandoned).",
        inputSchema={
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "Filter by session lifecycle state"},
                "limit": {"type": "number", "description": "Max sessions to return", "default": 50},
            },
        },
    ),
    Tool(
        name="kompany_channel_session",
        description="Fetch one CEO-channel session plus its ordered turns (the full conversation thread).",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session id to fetch"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="kompany_channel_go",
        description="Founder GO on a threshold-gated channel session — execute the held directive.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Gated session to execute"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="kompany_channel_abandon",
        description="Abandon a CEO-channel session — close it without executing.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session to abandon"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="kompany_status",
        description="Get current company status: balance, income, expenses, AI costs, active projects.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_override",
        description="Request an override with a risk briefing before execution.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Override request in natural language"},
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="kompany_decision_packet",
        description="Prepare a full decision-chain packet without executing it.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Directive to prepare"},
                "target_amount": {"type": "number", "description": "Optional target amount", "default": None},
            },
            "required": ["text"],
        },
    ),
]
