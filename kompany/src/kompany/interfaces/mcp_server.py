"""Kompany MCP Server — expose Kompany as MCP tools for Claude Code and similar."""

from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from kompany.core.engine import KompanyEngine

server = Server("kompany")
_engine: KompanyEngine | None = None


def get_engine() -> KompanyEngine:
    global _engine
    if _engine is None:
        _engine = KompanyEngine()
    return _engine


def _json_response(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


TOOLS = [
    Tool(
        name="kompany_init",
        description="Initialize a new Kompany with a name, product, starting balance, and stage.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Company name"},
                "product": {"type": "string", "description": "One-line product description"},
                "balance": {"type": "number", "description": "Starting balance in EUR", "default": 0.0},
                "stage": {"type": "string", "description": "Company stage", "default": "solo",
                          "enum": ["solo", "pre-seed", "seed", "series-a"]},
            },
            "required": ["name", "product"],
        },
    ),
    Tool(
        name="kompany_directive",
        description="Send a natural language directive to Kompany. The CEO will classify, route, and execute it.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Directive in natural language"},
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="kompany_status",
        description="Get current company status: balance, income, expenses, AI costs, active projects.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_projects",
        description="List all active projects.",
        inputSchema={"type": "object", "properties": {}},
    ),
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
]
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    engine = get_engine()

    if name == "kompany_init":
        engine.initialize_company(
            name=arguments["name"],
            product=arguments["product"],
            balance=arguments.get("balance", 0.0),
            stage=arguments.get("stage", "solo"),
        )
        return _json_response({
            "status": "initialized",
            "name": arguments["name"],
            "balance": arguments.get("balance", 0.0),
        })

    if name == "kompany_directive":
        result = engine.process_directive(arguments["text"])
        return _json_response({
            "status": result.status,
            "message": result.message,
            "project_id": result.project_id,
            "total_ai_cost": result.total_ai_cost,
            "agents_used": result.agents_used,
        })

    if name == "kompany_status":
        cfo = engine.registry.get("cfo")
        summary = cfo.get_summary()
        active = engine.projects.list_active()
        return _json_response({
            "company": engine.settings.company_name,
            "balance": summary["balance"],
            "total_income": summary["total_income"],
            "total_expenses": summary["total_expenses"],
            "total_ai_costs": summary["total_ai_costs"],
            "active_projects": len(active),
        })

    if name == "kompany_projects":
        active = engine.projects.list_active()
        return _json_response([
            {
                "id": p.id, "name": p.name,
                "type": p.type.value, "status": p.status.value,
                "target_amount": p.target_amount,
                "funded_amount": p.funded_amount,
            }
            for p in active
        ])

    if name == "kompany_project":
        p = engine.projects.get(arguments["project_id"])
        if not p:
            return _json_response({"error": f"Project '{arguments['project_id']}' not found"})
        tasks = engine.projects.list_tasks(p.id)
        return _json_response({
            "id": p.id, "name": p.name,
            "type": p.type.value, "status": p.status.value,
            "target_amount": p.target_amount,
            "funded_amount": p.funded_amount,
            "plan": p.plan, "assigned_agents": p.assigned_agents,
            "tasks": [
                {"id": t.id, "title": t.title, "agent": t.assigned_agent, "status": t.status.value}
                for t in tasks
            ],
        })

    if name == "kompany_ledger":
        entries = engine.ledger.get_recent(limit=arguments.get("limit", 10))
        return _json_response(entries)

    if name == "kompany_debate":
        from kompany.core.debate import DebateEngine
        stage = engine.settings.company_stage or "solo"
        de = DebateEngine(engine.registry, stage=stage)
        result = de.run(
            question=arguments["question"],
            company_state=engine.get_company_state(),
        )
        return _json_response({
            "question": result.question,
            "rounds": [[pos.model_dump() for pos in rnd] for rnd in result.rounds],
            "synthesis": result.synthesis.model_dump() if result.synthesis else None,
            "decision": result.decision.model_dump() if result.decision else None,
        })

    if name == "kompany_execute":
        result = engine.execute_project(arguments["project_id"])
        return _json_response(result)

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
