"""Episodes, distillation, memories, remote commands and browser intake.

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
from kompany.interfaces.api_parts.channel import _process_directive_graceful  # noqa: F401
from kompany.remote import request_from_telegram_update  # noqa: F401

router = APIRouter()


@router.get("/episodes")
def list_episodes(retention: str | None = None) -> list[dict[str, Any]]:
    """List materialized project episodes."""
    engine = get_engine()
    return engine.list_episodes(retention_tier=retention)


@router.get("/episodes/{project_id}")
def get_episode(project_id: str) -> dict[str, Any]:
    """Fetch one episode by project id."""
    engine = get_engine()
    row = engine.get_episode(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {project_id}")
    return row


@router.post("/episodes/{project_id}/rebuild")
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


@router.post("/distillation/run")
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


@router.get("/memories/{agent_role}")
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


@router.post("/memories")
def ingest_memory(req: MemoryIngestRequest) -> dict[str, Any]:
    """Persist one bounded external observation for a project agent."""
    engine = get_engine()
    if req.project_id and engine.projects.get(req.project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project '{req.project_id}' not found")
    context = req.context or (f"project:{req.project_id}" if req.project_id else None)
    memory_id = engine.memory.remember(
        agent_role=req.agent_role,
        content=req.content.strip(),
        category=req.category,
        knowledge_type=req.knowledge_type,
        context=context,
    )
    engine.audit.record(
        "learning.external_memory_ingested",
        "Ingested bounded external observation",
        detail={"agent_role": req.agent_role, "category": req.category},
        project_id=req.project_id,
    )
    if req.project_id:
        engine.rebuild_episode(req.project_id)
    return {"id": memory_id, "project_id": req.project_id, "context": context}


@router.post("/remote/command")
def remote_command(req: RemoteCommandAPIRequest) -> dict[str, Any]:
    """Handle an authenticated inbound remote command."""
    engine = get_engine()
    return engine.handle_remote_command(req.model_dump())


@router.post("/remote/telegram")
def remote_telegram(update: dict[str, Any]) -> dict[str, Any]:
    """Handle a Telegram webhook-style update."""
    engine = get_engine()
    return engine.handle_remote_command(request_from_telegram_update(update))


# ---------------------------------------------------------------------------
# Browser intake hook (issue #23) — bookmarklet/extension → Kompany.
# ---------------------------------------------------------------------------


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    selection: str = ""
    note: str = ""
    kind: str = "auto"  # "auto" | "ops" | "dev"


# In-process flood/replay guard. Per-process state is fine: there is exactly
# one engine/server per machine (one-server rule) and the guard only has to
# stop a stuck bookmarklet/extension from hammering the CEO.
_INTAKE_RATE_LIMIT = 10  # requests per window
_INTAKE_RATE_WINDOW = 60.0  # seconds
_INTAKE_DEDUPE_WINDOW = 300.0  # seconds
_intake_request_times: list[float] = []
_intake_seen: dict[tuple[str, str], float] = {}


def _intake_reset_guards() -> None:
    """Test seam: clear rate-limit/dedupe state between cases."""
    _intake_request_times.clear()
    _intake_seen.clear()


def _intake_compose_text(req: IntakeRequest) -> str:
    lines = [f"Intake from browser: {req.note or req.url}"]
    lines.append(f"URL: {req.url}")
    if req.selection:
        lines.append(f"Selection: {req.selection}")
    return "\n".join(lines)


def _intake_file_github_issue(title: str, body: str) -> dict[str, Any]:
    """Best-effort ``gh issue create``. Never raises — absence/failure is data."""
    import subprocess

    try:
        proc = subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body", body],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return {"filed": False, "error": "gh not installed"}
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        return {"filed": False, "error": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        return {"filed": False, "error": (proc.stderr or proc.stdout).strip()[:500]}
    return {"filed": True, "issue_url": proc.stdout.strip()}


@router.post("/intake")
def browser_intake(
    req: IntakeRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Browser → Kompany intake (issue #23). Token-guarded, localhost-only.

    A bookmarklet/extension POSTs ``{url, selection?, note?, kind?}``.
    ``kind=ops`` (and ``auto``) routes the composed text through
    ``engine.process_directive`` — the CEO classifier IS the router, so ops
    work dispatches exactly like any founder directive. ``kind=dev`` never
    hits the CEO: it appends a bullet to ``<data_dir>/dev-queue.md`` and
    files a GitHub issue best-effort via ``gh``.

    Interface-parity exception: this is REST-only by design. Browser
    transport is REST-native; the CLI equivalent already exists as
    ``kompany directive`` itself, so no engine op / MCP tool is added.
    """
    engine = get_engine()
    settings = engine.settings
    expected = getattr(settings, "intake_token", "") or getattr(
        settings, "mobile_remote_token", ""
    )
    if not expected:
        raise HTTPException(status_code=503, detail="intake is not configured (set INTAKE_TOKEN)")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid intake token")

    if req.kind not in {"auto", "ops", "dev"}:
        raise HTTPException(status_code=422, detail="kind must be auto|ops|dev")

    now = time.monotonic()
    # Rate limit: sliding window.
    _intake_request_times[:] = [t for t in _intake_request_times if now - t < _INTAKE_RATE_WINDOW]
    if len(_intake_request_times) >= _INTAKE_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="intake rate limit exceeded (10/min)")
    _intake_request_times.append(now)
    # Dedupe identical url+note within the window.
    key = (req.url, req.note)
    last = _intake_seen.get(key)
    if last is not None and now - last < _INTAKE_DEDUPE_WINDOW:
        return {"status": "duplicate", "message": "identical intake seen within 5 minutes"}
    _intake_seen[key] = now

    text = _intake_compose_text(req)
    if req.kind in {"auto", "ops"}:
        result = _process_directive_graceful(text, None)
        return {"status": "routed", "kind": "ops", "result": result}

    # kind == "dev" — founder's queue, never the CEO.
    data_dir = Path(getattr(settings, "data_dir", Path("~/.kompany").expanduser()))
    queue_path = data_dir / "dev-queue.md"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    bullet = f"- {req.note or 'Browser intake'} — {req.url}"
    if req.selection:
        bullet += f" — {req.selection[:200]}"
    with queue_path.open("a", encoding="utf-8") as fh:
        fh.write(bullet + "\n")
    title = (req.note or f"Browser intake: {req.url}")[:120]
    github = _intake_file_github_issue(title, text)
    return {
        "status": "queued",
        "kind": "dev",
        "queue_path": str(queue_path),
        "github": github,
    }


@router.post("/remote/replays/cleanup")
def cleanup_remote_replays(req: RemoteReplayCleanupRequest | None = None) -> dict[str, Any]:
    """Delete expired remote replay records."""
    engine = get_engine()
    req = req or RemoteReplayCleanupRequest()
    return engine.cleanup_remote_replays(ttl_seconds=req.ttl_seconds)
