"""Health probe, LLM spend, SSE events, anima, channels, workspaces, agents.

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

router = APIRouter()

@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for the Tauri sidecar shell.

    The Rust shell polls this endpoint after spawning the sidecar
    binary and only opens the WebView once it returns 200. Keep it
    cheap — no DB hits, no engine spin-up.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Onboarding REST surface (used by the Tauri shell + browser flow).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


@router.get("/llm/spend/summary")
def llm_spend_summary() -> dict[str, Any]:
    """Aggregate AI_COST ledger rows for the dashboard LLM spend chip.

    The PREVIEW / STREAM / LEDGER discipline (memory:
    [[engineering-cost-visibility-discipline]]) requires every LLM call
    to land an ``AI_COST`` ledger row. This endpoint sums them so the
    cyberpunk header can render a running total without subscribing to
    SSE just to keep a counter. The chip refreshes on every ``llm.spend``
    SSE envelope (incremental) and reconciles against this endpoint on
    page load / focus-restore (authoritative).
    """
    engine = get_engine()
    try:
        totals = engine.ledger.get_totals()
    except Exception:
        return {"total_usd": 0.0, "row_count": 0}
    # ledger amount for AI_COST rows is stored as a negative number
    # (it's an expense). The chip wants the magnitude.
    raw = float(totals.get("ai_cost", 0.0))
    total_usd = abs(raw)
    row = engine.db.execute(
        "SELECT COUNT(*) AS c FROM ledger WHERE category = 'ai_cost'"
    ).fetchone()
    row_count = int(row["c"]) if row else 0
    return {"total_usd": total_usd, "row_count": row_count}


# ---------------------------------------------------------------------------
# Web UI: SSE feed + static file serving
# ---------------------------------------------------------------------------

# The 11 canonical C-suite roles, in display order. Used as the source of
# truth for the office panel — the UI always shows 11 rows whether or not
# audit_log mentions a role.
C_SUITE_ROLES: tuple[str, ...] = (
    "ceo", "cfo", "cto", "cpo", "cmo", "cro",
    "coo", "csa", "ciso", "cos", "cv",
)

# Heartbeat keepalive interval (seconds). EventSource drops if the server
# stays silent for too long behind some proxies — 15s is the conservative
# default used by SSE bridges in the wild.
_SSE_HEARTBEAT_SECONDS = 15.0


async def _sse_event_stream() -> AsyncIterator[bytes]:
    """Async generator that yields SSE-formatted bytes for one client.

    Multiplexes:
      * an :class:`asyncio.Queue` fed by the global :class:`EventHub`
      * a periodic ``:keepalive`` comment so reverse proxies don't time out
    """
    hub = get_event_hub()
    # Capture the loop this SSE stream runs on so publishes from
    # background threads (the kickoff task) can hop back onto it via
    # call_soon_threadsafe instead of being dropped.
    hub.register_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
    hub._subscribers.add(queue)  # type: ignore[attr-defined]
    try:
        # Tell the client we're alive immediately. Some browsers wait for
        # the first byte before resolving the EventSource ``open`` event.
        yield b": connected\n\n"
        while True:
            try:
                envelope = await asyncio.wait_for(
                    queue.get(),
                    timeout=_SSE_HEARTBEAT_SECONDS,
                )
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue
            event_type = envelope.get("type", "message")
            event_id = envelope.get("id", 0)
            # Wrap the type into the JSON payload and use the *default*
            # ``message`` event so a single ``onmessage`` listener can
            # demultiplex every audit.<x> / inbox.updated / health.event
            # flavor. EventSource cannot wildcard named events, and we
            # don't want to enumerate every possible ``audit.<subtype>``.
            envelope_out = {
                "type": event_type,
                "data": dict(envelope.get("data", {}) or {}),
            }
            data = json.dumps(envelope_out, default=str)
            payload = (
                f"id: {event_id}\n"
                f"data: {data}\n\n"
            ).encode("utf-8")
            yield payload
    finally:
        hub._subscribers.discard(queue)  # type: ignore[attr-defined]


@router.get("/events")
async def events_stream() -> StreamingResponse:
    """Server-Sent Events feed of live engine activity.

    Browser ``EventSource`` clients connect here to receive ``audit.*``,
    ``inbox.updated``, ``health.event``, and ``episode.recorded`` events
    in real time. No history is replayed — only events emitted after the
    client connects.
    """
    return StreamingResponse(
        _sse_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable nginx response buffering — SSE depends on chunks
            # arriving as soon as the server flushes them.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/anima/state")
def anima_state() -> dict[str, Any]:
    """Anima persona state (06-12-anima-persona PRD D5): valence,
    energy, derived tone, last_diary_date."""
    return get_engine().anima_state()


@router.get("/anima/diary")
def anima_diary(limit: int = 30) -> list[dict[str, Any]]:
    """Recent Anima diary entries, newest first."""
    return get_engine().anima_diary_list(limit=limit)


@router.get("/channels/status")
def channels_status() -> dict[str, Any]:
    """Channel adapter health + outbox counts (06-12-channels PRD D5)."""
    return get_engine().channels_status()


@router.get("/channels/outbox")
def channels_outbox(limit: int = 20) -> list[dict[str, Any]]:
    """Most recent channel outbox rows, newest first."""
    return get_engine().outbox_list(limit=limit)


class WorkspaceSwitchRequest(BaseModel):
    name: str


class WorkspaceCreateRequest(BaseModel):
    name: str
    label: str = ""


@router.get("/workspaces")
def workspaces_list() -> dict[str, Any]:
    """Workspace registry (issue #15): active brand + all entries.
    Canonical shape: ``engine.workspaces_list()`` — same on CLI/MCP/SDK."""
    return get_engine().workspaces_list()


@router.post("/workspaces/switch")
def workspaces_switch(req: WorkspaceSwitchRequest) -> dict[str, Any]:
    """Switch the active workspace (brand) and rebind this server.

    Flips the registry, then drops the cached engine + event hub so the
    NEXT request rebuilds against the new data dir. Connected SSE
    clients keep the old hub — the web UI reloads the page after a
    switch, which is the supported recovery. ``restart_required`` is
    True only when KOMPANY_DATA_DIR pins this process to one dir (the
    registry flip then cannot take effect here — daemon plist case)."""
    from kompany.config.workspaces import WorkspaceError
    from kompany.core.event_hub import reset_event_hub

    try:
        entry = get_engine().workspace_switch(req.name)
    except WorkspaceError as exc:
        return {"error": str(exc)}
    reset_engine()
    reset_event_hub()
    return entry


@router.post("/workspaces")
def workspaces_create(req: WorkspaceCreateRequest) -> dict[str, Any]:
    """Create + register a fresh workspace dir (not yet active).
    Switch to it, then run onboarding against the new dir."""
    from kompany.config.workspaces import WorkspaceError

    try:
        return get_engine().workspace_create(req.name, label=req.label)
    except WorkspaceError as exc:
        return {"error": str(exc)}


@router.get("/agents/work-summary")
def agents_work_summary() -> dict[str, dict[str, Any]]:
    """Per-agent task-history summary (06-12-panel-truthfulness #22).

    Keyed by lowercase role; lets the UI distinguish "worked but no
    episode closed yet" from "never worked"."""
    return get_engine().agent_work_summary()


@router.get("/agents/status")
def agents_status() -> list[dict[str, Any]]:
    """Return the live status of all 11 C-suite roles for the office panel.

    Source of truth: the ``agent_status`` table if present, with the most
    recent ``audit_log`` activity as a heuristic fallback when an agent
    has no explicit status row yet. The response always contains exactly
    11 rows in canonical display order.
    """
    engine = get_engine()
    rows_by_role: dict[str, dict[str, Any]] = {}

    # Primary source: agent_status store.
    try:
        for entry in engine.agent_status.list_all():  # type: ignore[attr-defined]
            role = (entry.get("agent_role") or entry.get("role") or "").lower()
            if role:
                rows_by_role[role] = entry
    except Exception:
        pass

    # Fallback: tail audit_log for any agent activity in the last ~5 min.
    if not rows_by_role:
        try:
            recent = engine.audit.recent(limit=200)
        except Exception:
            recent = []
        for row in recent:
            role = (row.get("agent_role") or "").lower()
            if not role or role in rows_by_role:
                continue
            rows_by_role[role] = {
                "agent_role": role,
                "status": "busy",
                "last_action": row.get("action"),
                "updated_at": row.get("timestamp"),
            }

    result = []
    from kompany.state.activity import derive_activity_kind

    for role in C_SUITE_ROLES:
        row = rows_by_role.get(role)
        if row is None:
            result.append({
                "role": role.upper(),
                "status": "idle",
                "last_action": None,
                "latency_ms": None,
                "activity_kind": "idle",
                "project_id": None,
                "project_type": None,
            })
        else:
            status = row.get("status") or "idle"
            project_type = row.get("project_type")
            result.append({
                "role": role.upper(),
                "status": status,
                "last_action": row.get("last_action") or row.get("action"),
                "latency_ms": row.get("latency_ms"),
                # Advisory kind: prefer the stored value; derive for audit-fallback
                # rows that predate the column.
                "activity_kind": row.get("activity_kind")
                or derive_activity_kind(role, status, project_type),
                "project_id": row.get("project_id"),
                "project_type": project_type,
            })
    return result

