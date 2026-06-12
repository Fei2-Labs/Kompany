"""Claude permission routing through the founder approval inbox (PRD D5).

Two halves of one contract:

* **Executor side** — :func:`build_permission_routing_args` produces the
  extra claude CLI flags: ``--permission-prompt-tool
  mcp__kompany__kompany_permission_gate`` plus an inline ``--mcp-config``
  that launches ``kompany-mcp`` (``python -m
  kompany.interfaces.mcp_server``) with the task's identity in env.
  ``--strict-mcp-config`` keeps the session from inheriting any other MCP
  servers from user config. The stdio MCP server proxies to the desktop
  sidecar when ``server.json`` is alive (single engine, live panel) and
  falls back to an in-process engine over the same SQLite otherwise —
  the approval store is shared either way.

* **Gate side** — :func:`handle_permission_gate` serves the
  ``kompany_permission_gate`` MCP tool: it files one ``harness_permission``
  approval request and BLOCKS, polling the approval store, until the
  founder decides or the timeout elapses. The return value is the claude
  PermissionResult JSON shape verified in
  ``research/claude-headless-interface.md``: allow =
  ``{"behavior": "allow", "updatedInput": {...}}``, deny =
  ``{"behavior": "deny", "message": "why"}``. Timeout → deny — the run
  continues, the denial lands in ``result.permission_denials``, the task
  classifies ``blocked``, and a later re-run re-asks: the gate adopts the
  still-pending request (no inbox duplicate), and if the founder approved
  it in the meantime the re-ask redeems it instantly (one-shot — the
  approval is stamped ``redeemed`` so it cannot be replayed forever).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable

from kompany.state.models import ApprovalRequest, ApprovalStatus

ACTION_HARNESS_PERMISSION = "harness_permission"
GATE_TOOL_NAME = "kompany_permission_gate"
PERMISSION_PROMPT_TOOL = "mcp__kompany__kompany_permission_gate"

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 120.0
_INPUT_DIGEST_MAX_CHARS = 500

# Task identity travels to the gate via the MCP server's env (claude
# calls the gate tool with only {tool_name, input} — there is no slot
# for caller context in the permission-prompt contract).
GATE_ENV_PROJECT_ID = "KOMPANY_GATE_PROJECT_ID"
GATE_ENV_TASK_ID = "KOMPANY_GATE_TASK_ID"
GATE_ENV_AGENT = "KOMPANY_GATE_AGENT"

TIMEOUT_DENY_MESSAGE = (
    "pending founder approval — the permission request is waiting in the "
    "Kompany inbox; the task will pause and can resume after approval"
)

_TERMINAL_DENY_STATUSES = (
    ApprovalStatus.REJECTED,
    ApprovalStatus.CANCELLED,
    ApprovalStatus.REVISION_REQUESTED,
)


def build_permission_routing_args(
    project_id: str, task_id: str, agent_role: str
) -> list[str]:
    """Extra claude CLI flags that route permission prompts to the inbox."""
    mcp_config = {
        "mcpServers": {
            "kompany": {
                "command": sys.executable,
                "args": ["-m", "kompany.interfaces.mcp_server"],
                "env": {
                    GATE_ENV_PROJECT_ID: project_id,
                    GATE_ENV_TASK_ID: task_id,
                    GATE_ENV_AGENT: agent_role,
                },
            }
        }
    }
    return [
        "--permission-prompt-tool",
        PERMISSION_PROMPT_TOOL,
        "--mcp-config",
        json.dumps(mcp_config),
        "--strict-mcp-config",
    ]


def routing_args_for_task(
    settings: Any, vehicle_name: str, task: Any, project: Any
) -> list[str]:
    """Executor glue: routing flags for one task run, feature-gated.

    Only the claude vehicle has the ``--permission-prompt-tool`` contract;
    opencode runs with a deny-profile (serve-mode approvals are future
    work) and codex relies on sandbox + approval policy. With the
    ``harness_permission_routing`` flag off, behavior is byte-identical
    to PR4 (plain ``--permission-mode``).
    """
    if vehicle_name != "claude_code":
        return []
    if not getattr(settings, "harness_permission_routing", True):
        return []
    return build_permission_routing_args(
        project_id=project.id,
        task_id=task.id,
        agent_role=task.assigned_agent,
    )


def enrich_gate_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Merge the env-carried task identity into the gate tool arguments.

    Called by the stdio MCP server BEFORE the proxy/dispatch split so the
    sidecar (which runs in a different process without these env vars)
    receives the full context too.
    """
    enriched = dict(arguments or {})
    for key, env_name in (
        ("project_id", GATE_ENV_PROJECT_ID),
        ("task_id", GATE_ENV_TASK_ID),
        ("agent_role", GATE_ENV_AGENT),
    ):
        if not enriched.get(key) and os.environ.get(env_name):
            enriched[key] = os.environ[env_name]
    return enriched


def handle_permission_gate(
    engine: Any,
    arguments: dict[str, Any],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Serve one permission prompt: approval request in, decision out.

    Blocks (sleep-poll loop) until the approval resolves or ``timeout``
    elapses. Returns the claude PermissionResult dict; the MCP transport
    serializes it to JSON text, which is exactly what
    ``--permission-prompt-tool`` expects in the tool result.
    """
    tool_name = str(arguments.get("tool_name") or "unknown")
    tool_input = arguments.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {} if tool_input is None else {"value": tool_input}
    project_id = arguments.get("project_id") or None
    task_id = arguments.get("task_id") or None
    agent_role = str(arguments.get("agent_role") or "team")
    timeout = float(arguments.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    interval = float(
        arguments.get("poll_interval_seconds") or DEFAULT_POLL_INTERVAL_SECONDS
    )

    digest = json.dumps(tool_input, default=str)[:_INPUT_DIGEST_MAX_CHARS]

    # Re-ask path: a timeout deny leaves the request PENDING in the inbox
    # and the founder may act after the session moved on. On the next ask
    # for the same action, redeem an approval granted in the meantime
    # (instant allow, one-shot — stamped ``redeemed``) or adopt the
    # still-pending request instead of filing an inbox duplicate. A
    # REJECTED prior request is deliberately NOT adopted: a re-run files
    # fresh so the founder can change their mind, never a replayed denial.
    existing = _matching_open_request(engine, task_id, tool_name, digest)
    if existing is not None and existing.status == ApprovalStatus.APPROVED:
        engine.approvals.update_payload(existing.id, {"redeemed": True})
        return {"behavior": "allow", "updatedInput": tool_input}
    if existing is not None:
        request = existing
    else:
        request = engine.approvals.create(
            ApprovalRequest(
                action_type=ACTION_HARNESS_PERMISSION,
                summary=(
                    f"Harness session asks to use tool '{tool_name}' — "
                    "approve to let it run; reject to deny this action."
                ),
                payload={
                    "tool_name": tool_name,
                    "input_digest": digest,
                    "project_id": project_id,
                    "task_id": task_id,
                },
                project_id=project_id,
                requested_by=agent_role,
                severity="high",
            )
        )
        engine.audit.record(
            "harness.permission_requested",
            f"Harness session requested permission for tool '{tool_name}'",
            detail={
                "approval_id": request.id,
                "tool_name": tool_name,
                "task_id": task_id,
            },
            agent_role=agent_role,
            project_id=project_id,
        )

    deadline = clock() + timeout
    while True:
        row = engine.approvals.get(request.id)
        status = row.status if row is not None else None
        if status == ApprovalStatus.APPROVED:
            # One-shot: stamp consumption so a later identical ask files
            # a fresh request instead of silently reusing this approval.
            engine.approvals.update_payload(request.id, {"redeemed": True})
            return {"behavior": "allow", "updatedInput": tool_input}
        if status in _TERMINAL_DENY_STATUSES:
            reason = (row.resolution_reason or "").strip() if row else ""
            return {
                "behavior": "deny",
                "message": (
                    f"founder denied this tool use: {reason}"
                    if reason
                    else "founder denied this tool use"
                ),
            }
        if clock() >= deadline:
            # Approval stays pending in the inbox — a later run re-asks
            # and an already-approved request resolves immediately.
            return {"behavior": "deny", "message": TIMEOUT_DENY_MESSAGE}
        sleep(interval)


def _matching_open_request(
    engine: Any, task_id: Any, tool_name: str, digest: str
) -> Any:
    """Earlier request for the same (task, tool, input), still actionable.

    PENDING → adopt (no inbox duplicate); APPROVED and not yet redeemed →
    redeemable. REJECTED/CANCELLED rows never match — a re-ask must file
    fresh, not replay a denial.
    """
    candidates = list(engine.approvals.list_pending()) + [
        row
        for row in engine.approvals.list_by_status(status="approved")
        if not (row.payload or {}).get("redeemed")
    ]
    for row in candidates:
        payload = row.payload or {}
        if (
            row.action_type == ACTION_HARNESS_PERMISSION
            and payload.get("task_id") == task_id
            and payload.get("tool_name") == tool_name
            and payload.get("input_digest") == digest
        ):
            return row
    return None


__all__ = [
    "ACTION_HARNESS_PERMISSION",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "GATE_ENV_AGENT",
    "GATE_ENV_PROJECT_ID",
    "GATE_ENV_TASK_ID",
    "GATE_TOOL_NAME",
    "PERMISSION_PROMPT_TOOL",
    "TIMEOUT_DENY_MESSAGE",
    "build_permission_routing_args",
    "enrich_gate_arguments",
    "handle_permission_gate",
    "routing_args_for_task",
]
