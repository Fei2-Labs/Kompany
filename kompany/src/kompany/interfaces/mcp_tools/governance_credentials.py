"""Governance + credential tools (delivery release, vault, tool policies, approvals, permission gate).

Split out of ``mcp_server.py`` (ADR-0003). Entries are verbatim moves;
``mcp_server.TOOLS`` concatenates the domain lists back in the original
order — tool ordering is part of the client-visible contract.
"""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
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
]
