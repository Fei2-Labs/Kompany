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
        name="kompany_agent_work_summary",
        description=(
            "Per-agent task-history summary keyed by lowercase role: "
            "delivered/completed/failed/total counts + last_active."
        ),
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
        name="kompany_permission_gate",
        description=(
            "Adjudicate one harness-session permission prompt (PRD D5): "
            "files a harness_permission approval in the founder inbox and "
            "blocks until it is approved, rejected, or times out. Returns "
            "the Claude Code PermissionResult shape "
            "({'behavior': 'allow'|'deny', ...}); wired via "
            "--permission-prompt-tool, not meant for interactive use."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Tool the session wants to run"},
                "input": {"type": "object", "description": "Tool input from the permission prompt", "default": {}},
                "tool_use_id": {"type": "string", "description": "Claude's tool_use id (passthrough)"},
                "project_id": {"type": "string", "description": "Owning project (env-injected when omitted)"},
                "task_id": {"type": "string", "description": "Owning task (env-injected when omitted)"},
                "agent_role": {"type": "string", "description": "Requesting agent role (env-injected when omitted)"},
                "timeout_seconds": {"type": "number", "default": 120},
                "poll_interval_seconds": {"type": "number", "default": 2},
            },
            "required": ["tool_name"],
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
    # Self-update pipeline (06-12-self-update-pipeline PR2).
    Tool(
        name="kompany_self_update_propose",
        description=(
            "Governed self-update: run a harness session in the dedicated "
            "repo clone, enforce the T3 tier guard on the real diff, run "
            "tests, and file a self_update_proposal approval card. The "
            "merge stays human (founder approves, branch is pushed, PR "
            "opened best-effort)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "What to change and why (plain language)",
                },
            },
            "required": ["instruction"],
        },
    ),
    Tool(
        name="kompany_self_update_list",
        description="Recent self-update proposals, newest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    ),
    Tool(
        name="kompany_self_update_show",
        description="One self-update proposal by id.",
        inputSchema={
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
        },
    ),
    # ModelSource founder surface (06-11-harness-execution-leg PR5b).
    Tool(
        name="kompany_model_source_show",
        description=(
            "Show the active model source (kind, billing_mode, monthly fee) "
            "or null when none is configured (legacy per-token billing)."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_model_source_set",
        description=(
            "Set the active model source. kind: custom_api | "
            "claude_subscription | openai_subscription. Subscription kinds "
            "require monthly_fee_usd. Pass clear=true to remove the source "
            "(legacy per-token billing). The execution loop is derived by "
            "the engine — there is no loop/vehicle input."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "custom_api | claude_subscription | openai_subscription"},
                "billing_mode": {"type": "string", "description": "api | subscription (defaults from kind)"},
                "monthly_fee_usd": {"type": "number", "description": "Required for subscription billing"},
                "price_overrides": {"type": "object", "description": "model -> [input_usd, output_usd] per million tokens"},
                "clear": {"type": "boolean", "description": "Remove the source (legacy billing)", "default": False},
            },
        },
    ),
    Tool(
        name="kompany_detect_clis",
        description=(
            "Probe PATH for agent CLIs (claude / codex / opencode) that "
            "unlock zero-key model sources. Returns {cli: {found, path, "
            "version, source_kind}}."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    # Founder profile + rules (#6/#7).
    Tool(
        name="kompany_founder_profile_show",
        description=(
            "Show the founder profile (address, comms_style, language, "
            "working_hours, timezone, risk_tolerance) or null when unset."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_founder_profile_set",
        description=(
            "Merge-set the founder profile (partial fields merge over the "
            "stored profile). Pass clear=true to remove it."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "How to address the founder"},
                "pronouns": {"type": "string"},
                "comms_style": {"type": "string", "description": "Preferred tone, e.g. 'terse, direct'"},
                "language": {"type": "string", "description": "e.g. zh / en"},
                "working_hours": {"type": "string"},
                "timezone": {"type": "string"},
                "risk_tolerance": {"type": "string"},
                "clear": {"type": "boolean", "description": "Remove the profile", "default": False},
            },
        },
    ),
    Tool(
        name="kompany_founder_rules_show",
        description=(
            "Show the founder rules ({hard, soft}) or null when unset. "
            "Hard rules are enforced (proposal filter + execution gate); "
            "soft is best-effort prompt text."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_founder_rules_set",
        description=(
            "Merge-set the founder rules. hard: list of {kind, match, "
            "action} (kind: exclude_capability | budget_cap | "
            "forbid_paid_category); soft: free-text preferences. Pass "
            "clear=true to remove all rules."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "hard": {"type": "array", "items": {"type": "object"}, "description": "[{kind, match, action}]"},
                "soft": {"type": "string", "description": "Free-text preferences"},
                "clear": {"type": "boolean", "description": "Remove all founder rules", "default": False},
            },
        },
    ),
]


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
            result = mcp_proxy.proxy_tool_call(sidecar["port"], name, arguments)
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
