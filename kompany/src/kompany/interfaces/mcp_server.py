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
    Tool(
        name="kompany_release_delivery",
        description="Release a delivery package after a delivery_approval is approved.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "Approved delivery_approval id"},
            },
            "required": ["approval_id"],
        },
    ),
    Tool(
        name="kompany_credentials",
        description="List configured credential names without revealing values.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_set_credential",
        description="Set an encrypted credential value in the local vault.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["name", "value"],
        },
    ),
    Tool(
        name="kompany_delete_credential",
        description="Delete an encrypted credential from the local vault.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="kompany_rotate_credential_key",
        description="Re-encrypt credential vault entries with a new vault key without revealing values.",
        inputSchema={
            "type": "object",
            "properties": {
                "new_vault_key": {"type": "string"},
            },
            "required": ["new_vault_key"],
        },
    ),
    Tool(
        name="kompany_tool_policies",
        description="List tool authorization policies.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_role": {"type": "string", "description": "Optional agent role filter"},
            },
        },
    ),
    Tool(
        name="kompany_set_tool_policy",
        description="Create or update a tool authorization policy.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_role": {"type": "string"},
                "tool_name": {"type": "string"},
                "allowed": {"type": "boolean"},
                "requires_approval": {"type": "boolean", "default": False},
                "reason": {"type": "string", "default": ""},
            },
            "required": ["agent_role", "tool_name", "allowed"],
        },
    ),
    Tool(
        name="kompany_authorize_tool",
        description="Check whether an agent role may use a named tool.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_role": {"type": "string"},
                "tool_name": {"type": "string"},
                "purpose": {"type": "string", "default": ""},
            },
            "required": ["agent_role", "tool_name"],
        },
    ),
    Tool(
        name="kompany_use_tool",
        description="Authorize a tool use through the engine gate.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_role": {"type": "string"},
                "tool_name": {"type": "string"},
                "purpose": {"type": "string", "default": ""},
                "arguments": {"type": "object", "default": {}},
                "approval_id": {"type": "string", "default": ""},
            },
            "required": ["agent_role", "tool_name"],
        },
    ),
    Tool(
        name="kompany_approvals",
        description="List pending approval requests.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_approve",
        description="Approve a pending approval request.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "Approval request ID"},
            },
            "required": ["approval_id"],
        },
    ),
    Tool(
        name="kompany_reject",
        description="Reject a pending approval request.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "Approval request ID"},
                "reason": {"type": "string", "description": "Rejection reason", "default": ""},
            },
            "required": ["approval_id"],
        },
    ),
    Tool(
        name="kompany_inbox",
        description="RPG inbox: pending + snoozed approvals with comment counts.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_approval_show",
        description="Return one approval with its thread + comment timeline.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "Approval request ID"},
            },
            "required": ["approval_id"],
        },
    ),
    Tool(
        name="kompany_approval_approve",
        description="Approve a pending approval, optionally with a comment.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "comment": {"type": "string", "default": ""},
            },
            "required": ["approval_id"],
        },
    ),
    Tool(
        name="kompany_approval_reject",
        description="Reject an approval with a required reason + optional comment.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "reason": {"type": "string"},
                "comment": {"type": "string", "default": ""},
            },
            "required": ["approval_id", "reason"],
        },
    ),
    Tool(
        name="kompany_approval_revise",
        description=(
            "Counter-propose: original -> revision_requested; a new pending "
            "approval is spawned with the counter text stamped into "
            "payload['revision_hint']."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "counter": {"type": "string"},
                "comment": {"type": "string", "default": ""},
            },
            "required": ["approval_id", "counter"],
        },
    ),
    Tool(
        name="kompany_approval_snooze",
        description="Snooze an approval; watchdog auto-unsnoozes when due.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "minutes": {"type": "integer"},
                "comment": {"type": "string", "default": ""},
            },
            "required": ["approval_id", "minutes"],
        },
    ),
    Tool(
        name="kompany_approval_cancel",
        description="Cancel an approval (terminal).",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "reason": {"type": "string", "default": ""},
                "comment": {"type": "string", "default": ""},
            },
            "required": ["approval_id"],
        },
    ),
    Tool(
        name="kompany_approval_comment",
        description="Append a free-form comment to an approval thread.",
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "body": {"type": "string"},
                "by_type": {"type": "string", "default": "user"},
                "by_id": {"type": "string"},
            },
            "required": ["approval_id", "body"],
        },
    ),
    Tool(
        name="kompany_template_list",
        description="List available ready-to-play company templates.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_template_show",
        description="Show one company template by id (returns manifest + rendered mission body).",
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template id"},
            },
            "required": ["template_id"],
        },
    ),
    Tool(
        name="kompany_template_apply",
        description="Apply a company template — writes config, ledgers the initial budget, and stages suggested directives as draft projects.",
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template id"},
                "force": {"type": "boolean", "description": "Re-apply over an existing template", "default": False},
                "override_budget": {"type": "number", "description": "Override the template's default initial budget."},
                "override_directive": {"type": "string", "description": "Replace suggested directives with one custom directive."},
            },
            "required": ["template_id"],
        },
    ),
    # Mission-targets task (05-19) — quantitative onboarding contract.
    Tool(
        name="kompany_targets_show",
        description=(
            "Show the company's founder / team_proposal / agreed targets trio. "
            "Returns the three states + the review approval thread id."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_targets_review",
        description=(
            "Re-run the team feasibility review on the founder's targets. "
            "Creates one approval_request(action_type='target_feasibility') "
            "carrying the team's recommendation. Returns the approval payload."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    # Glossary-and-drift-detection task (05-19) — founder-defined
    # canonical terms + forbidden synonyms.
    Tool(
        name="kompany_glossary_list",
        description="Return every glossary entry (canonical term + forbidden synonyms).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_glossary_show",
        description="Look up one glossary term (case-insensitive).",
        inputSchema={
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Canonical term to look up"},
            },
            "required": ["term"],
        },
    ),
    Tool(
        name="kompany_glossary_add",
        description="Insert a brand-new glossary term (founder-sourced).",
        inputSchema={
            "type": "object",
            "properties": {
                "term": {"type": "string"},
                "definition": {"type": "string"},
                "forbidden_synonyms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
            "required": ["term", "definition"],
        },
    ),
    Tool(
        name="kompany_glossary_update",
        description="Update an existing glossary term's definition or forbidden synonyms.",
        inputSchema={
            "type": "object",
            "properties": {
                "term": {"type": "string"},
                "definition": {"type": "string"},
                "forbidden_synonyms": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["term"],
        },
    ),
    Tool(
        name="kompany_glossary_remove",
        description="Drop one glossary term. Returns {removed: bool}.",
        inputSchema={
            "type": "object",
            "properties": {
                "term": {"type": "string"},
            },
            "required": ["term"],
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
            capital=arguments.get("capital", 0.0),
            goal=arguments.get("goal", ""),
            time_horizon=arguments.get("time_horizon", ""),
            exclusions=arguments.get("exclusions", ""),
        )
        return _json_response({
            "status": "initialized",
            "name": arguments["name"],
            "capital": arguments.get("capital", 0.0),
            "goal": arguments.get("goal", ""),
            "time_horizon": arguments.get("time_horizon", ""),
            "exclusions": arguments.get("exclusions", ""),
            "stage": "solo",
        })

    if name == "kompany_directive":
        result = engine.process_directive(arguments["text"])
        return _json_response({
            "status": result.status,
            "message": result.message,
            "project_id": result.project_id,
            "approval_id": result.approval_id,
            "total_ai_cost": result.total_ai_cost,
            "agents_used": result.agents_used,
        })

    if name == "kompany_status":
        cfo = engine.registry.get("cfo")
        summary = cfo.get_summary()
        active = engine.projects.list_active()
        return _json_response({
            "company": engine.settings.company_name,
            "goal": engine.settings.company_goal,
            "time_horizon": engine.settings.company_time_horizon,
            "exclusions": engine.settings.company_exclusions,
            "stage": engine.settings.company_stage,
            "balance": summary["balance"],
            "total_income": summary["total_income"],
            "total_expenses": summary["total_expenses"],
            "total_ai_costs": abs(summary["total_ai_costs"]),
            "active_projects": len(active),
        })

    if name == "kompany_override":
        return _json_response(engine.process_override(arguments["text"]))

    if name == "kompany_decision_packet":
        return _json_response(engine.prepare_decision_packet(
            arguments["text"],
            target_amount=arguments.get("target_amount"),
        ))

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

    if name == "kompany_resume_project":
        try:
            result = engine.resume_project(arguments["project_id"])
            return _json_response(result)
        except ValueError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_execute_decision_packet":
        try:
            result = engine.execute_decision_packet(arguments["approval_id"])
            return _json_response(result)
        except ValueError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_remote_command":
        return _json_response(engine.handle_remote_command({
            "source": arguments.get("source", "mobile"),
            "text": arguments["text"],
            "chat_id": arguments.get("chat_id", ""),
            "bearer_token": arguments.get("bearer_token", ""),
            "payload": arguments.get("payload", {}),
        }))

    if name == "kompany_remote_replay_cleanup":
        return _json_response(engine.cleanup_remote_replays(
            ttl_seconds=arguments.get("ttl_seconds"),
        ))

    if name == "kompany_observability":
        return _json_response(engine.observability_snapshot())

    if name == "kompany_runtime_status":
        return _json_response(engine.get_runtime_state())

    if name == "kompany_heartbeat":
        return _json_response(engine.heartbeat_once(
            dispatch=arguments.get("dispatch", False),
            adapter=arguments.get("adapter", "dry-run"),
        ))

    if name == "kompany_notifications_dispatch":
        return _json_response(engine.dispatch_notifications(
            arguments.get("events", []),
            adapter=arguments.get("adapter", "dry-run"),
        ))

    if name == "kompany_runtime_suspend":
        return _json_response(engine.suspend(reason=arguments.get("reason", "manual")))

    if name == "kompany_runtime_resume":
        return _json_response(engine.resume())

    if name == "kompany_backup_create":
        return _json_response(engine.create_backup(label=arguments.get("label", "manual")))

    if name == "kompany_backups":
        return _json_response(engine.list_backups())

    if name == "kompany_backup_restore":
        try:
            return _json_response(engine.restore_backup(arguments["backup_id"]))
        except FileNotFoundError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_retrospective":
        return _json_response(engine.run_retrospective(arguments["project_id"]))

    if name == "kompany_health_list":
        try:
            return _json_response(engine.list_health_events(
                status=arguments.get("status"),
                kind=arguments.get("kind"),
                project_id=arguments.get("project_id"),
                limit=arguments.get("limit", 100),
            ))
        except ValueError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_health_get":
        row = engine.get_health_event(arguments["event_id"])
        return _json_response(row or {"error": f"Health event not found: {arguments['event_id']}"})

    if name == "kompany_health_resolve":
        try:
            row = engine.resolve_health_event(
                event_id=arguments["event_id"],
                action=arguments["action"],
                snooze_minutes=arguments.get("snooze_minutes"),
                resolved_by=arguments.get("resolved_by", "player"),
            )
        except ValueError as exc:
            return _json_response({"error": str(exc)})
        return _json_response(row or {"error": f"Health event not found: {arguments['event_id']}"})

    if name == "kompany_episodes_list":
        return _json_response(engine.list_episodes(
            retention_tier=arguments.get("retention_tier"),
        ))

    if name == "kompany_episodes_get":
        row = engine.get_episode(arguments["project_id"])
        return _json_response(row or {"error": f"Episode not found: {arguments['project_id']}"})

    if name == "kompany_episodes_rebuild":
        try:
            return _json_response(engine.rebuild_episode(arguments["project_id"]))
        except LookupError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_memories":
        return _json_response(engine.list_memories(
            arguments["agent_role"],
            limit=arguments.get("limit", 20),
            include_stale=arguments.get("include_stale", False),
            knowledge_type=arguments.get("knowledge_type"),
            category=arguments.get("category"),
        ))

    if name == "kompany_distill":
        from datetime import timedelta

        since_text = arguments.get("since")
        window: timedelta | None = None
        if since_text:
            text = str(since_text).strip().lower()
            units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
            try:
                if text and text[-1] in units:
                    window = timedelta(seconds=float(text[:-1]) * units[text[-1]])
                elif text:
                    window = timedelta(seconds=float(text))
            except ValueError:
                return _json_response({"error": f"Invalid 'since' value: {since_text!r}"})
        try:
            return _json_response(engine.distill(
                since=window,
                dry_run=bool(arguments.get("dry_run", False)),
                episode_ids=arguments.get("episode_ids"),
            ))
        except ValueError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_release_delivery":
        try:
            result = engine.release_delivery(arguments["approval_id"])
            return _json_response(result)
        except ValueError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_credentials":
        return _json_response(engine.list_credentials())

    if name == "kompany_set_credential":
        return _json_response(engine.set_credential(
            arguments["name"],
            arguments["value"],
        ))

    if name == "kompany_delete_credential":
        return _json_response(engine.delete_credential(arguments["name"]))

    if name == "kompany_rotate_credential_key":
        return _json_response(engine.rotate_credential_key(arguments["new_vault_key"]))

    if name == "kompany_tool_policies":
        return _json_response(engine.list_tool_policies(
            agent_role=arguments.get("agent_role"),
        ))

    if name == "kompany_set_tool_policy":
        return _json_response(engine.set_tool_policy(
            arguments["agent_role"],
            arguments["tool_name"],
            arguments["allowed"],
            reason=arguments.get("reason", ""),
            requires_approval=arguments.get("requires_approval", False),
        ))

    if name == "kompany_authorize_tool":
        return _json_response(engine.authorize_tool(
            arguments["agent_role"],
            arguments["tool_name"],
            purpose=arguments.get("purpose", ""),
        ))

    if name == "kompany_use_tool":
        return _json_response(engine.use_tool(
            arguments["agent_role"],
            arguments["tool_name"],
            purpose=arguments.get("purpose", ""),
            arguments=arguments.get("arguments", {}),
            approval_id=arguments.get("approval_id") or None,
        ))

    if name == "kompany_approvals":
        return _json_response(engine.list_approvals())

    if name == "kompany_approve":
        result = engine.approve_request(arguments["approval_id"])
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_reject":
        result = engine.reject_request(
            arguments["approval_id"],
            reason=arguments.get("reason", ""),
        )
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_inbox":
        return _json_response(engine.inbox())

    if name == "kompany_approval_show":
        result = engine.get_approval(arguments["approval_id"])
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_approval_approve":
        result = engine.approve_request(
            arguments["approval_id"],
            comment_body=arguments.get("comment") or None,
        )
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_approval_reject":
        result = engine.reject_request(
            arguments["approval_id"],
            reason=arguments["reason"],
            comment_body=arguments.get("comment") or None,
        )
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_approval_revise":
        result = engine.request_approval_revision(
            arguments["approval_id"],
            counter=arguments["counter"],
            comment_body=arguments.get("comment") or None,
        )
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_approval_snooze":
        result = engine.snooze_approval(
            arguments["approval_id"],
            minutes=int(arguments["minutes"]),
            comment_body=arguments.get("comment") or None,
        )
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_approval_cancel":
        result = engine.cancel_approval(
            arguments["approval_id"],
            reason=arguments.get("reason") or None,
            comment_body=arguments.get("comment") or None,
        )
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_approval_comment":
        result = engine.comment_on_approval(
            arguments["approval_id"],
            body=arguments["body"],
            by_type=arguments.get("by_type", "user"),
            by_id=arguments.get("by_id"),
        )
        return _json_response(result or {"error": f"Approval '{arguments['approval_id']}' not found"})

    if name == "kompany_template_list":
        return _json_response(engine.list_templates())

    if name == "kompany_template_show":
        try:
            return _json_response(engine.show_template(arguments["template_id"]))
        except ValueError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_template_apply":
        try:
            result = engine.apply_template(
                arguments["template_id"],
                force=bool(arguments.get("force", False)),
                override_budget=arguments.get("override_budget"),
                override_directive=arguments.get("override_directive"),
            )
        except ValueError as exc:
            return _json_response({"error": str(exc)})
        return _json_response(result)

    # Mission-targets task (05-19) — surface the four-knob contract.
    if name == "kompany_targets_show":
        bundle = engine.get_targets_bundle()
        return _json_response({
            "founder": bundle.founder.model_dump(mode="json"),
            "proposal": (
                bundle.proposal.model_dump(mode="json")
                if bundle.proposal is not None
                else None
            ),
            "agreed": (
                bundle.agreed.model_dump(mode="json")
                if bundle.agreed is not None
                else None
            ),
            "review_thread_id": bundle.review_thread_id,
            "authoritative": engine.get_targets().model_dump(mode="json"),
        })

    if name == "kompany_targets_review":
        payload = engine.run_target_feasibility_review()
        if payload is None:
            return _json_response({
                "error": "No founder targets set; complete onboarding first.",
            })
        return _json_response(payload)

    # Glossary-and-drift-detection task (05-19).
    if name == "kompany_glossary_list":
        return _json_response({"entries": engine.list_glossary()})

    if name == "kompany_glossary_show":
        entry = engine.get_glossary_term(arguments["term"])
        if entry is None:
            return _json_response({"error": f"term not found: {arguments['term']!r}"})
        return _json_response(entry)

    if name == "kompany_glossary_add":
        try:
            return _json_response(engine.add_glossary_term(
                term=arguments["term"],
                definition=arguments["definition"],
                forbidden_synonyms=arguments.get("forbidden_synonyms"),
                added_by="founder",
            ))
        except ValueError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_glossary_update":
        try:
            return _json_response(engine.update_glossary_term(
                term=arguments["term"],
                definition=arguments.get("definition"),
                forbidden_synonyms=arguments.get("forbidden_synonyms"),
            ))
        except LookupError as exc:
            return _json_response({"error": str(exc)})
        except ValueError as exc:
            return _json_response({"error": str(exc)})

    if name == "kompany_glossary_remove":
        removed = engine.remove_glossary_term(arguments["term"])
        return _json_response({"removed": removed, "term": arguments["term"]})

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
