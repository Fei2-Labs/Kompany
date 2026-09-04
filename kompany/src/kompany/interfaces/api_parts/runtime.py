"""Runtime control, heartbeat, tools, backups and credentials.

Split out of api.py per ADR-0003 (06-12-adr3-splits). Handler bodies are
verbatim moves onto a domain ``APIRouter``; route paths are unchanged.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import hmac  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401
from secrets import compare_digest  # noqa: F401
from typing import Any, AsyncIterator  # noqa: F401

from fastapi import (  # noqa: F401
    APIRouter,
    BackgroundTasks,
    Body,
    Form,
    Header,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse  # noqa: F401
from pydantic import BaseModel, ConfigDict, Field  # noqa: F401

from kompany.core.event_hub import get_event_hub  # noqa: F401
from kompany.interfaces.api_parts.deps import get_engine, reset_engine  # noqa: F401
from kompany.interfaces.api_parts.models import *  # noqa: F401,F403

router = APIRouter()


@router.get("/runtime")
def runtime_status() -> dict[str, Any]:
    """Return engine runtime state."""
    engine = get_engine()
    return engine.get_runtime_state()


@router.post("/heartbeat")
def heartbeat(req: HeartbeatRequest | None = None) -> dict[str, Any]:
    """Run one heartbeat check."""
    engine = get_engine()
    req = req or HeartbeatRequest()
    return engine.heartbeat_once(dispatch=req.dispatch, adapter=req.adapter)


@router.post("/notifications/dispatch")
def dispatch_notifications(req: DispatchNotificationsRequest) -> list[dict[str, Any]]:
    """Dispatch notification events."""
    engine = get_engine()
    return engine.dispatch_notifications(req.events, adapter=req.adapter)


class SuspendRequest(BaseModel):
    reason: str = "manual"


class ToolProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(..., min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    reason: str | None = None
    project_id: str | None = None
    task_id: str | None = None


class WorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None


@router.get("/workflows")
def list_workflows_catalog() -> list[dict[str, Any]]:
    """Workflow catalog (built-in + plugin) with step list + cost preview."""
    engine = get_engine()
    return engine.workflows_list()


@router.post("/workflows/{workflow_id}/run")
def run_workflow_endpoint(workflow_id: str, req: WorkflowRunRequest) -> dict[str, Any]:
    """Run a workflow now. Gated steps file inbox approval cards; the run
    itself never spends beyond the agents' own LLM calls (ledger-booked)."""
    from kompany.core.workflows_registry import WorkflowNotFound

    engine = get_engine()
    try:
        return engine.run_workflow(workflow_id, req.inputs, project_id=req.project_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tools")
def list_tools_registry() -> list[dict[str, Any]]:
    """Registered tools with side_effect / autonomy tier / connection state."""
    engine = get_engine()
    return engine.tools_list()


@router.post("/tools/propose")
def propose_tool_action(req: ToolProposeRequest) -> dict[str, Any]:
    """Propose a tool action (#4) — files a tool_action approval card.

    Nothing executes now; approving the card (POST /approvals/{id}/approve)
    runs the action for real. PAID actions can ONLY run through this path."""
    engine = get_engine()
    try:
        return engine.propose_action(
            req.tool_name,
            req.inputs,
            summary=req.summary or f"Run {req.tool_name}",
            reason=req.reason,
            project_id=req.project_id,
            task_id=req.task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tools/policies")
def list_tool_policies(agent_role: str | None = None) -> list[dict[str, Any]]:
    """List tool authorization policies."""
    engine = get_engine()
    return engine.list_tool_policies(agent_role=agent_role)


@router.post("/tools/policies")
def set_tool_policy(req: ToolPolicyRequest) -> dict[str, Any]:
    """Create or update a tool authorization policy."""
    engine = get_engine()
    return engine.set_tool_policy(
        req.agent_role,
        req.tool_name,
        req.allowed,
        reason=req.reason,
        requires_approval=req.requires_approval,
    )


@router.post("/tools/authorize")
def authorize_tool(req: ToolAuthorizationRequest) -> dict[str, Any]:
    """Check whether an agent may use a tool."""
    engine = get_engine()
    return engine.authorize_tool(req.agent_role, req.tool_name, purpose=req.purpose)


@router.post("/tools/use")
def use_tool(req: ToolAuthorizationRequest) -> dict[str, Any]:
    """Authorize a tool use through the engine gate."""
    engine = get_engine()
    return engine.use_tool(
        req.agent_role,
        req.tool_name,
        purpose=req.purpose,
        arguments=req.arguments,
        approval_id=req.approval_id,
    )


@router.post("/runtime/suspend")
def runtime_suspend(req: SuspendRequest) -> dict[str, Any]:
    """Suspend the engine."""
    engine = get_engine()
    return engine.suspend(reason=req.reason)


@router.post("/runtime/resume")
def runtime_resume() -> dict[str, Any]:
    """Resume the engine."""
    engine = get_engine()
    return engine.resume()


class DrainRequest(BaseModel):
    reason: str = "deployment"


@router.post("/runtime/drain")
def runtime_drain(req: DrainRequest) -> dict[str, Any]:
    """Begin a deployment drain (suspend + report initial drain status).

    Stage A deployment plan, Stage A step 6: rejects new work immediately
    (same suspend gate as ``/runtime/suspend``); poll ``GET /runtime/drain``
    until ``ready_for_restart`` is true before restarting the process."""
    engine = get_engine()
    return engine.drain(reason=req.reason)


@router.get("/runtime/drain")
def runtime_drain_status() -> dict[str, Any]:
    """Poll drain progress: persisted suspend state plus live in-flight
    counts for task attempts, channel handlers, harness children, and
    connector calls. ``ready_for_restart`` is true only when all are zero
    and the runtime is suspended."""
    engine = get_engine()
    return engine.drain_status()


class CreateBackupRequest(BaseModel):
    label: str = "manual"


@router.post("/backups")
def create_backup(req: CreateBackupRequest) -> dict[str, Any]:
    """Create a labeled SQLite snapshot."""
    engine = get_engine()
    return engine.create_backup(label=req.label)


@router.get("/backups")
def list_backups() -> list[dict[str, Any]]:
    """List SQLite snapshots, newest first."""
    engine = get_engine()
    return engine.list_backups()


@router.post("/backups/{backup_id}/restore")
def restore_backup(backup_id: str) -> dict[str, Any]:
    """Restore a SQLite snapshot."""
    engine = get_engine()
    try:
        return engine.restore_backup(backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/credentials")
def list_credentials() -> list[dict[str, Any]]:
    engine = get_engine()
    return engine.list_credentials()


@router.post("/credentials")
def set_credential(req: CredentialRequest) -> dict[str, Any]:
    engine = get_engine()
    return engine.set_credential(req.name, req.value)


@router.delete("/credentials/{name}")
def delete_credential(name: str) -> dict[str, Any]:
    engine = get_engine()
    return engine.delete_credential(name)


@router.post("/credentials/rotate-key")
def rotate_credential_key(req: CredentialKeyRotationRequest) -> dict[str, Any]:
    engine = get_engine()
    return engine.rotate_credential_key(req.new_vault_key)

