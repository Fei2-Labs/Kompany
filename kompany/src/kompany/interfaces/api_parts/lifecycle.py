"""Decision packets, delivery, health events, templates, prefs, targets, glossary, audit.

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


class ExecutePacketRequest(BaseModel):
    approval_id: str


@router.post("/decision-packet/execute")
def execute_decision_packet(req: ExecutePacketRequest) -> dict[str, Any]:
    """Execute an approved decision-chain packet under governance."""
    engine = get_engine()
    try:
        return engine.execute_decision_packet(req.approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ReleaseDeliveryRequest(BaseModel):
    approval_id: str


@router.post("/delivery/release")
def release_delivery(req: ReleaseDeliveryRequest) -> dict[str, Any]:
    """Release a delivery package after delivery_approval is approved."""
    engine = get_engine()
    try:
        return engine.release_delivery(req.approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RetrospectiveRequest(BaseModel):
    project_id: str


@router.post("/retrospective")
def run_retrospective(req: RetrospectiveRequest) -> dict[str, Any]:
    """Run or replay a CoS retrospective for a project."""
    engine = get_engine()
    return engine.run_retrospective(req.project_id)


class HealthResolveRequest(BaseModel):
    action: str
    snooze_minutes: int | None = None
    resolved_by: str = "player"


@router.get("/health/events")
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


@router.get("/health/events/{event_id}")
def get_health_event(event_id: str) -> dict[str, Any]:
    """Fetch one health event."""
    engine = get_engine()
    row = engine.get_health_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Health event not found: {event_id}")
    return row


@router.post("/health/events/{event_id}/resolve")
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


@router.get("/templates")
def list_templates() -> list[dict[str, Any]]:
    """List available company templates."""
    return get_engine().list_templates()


@router.get("/templates/{template_id}")
def get_template(template_id: str) -> dict[str, Any]:
    """Fetch one template by id (includes rendered mission body)."""
    try:
        return get_engine().show_template(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/templates/{template_id}/apply")
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


# ---------------------------------------------------------------------------
# Company targets (mission-targets task 05-19)
# ---------------------------------------------------------------------------


class UIPreferencesResponse(BaseModel):
    """Founder's dashboard appearance preferences."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str
    auto_enabled: bool
    reduce_motion: str  # "auto" | "on" | "off"


class UIPreferencesUpdateRequest(BaseModel):
    """Partial update for ``PATCH /preferences`` — omitted fields are untouched."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str | None = None
    auto_enabled: bool | None = None
    reduce_motion: str | None = None


@router.get("/preferences", response_model=UIPreferencesResponse)
def get_preferences() -> UIPreferencesResponse:
    """Return the founder's stored UI preferences (DB is source of truth)."""
    prefs = get_engine().get_ui_preferences()
    return UIPreferencesResponse(**prefs.model_dump(mode="json"))


@router.patch("/preferences", response_model=UIPreferencesResponse)
def patch_preferences(req: UIPreferencesUpdateRequest) -> UIPreferencesResponse:
    """Patch one or more UI preferences. 422 on an invalid ``reduce_motion``."""
    try:
        prefs = get_engine().set_ui_preferences(
            theme_id=req.theme_id,
            auto_enabled=req.auto_enabled,
            reduce_motion=req.reduce_motion,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return UIPreferencesResponse(**prefs.model_dump(mode="json"))


@router.get("/targets")
def get_targets() -> dict[str, Any]:
    """Return the founder / team_proposal / agreed targets trio.

    Used by the cyberpunk header (``ledger.js``) to render the
    ``rev: $X / $Y`` and ``days: N/M`` stats. Always returns a payload —
    the founder slot is populated even on a fresh install (all zeros).
    """
    bundle = get_engine().get_targets_bundle()
    return {
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
        # Convenience: the authoritative numbers downstream readers want
        # without picking the right key themselves.
        "authoritative": get_engine().get_targets().model_dump(mode="json"),
    }


@router.post("/targets/review")
def post_targets_review() -> dict[str, Any]:
    """Re-run the team feasibility review on demand.

    Creates a fresh ``approval_request(action_type='target_feasibility')``
    and returns its payload. Returns ``404`` if no founder targets are
    set yet — the founder must complete onboarding first.
    """
    payload = get_engine().run_target_feasibility_review()
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="No founder targets set; complete onboarding first.",
        )
    return payload


class _ProposeFirstDirectivesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force_heuristic: bool = False
    # When true, wipe any existing drafts before proposing so the LLM
    # regenerates a fresh set. Triggered by the step-5 "regenerate
    # proposals" button.
    force: bool = False


@router.post("/onboarding/propose_first_directives")
def post_propose_first_directives(
    req: _ProposeFirstDirectivesRequest | None = None,
) -> dict[str, Any]:
    """Team-proposes-first-directives — implements the contract in
    ``docs/context/operations.md:60-62`` for the first-move wizard step.

    Reads ``targets.agreed`` + company state, runs a short CEO pass,
    writes up to 3 draft projects, and returns a structured result so
    the UI can distinguish "team thought hard" from "team unreachable,
    here are generic seeds." Lying about the source of the directives
    erodes the whole product story.

    Response shape::

        {
            "status": "ok" | "team_failed" | "no_targets" | "heuristic",
            "directives": [...],
            "error_code": str|None,
            "error_message": str|None,
            "provider": str|None
        }

    Pass ``force_heuristic=true`` to explicitly accept the local
    fallback after the founder sees an error ("use built-in starter
    pack" path).

    Idempotent on existing drafts: when drafts already exist
    (template-staged or from a prior call), returns them without
    spending another LLM call.
    """
    engine = get_engine()
    force_h = bool(req and req.force_heuristic)
    force_regen = bool(req and req.force)
    return engine.propose_first_directives(
        force_heuristic=force_h, force=force_regen
    )


class _DiscussFirstDirectivesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)


@router.post("/onboarding/discuss_first_directives")
def post_discuss_first_directives(
    req: _DiscussFirstDirectivesRequest,
) -> dict[str, Any]:
    """Founder Q&A on the current first-week directives.

    Runs ONE CEO LLM call that takes (a) the founder's question and
    (b) the current draft directives; returns the CEO's answer and
    optionally a revised directive list. The frontend stacks each
    Q&A pair below the cards; when ``directives_changed=true`` it
    re-renders the cards in place.
    """
    engine = get_engine()
    return engine.discuss_first_directives(req.question)


# ---------------------------------------------------------------------------
# Company glossary (glossary-and-drift-detection task 05-19)
# ---------------------------------------------------------------------------


class GlossaryWriteRequest(BaseModel):
    """Body for ``POST /glossary`` / ``PATCH /glossary/<term>``."""

    model_config = ConfigDict(extra="forbid")

    term: str | None = Field(default=None, min_length=1)
    definition: str | None = Field(default=None, min_length=1)
    forbidden_synonyms: list[str] | None = None


@router.get("/glossary")
def list_glossary() -> list[dict[str, Any]]:
    """Return every glossary entry."""
    return get_engine().list_glossary()


@router.get("/glossary/{term}")
def show_glossary_term(term: str) -> dict[str, Any]:
    """Look up one term (case-insensitive). Returns 404 when missing."""
    entry = get_engine().get_glossary_term(term)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Term not found: {term!r}")
    return entry


@router.post("/glossary")
def create_glossary_term(req: GlossaryWriteRequest) -> dict[str, Any]:
    """Insert a brand-new glossary term (founder-sourced)."""
    if not req.term or not req.definition:
        raise HTTPException(
            status_code=422,
            detail="term and definition are required to create a glossary entry",
        )
    try:
        return get_engine().add_glossary_term(
            term=req.term,
            definition=req.definition,
            forbidden_synonyms=req.forbidden_synonyms,
            added_by="founder",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/glossary/{term}")
def patch_glossary_term(term: str, req: GlossaryWriteRequest) -> dict[str, Any]:
    """Update an existing glossary term's definition or forbidden synonyms."""
    try:
        return get_engine().update_glossary_term(
            term=term,
            definition=req.definition,
            forbidden_synonyms=req.forbidden_synonyms,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/glossary/{term}")
def delete_glossary_term(term: str) -> dict[str, Any]:
    """Drop a glossary term. Returns ``{"removed": bool}``."""
    removed = get_engine().remove_glossary_term(term)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Term not found: {term!r}")
    return {"removed": True, "term": term}


@router.get("/audit/recent")
def audit_recent(limit: int = 40) -> list[dict[str, Any]]:
    """Return the most recent audit events, oldest-first, for the live
    timeline to BACKFILL on dashboard load.

    SSE has no replay, so events fired before the browser's EventSource
    connects (e.g. a kickoff that ran while the founder was still on the
    onboarding screen) are otherwise invisible. This lets the timeline
    show what already happened, then continue live from SSE.
    """
    engine = get_engine()
    rows = engine.audit.recent(limit=max(1, min(int(limit), 200)))
    # ``recent`` returns newest-first; reverse so the timeline appends in
    # chronological order.
    out: list[dict[str, Any]] = []
    for row in reversed(rows):
        out.append({
            "event_type": row.get("event_type"),
            "action": row.get("action"),
            "agent_role": row.get("agent_role"),
            "project_id": row.get("project_id"),
            "created_at": row.get("created_at") or row.get("timestamp"),
        })
    return out

