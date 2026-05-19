"""Kompany REST API — FastAPI interface to the engine."""

from __future__ import annotations

import hmac
import time
from secrets import compare_digest
from typing import Any

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from kompany.core.engine import KompanyEngine
from kompany.interfaces.web import render_dashboard
from kompany.remote import request_from_telegram_update

app = FastAPI(
    title="Kompany API",
    description="Autonomous business operating system for solo founders.",
    version="0.1.0",
)

_engine: KompanyEngine | None = None


def get_engine() -> KompanyEngine:
    global _engine
    if _engine is None:
        _engine = KompanyEngine()
    return _engine


class DirectiveRequest(BaseModel):
    text: str


class OverrideRequest(BaseModel):
    text: str


class DecisionPacketRequest(BaseModel):
    text: str
    target_amount: float | None = None


class InitRequest(BaseModel):
    name: str
    capital: float = 0.0
    goal: str = ""
    time_horizon: str = ""
    exclusions: str = ""


class DebateRequest(BaseModel):
    question: str


class RejectApprovalRequest(BaseModel):
    reason: str = ""
    comment: str = ""


class ApproveApprovalRequest(BaseModel):
    comment: str = ""


class ReviseApprovalRequest(BaseModel):
    counter: str
    comment: str = ""


class SnoozeApprovalRequest(BaseModel):
    minutes: int
    comment: str = ""


class CancelApprovalRequest(BaseModel):
    reason: str = ""
    comment: str = ""


class CommentApprovalRequest(BaseModel):
    body: str
    by_type: str = "user"
    by_id: str | None = None


class HeartbeatRequest(BaseModel):
    dispatch: bool = False
    adapter: str = "dry-run"


class DispatchNotificationsRequest(BaseModel):
    events: list[dict[str, Any]]
    adapter: str = "dry-run"


class ToolPolicyRequest(BaseModel):
    agent_role: str
    tool_name: str
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


class ToolAuthorizationRequest(BaseModel):
    agent_role: str
    tool_name: str
    purpose: str = ""
    arguments: dict[str, Any] = {}
    approval_id: str | None = None


class RemoteCommandAPIRequest(BaseModel):
    source: str = "mobile"
    text: str
    chat_id: str = ""
    bearer_token: str = ""
    payload: dict[str, Any] = {}


class RemoteReplayCleanupRequest(BaseModel):
    ttl_seconds: int | None = None


class DashboardActionRequest(BaseModel):
    action: str
    approval_id: str | None = None
    reason: str = ""


class CredentialRequest(BaseModel):
    name: str
    value: str


class CredentialKeyRotationRequest(BaseModel):
    new_vault_key: str


@app.post("/init")
def init_company(req: InitRequest) -> dict[str, Any]:
    """Initialize a new Kompany."""
    engine = get_engine()
    engine.initialize_company(
        name=req.name,
        capital=req.capital,
        goal=req.goal,
        time_horizon=req.time_horizon,
        exclusions=req.exclusions,
    )
    return {
        "status": "initialized",
        "name": req.name,
        "capital": req.capital,
        "goal": req.goal,
        "time_horizon": req.time_horizon,
        "exclusions": req.exclusions,
        "stage": "solo",
    }


@app.post("/directive")
def send_directive(req: DirectiveRequest) -> dict[str, Any]:
    """Send a directive to Kompany."""
    engine = get_engine()
    result = engine.process_directive(req.text)
    return {
        "status": result.status,
        "message": result.message,
        "project_id": result.project_id,
        "approval_id": result.approval_id,
        "total_ai_cost": result.total_ai_cost,
        "agents_used": result.agents_used,
    }


@app.post("/override")
def request_override(req: OverrideRequest) -> dict[str, Any]:
    """Request an override with a risk briefing."""
    engine = get_engine()
    return engine.process_override(req.text)


@app.post("/decision-packet")
def prepare_decision_packet(req: DecisionPacketRequest) -> dict[str, Any]:
    """Prepare a full decision-chain packet without executing it."""
    engine = get_engine()
    return engine.prepare_decision_packet(req.text, target_amount=req.target_amount)


@app.post("/projects/{project_id}/resume")
def resume_project(project_id: str) -> dict[str, Any]:
    """Resume a project from persisted task/checkpoint state."""
    engine = get_engine()
    try:
        return engine.resume_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ExecutePacketRequest(BaseModel):
    approval_id: str


@app.post("/decision-packet/execute")
def execute_decision_packet(req: ExecutePacketRequest) -> dict[str, Any]:
    """Execute an approved decision-chain packet under governance."""
    engine = get_engine()
    try:
        return engine.execute_decision_packet(req.approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ReleaseDeliveryRequest(BaseModel):
    approval_id: str


@app.post("/delivery/release")
def release_delivery(req: ReleaseDeliveryRequest) -> dict[str, Any]:
    """Release a delivery package after delivery_approval is approved."""
    engine = get_engine()
    try:
        return engine.release_delivery(req.approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RetrospectiveRequest(BaseModel):
    project_id: str


@app.post("/retrospective")
def run_retrospective(req: RetrospectiveRequest) -> dict[str, Any]:
    """Run or replay a CoS retrospective for a project."""
    engine = get_engine()
    return engine.run_retrospective(req.project_id)


class HealthResolveRequest(BaseModel):
    action: str
    snooze_minutes: int | None = None
    resolved_by: str = "player"


@app.get("/health/events")
def list_health_events(
    status: str | None = None,
    kind: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List watchdog health events, newest-first."""
    engine = get_engine()
    try:
        return engine.list_health_events(
            status=status,
            kind=kind,
            project_id=project_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/health/events/{event_id}")
def get_health_event(event_id: str) -> dict[str, Any]:
    """Fetch one health event."""
    engine = get_engine()
    row = engine.get_health_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Health event not found: {event_id}")
    return row


@app.post("/health/events/{event_id}/resolve")
def resolve_health_event(
    event_id: str,
    req: HealthResolveRequest,
) -> dict[str, Any]:
    """Apply a player action (``continue`` / ``snooze`` / ``dismiss``)."""
    engine = get_engine()
    try:
        row = engine.resolve_health_event(
            event_id=event_id,
            action=req.action,
            snooze_minutes=req.snooze_minutes,
            resolved_by=req.resolved_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"Health event not found: {event_id}")
    return row


class TemplateApplyRequest(BaseModel):
    force: bool = False
    override_budget: float | None = None
    override_directive: str | None = None


@app.get("/templates")
def list_templates() -> list[dict[str, Any]]:
    """List available company templates."""
    return get_engine().list_templates()


@app.get("/templates/{template_id}")
def get_template(template_id: str) -> dict[str, Any]:
    """Fetch one template by id (includes rendered mission body)."""
    try:
        return get_engine().show_template(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/templates/{template_id}/apply")
def apply_template(
    template_id: str,
    req: TemplateApplyRequest | None = None,
) -> dict[str, Any]:
    """Apply a template — writes company config, ledgers the initial
    budget, and stages suggested directives as draft projects."""
    body = req or TemplateApplyRequest()
    try:
        return get_engine().apply_template(
            template_id,
            force=body.force,
            override_budget=body.override_budget,
            override_directive=body.override_directive,
        )
    except ValueError as exc:
        # Distinguish "not found" from "already applied" via message
        message = str(exc)
        status = 404 if "not found" in message else 409
        raise HTTPException(status_code=status, detail=message) from exc


@app.get("/episodes")
def list_episodes(retention: str | None = None) -> list[dict[str, Any]]:
    """List materialized project episodes."""
    engine = get_engine()
    return engine.list_episodes(retention_tier=retention)


@app.get("/episodes/{project_id}")
def get_episode(project_id: str) -> dict[str, Any]:
    """Fetch one episode by project id."""
    engine = get_engine()
    row = engine.get_episode(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {project_id}")
    return row


@app.post("/episodes/{project_id}/rebuild")
def rebuild_episode(project_id: str) -> dict[str, Any]:
    """Force re-materialization of a project's episode."""
    engine = get_engine()
    try:
        return engine.rebuild_episode(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class DistillationRequest(BaseModel):
    since: str | None = None
    dry_run: bool = False
    episode_ids: list[str] | None = None


@app.post("/distillation/run")
def run_distillation(req: DistillationRequest | None = None) -> dict[str, Any]:
    """Run CoS cross-episode distillation.

    Body fields (all optional):

    * ``since`` — human window like ``"30d"`` / ``"12h"``; defaults to 30d.
    * ``dry_run`` — when true the LLM runs but no memories are written.
    * ``episode_ids`` — explicit subset that bypasses both ``since`` and
      the 50-episode cap.
    """
    from datetime import timedelta

    body = req or DistillationRequest()
    window: timedelta | None = None
    if body.since is not None:
        text = body.since.strip().lower()
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        try:
            if text and text[-1] in units:
                window = timedelta(seconds=float(text[:-1]) * units[text[-1]])
            elif text:
                window = timedelta(seconds=float(text))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid 'since' value: {body.since!r}",
            ) from exc

    try:
        return get_engine().distill(
            since=window,
            dry_run=body.dry_run,
            episode_ids=body.episode_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/memories/{agent_role}")
def list_memories(
    agent_role: str,
    limit: int = 20,
    include_stale: bool = False,
    knowledge_type: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """List memories for an agent."""
    engine = get_engine()
    return engine.list_memories(
        agent_role,
        limit=limit,
        include_stale=include_stale,
        knowledge_type=knowledge_type,
        category=category,
    )


@app.post("/remote/command")
def remote_command(req: RemoteCommandAPIRequest) -> dict[str, Any]:
    """Handle an authenticated inbound remote command."""
    engine = get_engine()
    return engine.handle_remote_command(req.model_dump())


@app.post("/remote/telegram")
def remote_telegram(update: dict[str, Any]) -> dict[str, Any]:
    """Handle a Telegram webhook-style update."""
    engine = get_engine()
    return engine.handle_remote_command(request_from_telegram_update(update))


@app.post("/remote/replays/cleanup")
def cleanup_remote_replays(req: RemoteReplayCleanupRequest | None = None) -> dict[str, Any]:
    """Delete expired remote replay records."""
    engine = get_engine()
    req = req or RemoteReplayCleanupRequest()
    return engine.cleanup_remote_replays(ttl_seconds=req.ttl_seconds)


@app.get("/observability")
def observability() -> dict[str, Any]:
    """Return an operational observability/RPG snapshot."""
    engine = get_engine()
    return engine.observability_snapshot()


@app.get("/dashboard", response_class=HTMLResponse)
def web_dashboard(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str = "",
) -> HTMLResponse:
    engine = get_engine()
    auth_error = _dashboard_auth_error(engine.settings, authorization, token, request)
    if auth_error is not None:
        if auth_error.status_code == 401 and not authorization and not token:
            return HTMLResponse(_render_dashboard_login(), status_code=401)
        raise auth_error
    return HTMLResponse(render_dashboard(engine.observability_snapshot()))


@app.get("/dashboard/login", response_class=HTMLResponse)
def dashboard_login() -> HTMLResponse:
    engine = get_engine()
    if not getattr(engine.settings, "web_dashboard_token", ""):
        raise HTTPException(status_code=503, detail="web dashboard auth is not configured")
    return HTMLResponse(_render_dashboard_login())


@app.post("/dashboard/login")
def dashboard_login_submit(
    request: Request,
    dashboard_token: str = Form(...),
) -> RedirectResponse:
    engine = get_engine()
    expected = getattr(engine.settings, "web_dashboard_token", "")
    if not expected:
        raise HTTPException(status_code=503, detail="web dashboard auth is not configured")
    if not compare_digest(dashboard_token, expected):
        raise HTTPException(status_code=401, detail="invalid dashboard token")

    response = RedirectResponse("/dashboard", status_code=303)
    ttl = getattr(engine.settings, "dashboard_session_ttl_seconds", 12 * 60 * 60)
    response.set_cookie(
        "kompany_dashboard_session",
        _dashboard_session_value(expected),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=ttl,
    )
    return response


@app.get("/dashboard/logout")
def dashboard_logout() -> RedirectResponse:
    response = RedirectResponse("/dashboard/login", status_code=303)
    response.delete_cookie(
        "kompany_dashboard_session",
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/dashboard/action")
def dashboard_action(
    req: DashboardActionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    token: str = "",
) -> dict[str, Any]:
    engine = get_engine()
    auth_error = _dashboard_auth_error(engine.settings, authorization, token, request)
    if auth_error is not None:
        raise auth_error
    action = req.action.strip().lower().replace("_", "-")
    if action == "runtime-status":
        result = engine.get_runtime_state()
    elif action == "heartbeat":
        result = engine.heartbeat_once()
    elif action == "approvals":
        result = {"approvals": engine.list_approvals()}
    elif action == "replay-cleanup":
        result = engine.cleanup_remote_replays()
    elif action == "approve":
        if not req.approval_id:
            raise HTTPException(status_code=400, detail="approval_id is required")
        approval = engine.approve_request(req.approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        result = {"approval": approval}
    elif action == "reject":
        if not req.approval_id:
            raise HTTPException(status_code=400, detail="approval_id is required")
        approval = engine.reject_request(req.approval_id, reason=req.reason or "dashboard rejection")
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        result = {"approval": approval}
    elif action == "runtime-suspend":
        result = engine.suspend(reason=req.reason or "dashboard request")
    elif action == "runtime-resume":
        result = engine.resume()
    else:
        raise HTTPException(status_code=400, detail="unsupported dashboard action")
    return {"status": "ok", "action": action, "result": result}


def _dashboard_auth_error(
    settings: Any,
    authorization: str | None,
    token: str,
    request: Request | None = None,
) -> HTTPException | None:
    expected = getattr(settings, "web_dashboard_token", "")
    if not expected:
        return HTTPException(status_code=503, detail="web dashboard auth is not configured")

    supplied = token
    if not supplied and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            supplied = value

    if supplied and compare_digest(supplied, expected):
        return None
    if request is not None:
        session = request.cookies.get("kompany_dashboard_session", "")
        ttl = getattr(settings, "dashboard_session_ttl_seconds", 12 * 60 * 60)
        if session and _dashboard_session_valid(expected, session, ttl):
            return None
    return HTTPException(status_code=401, detail="invalid dashboard token")


def _dashboard_session_value(token: str) -> str:
    issued_at = str(int(time.time()))
    return f"{issued_at}.{_dashboard_session_signature(token, issued_at)}"


def _dashboard_session_valid(token: str, session: str, ttl_seconds: int) -> bool:
    issued_at, separator, signature = session.partition(".")
    if not separator or not issued_at.isdigit():
        return False
    if int(issued_at) + max(0, ttl_seconds) < int(time.time()):
        return False
    expected = _dashboard_session_signature(token, issued_at)
    return compare_digest(signature, expected)


def _dashboard_session_signature(token: str, issued_at: str) -> str:
    return hmac.new(
        token.encode("utf-8"),
        f"kompany-dashboard-session:{issued_at}".encode("utf-8"),
        "sha256",
    ).hexdigest()


def _render_dashboard_login() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kompany Dashboard Login</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif; background: radial-gradient(circle at top left, #26345f, #10131a 46%, #080a10); color: #f6f7fb; }
    main { width: min(440px, calc(100vw - 32px)); background: rgba(24, 29, 41, .9); border: 1px solid #2a3347; border-radius: 22px; padding: 28px; box-shadow: 0 20px 60px rgba(0, 0, 0, .3); }
    .label { color: #a8b3cf; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    h1 { margin: 8px 0 12px; }
    p { color: #c7d2ef; }
    label { display: grid; gap: 8px; margin: 20px 0; }
    input { width: 100%; border: 1px solid #3a4b6d; border-radius: 12px; padding: 12px; background: #10131a; color: #f6f7fb; font: inherit; }
    button { width: 100%; border: 0; border-radius: 12px; padding: 12px; background: linear-gradient(135deg, #78ffd6, #80d8ff); color: #10131a; font-weight: 800; cursor: pointer; }
  </style>
</head>
<body>
  <main>
    <div class="label">Kompany Dashboard</div>
    <h1>Enter dashboard token</h1>
    <p>Use the configured dashboard token to start a private browser session.</p>
    <form method="post" action="/dashboard/login">
      <label>
        Dashboard token
        <input name="dashboard_token" type="password" autocomplete="current-password" autofocus required>
      </label>
      <button type="submit">Open dashboard</button>
    </form>
  </main>
</body>
</html>"""


@app.get("/runtime")
def runtime_status() -> dict[str, Any]:
    """Return engine runtime state."""
    engine = get_engine()
    return engine.get_runtime_state()


@app.post("/heartbeat")
def heartbeat(req: HeartbeatRequest | None = None) -> dict[str, Any]:
    """Run one heartbeat check."""
    engine = get_engine()
    req = req or HeartbeatRequest()
    return engine.heartbeat_once(dispatch=req.dispatch, adapter=req.adapter)


@app.post("/notifications/dispatch")
def dispatch_notifications(req: DispatchNotificationsRequest) -> list[dict[str, Any]]:
    """Dispatch notification events."""
    engine = get_engine()
    return engine.dispatch_notifications(req.events, adapter=req.adapter)


class SuspendRequest(BaseModel):
    reason: str = "manual"


@app.get("/tools/policies")
def list_tool_policies(agent_role: str | None = None) -> list[dict[str, Any]]:
    """List tool authorization policies."""
    engine = get_engine()
    return engine.list_tool_policies(agent_role=agent_role)


@app.post("/tools/policies")
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


@app.post("/tools/authorize")
def authorize_tool(req: ToolAuthorizationRequest) -> dict[str, Any]:
    """Check whether an agent may use a tool."""
    engine = get_engine()
    return engine.authorize_tool(req.agent_role, req.tool_name, purpose=req.purpose)


@app.post("/tools/use")
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


@app.post("/runtime/suspend")
def runtime_suspend(req: SuspendRequest) -> dict[str, Any]:
    """Suspend the engine."""
    engine = get_engine()
    return engine.suspend(reason=req.reason)


@app.post("/runtime/resume")
def runtime_resume() -> dict[str, Any]:
    """Resume the engine."""
    engine = get_engine()
    return engine.resume()


class CreateBackupRequest(BaseModel):
    label: str = "manual"


@app.post("/backups")
def create_backup(req: CreateBackupRequest) -> dict[str, Any]:
    """Create a labeled SQLite snapshot."""
    engine = get_engine()
    return engine.create_backup(label=req.label)


@app.get("/backups")
def list_backups() -> list[dict[str, Any]]:
    """List SQLite snapshots, newest first."""
    engine = get_engine()
    return engine.list_backups()


@app.post("/backups/{backup_id}/restore")
def restore_backup(backup_id: str) -> dict[str, Any]:
    """Restore a SQLite snapshot."""
    engine = get_engine()
    try:
        return engine.restore_backup(backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/credentials")
def list_credentials() -> list[dict[str, Any]]:
    engine = get_engine()
    return engine.list_credentials()


@app.post("/credentials")
def set_credential(req: CredentialRequest) -> dict[str, Any]:
    engine = get_engine()
    return engine.set_credential(req.name, req.value)


@app.delete("/credentials/{name}")
def delete_credential(name: str) -> dict[str, Any]:
    engine = get_engine()
    return engine.delete_credential(name)


@app.post("/credentials/rotate-key")
def rotate_credential_key(req: CredentialKeyRotationRequest) -> dict[str, Any]:
    engine = get_engine()
    return engine.rotate_credential_key(req.new_vault_key)


@app.get("/status")
def get_status() -> dict[str, Any]:
    """Get company status."""
    engine = get_engine()
    cfo = engine.registry.get("cfo")
    summary = cfo.get_summary()
    active = engine.projects.list_active()
    return {
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
    }


@app.post("/debate")
def run_debate(req: DebateRequest) -> dict[str, Any]:
    """Run a full multi-agent debate on a strategic question."""
    from kompany.core.debate import DebateEngine

    engine = get_engine()
    stage = engine.settings.company_stage or "solo"
    debate_engine = DebateEngine(engine.registry, stage=stage)
    result = debate_engine.run(
        question=req.question,
        company_state=engine.get_company_state(),
    )
    return {
        "question": result.question,
        "rounds": [[pos.model_dump() for pos in rnd] for rnd in result.rounds],
        "synthesis": result.synthesis.model_dump() if result.synthesis else None,
        "decision": result.decision.model_dump() if result.decision else None,
    }


@app.get("/projects")
def list_projects() -> list[dict[str, Any]]:
    """List active projects."""
    engine = get_engine()
    active = engine.projects.list_active()
    return [
        {
            "id": p.id,
            "name": p.name,
            "type": p.type.value,
            "status": p.status.value,
            "target_amount": p.target_amount,
            "funded_amount": p.funded_amount,
        }
        for p in active
    ]


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    """Get a specific project by ID."""
    engine = get_engine()
    p = engine.projects.get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    tasks = engine.projects.list_tasks(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "type": p.type.value,
        "status": p.status.value,
        "target_amount": p.target_amount,
        "funded_amount": p.funded_amount,
        "plan": p.plan,
        "assigned_agents": p.assigned_agents,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "agent": t.assigned_agent,
                "status": t.status.value,
            }
            for t in tasks
        ],
    }


@app.get("/ledger")
def get_ledger(limit: int = 10) -> list[dict]:
    """Get recent ledger entries."""
    engine = get_engine()
    return engine.ledger.get_recent(limit=limit)


@app.get("/approvals")
def list_approvals(status: str | None = None) -> list[dict[str, Any]]:
    """List approval requests. ``status`` query param filters by state;
    omit it to get only pending rows (legacy default)."""
    engine = get_engine()
    if status is None:
        return engine.list_approvals()
    return [r.model_dump(mode="json") for r in engine.approvals.list_by_status(status=status)]


@app.get("/approvals/{approval_id}")
def show_approval(approval_id: str) -> dict[str, Any]:
    """Return one approval with its thread + comment timeline."""
    engine = get_engine()
    data = engine.get_approval(approval_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return data


@app.get("/inbox")
def inbox() -> list[dict[str, Any]]:
    """RPG inbox: pending + snoozed approvals with comment counts."""
    return get_engine().inbox()


@app.post("/approvals/{approval_id}/approve")
def approve_request(
    approval_id: str,
    req: ApproveApprovalRequest | None = None,
) -> dict[str, Any]:
    """Approve a pending request."""
    engine = get_engine()
    comment = req.comment if req and req.comment else None
    result = engine.approve_request(approval_id, comment_body=comment)
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/reject")
def reject_request(approval_id: str, req: RejectApprovalRequest) -> dict[str, Any]:
    """Reject a pending request."""
    engine = get_engine()
    result = engine.reject_request(
        approval_id,
        reason=req.reason,
        comment_body=req.comment or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/revise")
def revise_request(approval_id: str, req: ReviseApprovalRequest) -> dict[str, Any]:
    """Counter-propose: original -> ``revision_requested``, new pending row spawned."""
    engine = get_engine()
    result = engine.request_approval_revision(
        approval_id,
        counter=req.counter,
        comment_body=req.comment or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/snooze")
def snooze_request(approval_id: str, req: SnoozeApprovalRequest) -> dict[str, Any]:
    """Snooze an approval; watchdog auto-unsnoozes when due."""
    engine = get_engine()
    result = engine.snooze_approval(
        approval_id,
        minutes=req.minutes,
        comment_body=req.comment or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/cancel")
def cancel_request(approval_id: str, req: CancelApprovalRequest) -> dict[str, Any]:
    """Cancel an approval (terminal)."""
    engine = get_engine()
    result = engine.cancel_approval(
        approval_id,
        reason=req.reason or None,
        comment_body=req.comment or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/comment")
def comment_request(approval_id: str, req: CommentApprovalRequest) -> dict[str, Any]:
    """Append a free-form comment to an approval thread."""
    engine = get_engine()
    result = engine.comment_on_approval(
        approval_id,
        body=req.body,
        by_type=req.by_type,
        by_id=req.by_id,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/projects/{project_id}/execute")
def execute_project(project_id: str) -> dict[str, Any]:
    """Execute a revenue project's tasks autonomously."""
    engine = get_engine()
    p = engine.projects.get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return engine.execute_project(project_id)
