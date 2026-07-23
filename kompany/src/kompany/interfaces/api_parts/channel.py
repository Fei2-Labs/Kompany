"""Directive + CEO-channel session endpoints.

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

from kompany.channels.context import DirectiveContext
from kompany.core.event_hub import get_event_hub  # noqa: F401
from kompany.interfaces.api_parts.deps import get_engine, reset_engine  # noqa: F401
from kompany.interfaces.api_parts.models import *  # noqa: F401,F403
from kompany.interfaces.api_parts.onboarding_ping import _classify_ping_error  # noqa: F401
from kompany.state.models import SESSION_TERMINAL_STATUSES

router = APIRouter()


def _flatten_directive_result(result: Any) -> dict[str, Any]:
    """Serialize a ``DirectiveResult`` to the shared parity dict.

    Delegates to ``DirectiveResult.to_dict()`` — the single source of truth
    shared by CLI/MCP/SDK so every non-web surface emits identical top-level
    keys (status / message / project_id / approval_id / total_ai_cost /
    agents_used / run_id / session_id). Used by /directive, /channel/send,
    /channel/*/go and /channel/*/abandon.
    """
    return result.to_dict()


def _process_directive_graceful(
    text: str,
    session_id: str | None,
    context: DirectiveContext | None = None,
) -> dict[str, Any]:
    """Run ``process_directive`` wrapped in the graceful-error contract.

    ``process_directive`` re-raises on LLM failure (its library contract).
    The HTTP layer must NOT surface that as a raw 500 — the founder sees
    "directive failed: HTTP 500" with no clue. Instead we catch it and
    return a graceful 200 with a classified, honest error (reusing
    :func:`_classify_ping_error`) so the live timeline / channel thread can
    render the real cause (quota / auth / network / provider).

    Shared by ``/directive`` and ``/channel/send`` — single source of truth,
    no duplicated error logic (code-reuse spec).
    """
    engine = get_engine()
    try:
        if context is None:
            result = engine.process_directive(text, session_id=session_id)
        else:
            result = engine.process_directive(
                text,
                session_id=session_id,
                context=context,
            )
    except Exception as exc:  # noqa: BLE001 — surface honestly, never 500
        detail = f"{type(exc).__name__}: {exc}"
        code = _classify_ping_error(detail)
        session = (
            engine.channel.get_session(session_id)
            if session_id is not None
            else None
        )
        conversation_continues = bool(
            session is not None
            and session.state not in SESSION_TERMINAL_STATUSES
        )
        human = {
            "rate_limited": "Your LLM provider hit a rate/quota limit. Wait a moment and retry.",
            "unauthorized": "Your LLM provider rejected the API key. Check it in settings.",
            "network": "Couldn't reach your LLM provider. Check your connection and retry.",
            "provider_error": "Your LLM provider rejected the request (it may not support this model/params). Try a different model in settings.",
        }.get(code, "The team's LLM call failed. Retry, or switch model/provider in settings.")
        return {
            "status": "failed",
            "message": human,
            "error_code": code,
            "project_id": session.project_id if session else None,
            "approval_id": None,
            "total_ai_cost": 0.0,
            "agents_used": [],
            "run_id": None,
            "session_id": session_id,
            "active_agent_id": session.active_agent_id if session else "ceo",
            "previous_agent_id": None,
            "handoff_id": None,
            "conversation_continues": conversation_continues,
            "delegation_id": None,
            "delegation_status": None,
        }
    return _flatten_directive_result(result)


@router.post("/directive")
def send_directive(req: DirectiveRequest) -> dict[str, Any]:
    """Send a directive to Kompany (backward-compat alias for /channel/send).

    Delegates to the same handler path as ``/channel/send`` so existing
    callers (CLI/SDK/MCP/internal) keep working unchanged: the response keeps
    the legacy keys plus session_id/run_id (added in PR0/PR1). No duplicated
    logic — both routes funnel through :func:`_process_directive_graceful`.
    """
    return _process_directive_graceful(req.text, req.session_id)


class ChannelSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    # Continue an existing session (clarify reply / gated context); omit to
    # open a fresh session.
    session_id: str | None = None
    # Optional canonical scope for board Chat. Callers continuing a session
    # should keep these values stable.
    project_id: str | None = None
    agent_id: str | None = None


@router.post("/channel/send")
def channel_send(req: ChannelSendRequest) -> dict[str, Any]:
    """Founder message into the CEO channel.

    Opens a new session (``session_id`` omitted) or continues an existing one
    (clarify reply). Returns the full flattened ``DirectiveResult`` — status
    may be ``clarify`` (CEO asks back), ``gated`` (threshold gate, awaiting
    GO), or any execute/answer status. LLM failures surface as a graceful 200
    with a classified ``error_code``, never a 500.
    """
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
        project_id=req.project_id,
        active_agent_id=req.agent_id,
    )
    return _process_directive_graceful(
        req.text,
        req.session_id,
        context=context,
    )


def _session_to_dict(session: Any) -> dict[str, Any]:
    """Serialize a ``ConversationSession`` row for the channel REST surface."""
    return {
        "session_id": session.id,
        "state": session.state.value,
        "route": session.route,
        "clarify_turns": session.clarify_turns,
        "created_at": str(session.created_at) if session.created_at is not None else None,
        "closed_at": str(session.closed_at) if session.closed_at is not None else None,
        "run_id": session.run_id,
        "directive_id": session.directive_id,
        "company_id": session.company_id,
        "project_id": session.project_id,
        "channel": session.channel,
        "account_id": session.account_id,
        "chat_id": session.chat_id,
        "thread_id": session.thread_id,
        "sender_id": session.sender_id,
        "active_agent_id": session.active_agent_id,
        "previous_agent_id": session.previous_agent_id,
        "handoff_id": session.handoff_id,
        "handoff_reason": session.handoff_reason,
        "handoff_confidence": session.handoff_confidence,
        "session_epoch": session.session_epoch,
        "approval_id": session.approval_id,
    }


def _turn_to_dict(turn: Any) -> dict[str, Any]:
    """Serialize a ``ConversationTurn`` row for the channel thread view."""
    return {
        "turn_index": turn.turn_index,
        "role": turn.role,
        "agent_id": turn.agent_id,
        "content": turn.content,
        "kind": turn.kind,
        "cost": turn.cost,
        "run_id": turn.run_id,
        "directive_id": turn.directive_id,
        "created_at": str(turn.created_at) if turn.created_at is not None else None,
    }


@router.get("/channel/sessions")
def channel_sessions(
    state: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List CEO-channel sessions, newest first.

    Optional ``state`` filters by session lifecycle state (open / clarifying /
    gated / dispatched / answered / abandoned); ``limit`` caps the result set
    (sane default 50). Used by the Web UI to restore the thread list on load.
    """
    engine = get_engine()
    limit = max(1, min(int(limit), 200))
    sessions = engine.channel.list_sessions(state=state, limit=limit)
    return {"sessions": [_session_to_dict(s) for s in sessions]}


@router.get("/channel/sessions/{session_id}")
def channel_session_detail(session_id: str) -> dict[str, Any]:
    """A single session plus its ordered turns (the full thread).

    404 if the session is unknown. The thread restores in-flight turn state on
    reload (no SSE replay) — clients reconcile live cost via
    ``/channel/runs/{run_id}/cost``.
    """
    engine = get_engine()
    session = engine.channel.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown channel session {session_id!r}")
    turns = engine.channel.session_turns(session_id)
    return {
        "session": _session_to_dict(session),
        "turns": [_turn_to_dict(t) for t in turns],
    }


@router.post("/channel/sessions/{session_id}/go")
def channel_go(session_id: str) -> dict[str, Any]:
    """Founder GO on a gated session — execute the held directive.

    Returns the flattened ``DirectiveResult``. The engine returns a clean
    failed result (no exceptions) for illegal GOs (non-gated / closed /
    unknown), so this never 500s.
    """
    engine = get_engine()
    return _flatten_directive_result(engine.channel_go(session_id))


@router.post("/channel/sessions/{session_id}/abandon")
def channel_abandon(session_id: str) -> dict[str, Any]:
    """Founder abandons a session — close it without executing.

    Returns the flattened ``DirectiveResult``. The engine returns a clean
    failed result (no exceptions) for non-abandonable sessions.
    """
    engine = get_engine()
    return _flatten_directive_result(engine.channel_abandon(session_id))


@router.get("/channel/runs/{run_id}/cost")
def channel_run_cost(run_id: str) -> dict[str, Any]:
    """Authoritative per-run AI cost — reconcile source for reload restore.

    SSE has no replay, so a mid-execution reload misses ``llm.spend`` events
    emitted while the page was loading. The channel bubble restores the
    accumulated cost by summing the ledger's ``ai_cost`` rows for this
    ``run_id`` (each ledger row carries ``run_id``). Returned as a positive
    total (AI-cost rows are stored as negative expense amounts).
    """
    engine = get_engine()
    row = engine.db.execute(
        "SELECT COALESCE(SUM(ABS(amount)), 0.0) AS total "
        "FROM ledger WHERE run_id = ? AND category = 'ai_cost'",
        (run_id,),
    ).fetchone()
    total = float(row["total"]) if row and row["total"] is not None else 0.0
    return {"run_id": run_id, "total_cost": total}


@router.post("/override")
def request_override(req: OverrideRequest) -> dict[str, Any]:
    """Request an override with a risk briefing."""
    engine = get_engine()
    return engine.process_override(req.text)


@router.post("/decision-packet")
def prepare_decision_packet(req: DecisionPacketRequest) -> dict[str, Any]:
    """Prepare a full decision-chain packet without executing it."""
    engine = get_engine()
    return engine.prepare_decision_packet(req.text, target_amount=req.target_amount)


@router.post("/projects/{project_id}/resume")
def resume_project(project_id: str) -> dict[str, Any]:
    """Resume a project from persisted task/checkpoint state."""
    engine = get_engine()
    try:
        return engine.resume_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
