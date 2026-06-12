"""Kompany REST API — FastAPI interface to the engine."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
from pathlib import Path
from secrets import compare_digest
from typing import Any, AsyncIterator

from fastapi import BackgroundTasks, Body, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from kompany.core.engine import KompanyEngine
from kompany.core.event_hub import get_event_hub
from kompany.interfaces.mcp_bridge import router as mcp_bridge_router
from kompany.interfaces.web import render_dashboard
from kompany.remote import request_from_telegram_update

app = FastAPI(
    title="Kompany API",
    description="Autonomous business operating system for solo founders.",
    version="0.1.0",
)
app.include_router(mcp_bridge_router)

_engine: KompanyEngine | None = None


def get_engine() -> KompanyEngine:
    global _engine
    if _engine is None:
        _engine = KompanyEngine()
    return _engine


def reset_engine() -> None:
    """Drop the cached engine instance.

    Used by the onboarding REST endpoint after a fresh install so the
    next ``get_engine()`` call picks up the just-written ``kompany.db``.
    Also handy for tests that swap out data dirs across requests.
    """
    global _engine
    _engine = None


@app.on_event("startup")
async def _start_background_workers() -> None:
    """Start engine background workers (watchdog scanner + daemon ticker).

    ``KompanyEngine.start()`` existed but nothing ever awaited it in a
    server boot, so the watchdog's proactive scanner and the tick loop
    only ran in tests (which call ``scan_once``/``tick_once`` directly).
    Live-verification finding, 06-12-daemon-tick-loop.

    getattr-guarded: tests stub ``_engine`` with minimal fakes that have
    no worker surface, and the hook must not force every fake to grow one.
    """
    start = getattr(get_engine(), "start", None)
    if start is not None:
        await start()


@app.on_event("shutdown")
async def _stop_background_workers() -> None:
    stop = getattr(_engine, "stop", None)
    if stop is not None:
        await stop()


# Domain routers (ADR-0003 split). Include order preserves the original
# in-file route registration order; routers are imported after ``app`` and
# the engine accessors exist, and resolve them lazily via api_parts.deps.
from kompany.interfaces.api_parts import (  # noqa: E402
    channel as _channel,
    dashboard as _dashboard,
    integrations as _integrations,
    lifecycle as _lifecycle,
    models as _models,
    onboarding as _onboarding,
    onboarding_ping as _onboarding_ping,
    ops as _ops,
    projects as _projects,
    runtime as _runtime,
    settings as _settings,
    system as _system,
)

_API_PART_MODULES = (
    _onboarding,
    _onboarding_ping,
    _models,
    _settings,
    _integrations,
    _channel,
    _lifecycle,
    _ops,
    _dashboard,
    _runtime,
    _projects,
    _system,
)

for _part in _API_PART_MODULES:
    _router = getattr(_part, "router", None)
    if _router is not None:
        app.include_router(_router)

# Re-export every part-level name (routes, models, helpers) so existing
# imports and monkeypatch targets like ``kompany.interfaces.api.list_projects``
# keep working. Engine accessors are skipped: the canonical ``get_engine`` /
# ``reset_engine`` live in this module.
_PART_EXPORT_SKIP = {"app", "router", "get_engine", "reset_engine"}
for _part in _API_PART_MODULES:
    for _name, _value in vars(_part).items():
        if _name.startswith("__") or _name in _PART_EXPORT_SKIP:
            continue
        if _name not in globals():
            globals()[_name] = _value

def _web_ui_dir() -> Path:
    """Resolve the bundled ``web_ui/`` directory inside the installed package."""
    return Path(__file__).resolve().parent.parent / "web_ui"


# Clean URL alias for the onboarding page so callers can link to
# ``/ui/onboarding`` instead of the bare ``.html`` file. Must be
# registered before the StaticFiles mount so it wins over the static
# router. Returns a 307 so the WebView updates its location bar to the
# canonical static path (preserves relative asset resolution).
@app.get("/ui/onboarding", include_in_schema=False)
def onboarding_alias() -> RedirectResponse:
    return RedirectResponse(url="/ui/onboarding.html", status_code=307)


# Mount the cyberpunk SPA at /ui. ``html=True`` tells StaticFiles to serve
# ``index.html`` for the directory root, so ``/ui/`` works without a list view.
_WEB_UI_DIR = _web_ui_dir()
if _WEB_UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_WEB_UI_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Redirect bare ``/`` to the web UI."""
    return RedirectResponse(url="/ui/", status_code=307)
