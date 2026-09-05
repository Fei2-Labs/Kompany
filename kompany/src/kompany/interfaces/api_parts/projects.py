"""Status, debate, projects, ledger and approvals.

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


@router.get("/status")
def get_status() -> dict[str, Any]:
    """Get company status (canonical dict — see core/status_ops.py)."""
    from kompany.core.status_ops import build_status

    return build_status(get_engine())


@router.post("/debate")
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


@router.get("/projects")
def list_projects(include_draft: bool = False) -> list[dict[str, Any]]:
    """List projects.

    By default returns only ``status='active'`` rows (legacy behaviour
    used by the dashboard timeline + inbox).  Pass ``?include_draft=1``
    to additionally include rows ``Templates.apply`` staged as drafts —
    the onboard-v2 First Move step (PRD 05-19-onboard-v2-flow) reads
    those to render its three suggested-directive cards.

    Draft rows use a literal ``'draft'`` status string that's outside
    :class:`ProjectStatus`, so we serialise the raw value instead of
    routing through ``Projects.list_active``.
    """
    engine = get_engine()
    if not include_draft:
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
    # Raw scan including drafts. We can't push these through
    # ``_row_to_project`` because that constructor coerces status into
    # the enum (no DRAFT member). Read the SQL row directly.
    rows = engine.db.execute(
        "SELECT id, name, type, status, target_amount, funded_amount "
        "FROM projects "
        "WHERE status IN ('active', 'draft') "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "type": r["type"],
            "status": r["status"],
            "target_amount": r["target_amount"],
            "funded_amount": r["funded_amount"],
        }
        for r in rows
    ]


@router.get("/projects/{project_id}")
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
                # Additive (09-04 dashboard): the NEEDS YOU feed surfaces
                # BLOCKED tasks and must show the connect / approve ask
                # (result.founder_action); task rows omitted result.
                "result": t.result,
                "agent": t.assigned_agent,
                "status": t.status.value,
            }
            for t in tasks
        ],
    }


@router.get("/ledger")
def get_ledger(limit: int = 10) -> list[dict]:
    """Get recent ledger entries."""
    engine = get_engine()
    return engine.ledger.get_recent(limit=limit)


@router.get("/approvals")
def list_approvals(status: str | None = None) -> list[dict[str, Any]]:
    """List approval requests. ``status`` query param filters by state;
    omit it to get only pending rows (legacy default)."""
    engine = get_engine()
    if status is None:
        return engine.list_approvals()
    return [r.model_dump(mode="json") for r in engine.approvals.list_by_status(status=status)]


@router.get("/approvals/{approval_id}")
def show_approval(approval_id: str) -> dict[str, Any]:
    """Return one approval with its thread + comment timeline."""
    engine = get_engine()
    data = engine.get_approval(approval_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return data


@router.get("/inbox")
def inbox() -> list[dict[str, Any]]:
    """RPG inbox: pending + snoozed approvals with comment counts."""
    return get_engine().inbox()


@router.post("/approvals/{approval_id}/approve")
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


@router.post("/approvals/{approval_id}/reject")
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


@router.post("/approvals/{approval_id}/revise")
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


@router.post("/approvals/{approval_id}/snooze")
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


@router.post("/approvals/{approval_id}/cancel")
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


@router.post("/approvals/{approval_id}/comment")
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


@router.post("/projects/{project_id}/execute")
def execute_project(project_id: str) -> dict[str, Any]:
    """Execute a revenue project's tasks autonomously."""
    engine = get_engine()
    p = engine.projects.get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return engine.execute_project(project_id)


def _kickoff_project_safely(project_id: str) -> None:
    """Background-task wrapper around ``engine.execute_project`` used by
    the First-Move activation handler.

    A founder finishing onboarding picked a directive and expected the
    team to actually START WORKING. Just flipping ``status='active'``
    leaves the dashboard idle until something else triggers a run —
    historically the founder had to call ``/projects/{id}/execute``
    manually, which they have no way to discover. Bug surfaced
    2026-05-26 when a freshly-onboarded user landed on the dashboard
    with cash $50, 1 active project, and 11 idle agents — staring at
    an empty office wondering "what now?".

    Any exception inside this background task is swallowed + audited
    so a runner blow-up never poisons the HTTP response the founder
    is waiting on. The next dashboard refresh will surface the audit
    entry in the live timeline.
    """
    engine = get_engine()
    try:
        engine.execute_project(project_id)
    except Exception as exc:  # noqa: BLE001 — background swallow
        engine.audit.record(
            event_type="project.kickoff_failed",
            action=f"Background kickoff for project {project_id} failed",
            detail={"project_id": project_id, "error": str(exc)},
            project_id=project_id,
        )


class CancelProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = ""


@router.post("/projects/{project_id}/abandon")
@router.post("/projects/{project_id}/cancel")
def cancel_project(
    project_id: str, req: CancelProjectRequest | None = None
) -> dict[str, Any]:
    """Abandon a plan (#10): project → ``cancelled``, unfinished tasks
    stopped, open approval cards withdrawn, unspent envelope released
    back to the treasury (earmark accounting — no ledger row needed).
    AI cost already spent is real and stays in the ledger. Idempotent
    on an already-terminal project. ``/cancel`` is a legacy alias of
    ``/abandon`` — same handler, same engine op.
    """
    engine = get_engine()
    reason = (req.reason if req else "") or ""
    try:
        return engine.abandon_project(project_id, reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/activate")
def activate_project(
    project_id: str, background_tasks: BackgroundTasks = None  # type: ignore[assignment]
) -> dict[str, Any]:
    """Promote a ``status='draft'`` project to ``active``.

    Used by the onboard-v2 "First Move" step (PRD 05-19-onboard-v2-flow):
    after ``apply_template`` stages suggested directives as draft
    projects, the founder picks one and we flip its status so the engine
    will pick it up on the next directive sweep. The two unselected
    drafts stay in ``draft`` for later activation.

    Returns ``404`` when no project matches the id. Idempotent: an
    already-active project is returned unchanged.
    """
    engine = get_engine()
    # Touch the raw row first so we can distinguish "not found" from the
    # ``status != 'draft'`` branch ``Projects.get`` would happily return.
    row = engine.db.execute(
        "SELECT id, status FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    current_status = row["status"]
    if current_status == "active":
        # Idempotent — caller can replay this safely (e.g. retry after a
        # network blip during First Move submit).
        proj = engine.projects.get(project_id)
        return {
            "id": project_id,
            "status": "active",
            "previous_status": "active",
            "name": proj.name if proj else None,
        }
    # Flip the literal status string (``ProjectStatus`` has no DRAFT
    # member; ``Templates.apply`` writes ``'draft'`` directly).
    engine.db.execute(
        "UPDATE projects SET status = 'active', updated_at = datetime('now') "
        "WHERE id = ?",
        (project_id,),
    )
    engine.db.commit()
    engine.audit.record(
        event_type="project.activated",
        action=f"Activated project from {current_status} to active",
        detail={
            "project_id": project_id,
            "previous_status": current_status,
            "source": "onboarding.first_move",
        },
        project_id=project_id,
    )
    # Auto-kickoff: schedule the actual run in the background so the
    # founder doesn't land on an idle dashboard. The HTTP response
    # returns fast (sub-50ms) and the team starts working in parallel.
    # ``background_tasks`` is ``None`` for direct in-process calls
    # (tests, CLI) — skip the kickoff in that case so test fixtures
    # don't unexpectedly burn LLM tokens.
    kickoff_scheduled = False
    if background_tasks is not None:
        background_tasks.add_task(_kickoff_project_safely, project_id)
        engine.audit.record(
            event_type="project.kickoff_scheduled",
            action=f"Scheduled background kickoff for project {project_id}",
            detail={"project_id": project_id},
            project_id=project_id,
        )
        kickoff_scheduled = True
    proj = engine.projects.get(project_id)
    return {
        "id": project_id,
        "status": "active",
        "previous_status": current_status,
        "name": proj.name if proj else None,
        "kickoff_scheduled": kickoff_scheduled,
    }


# ---------------------------------------------------------------------------
# LLM spend summary (onboard-v2 cost chip + dashboard cost chip)
