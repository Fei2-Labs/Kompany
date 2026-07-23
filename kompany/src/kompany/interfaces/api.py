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
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
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

# CORS (07-14 cloud-deploy): when the engine runs on a VPS, the
# kompany-world UI and other browser clients need cross-origin access.
# Opt-in via KOMPANY_CORS_ORIGINS (comma-separated). Default off —
# local sidecar mode serves UI from the same origin, no CORS needed.
_cors_origins = os.environ.get("KOMPANY_CORS_ORIGINS", "").strip()
if _cors_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
    alerts as _alerts,
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
    _alerts,
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


def _board_ui_dir() -> Path:
    """Resolve the built operations-board SPA inside the installed package.

    Source lives in ``kompany-core/board-ui/``; ``vite build`` emits into
    ``src/kompany/board_ui/dist/`` so the wheel / PyInstaller bundle ships
    it. May be absent in a dev checkout that hasn't run a board build yet —
    callers must guard for ``is_dir()`` being False.
    """
    return Path(__file__).resolve().parent.parent / "board_ui" / "dist"


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

# Kompany World (Phaser office visualization) at /world/. The built
# SPA lives in ``world_ui/dist/`` — deploy it there on the VPS. When
# absent (local dev without the world UI), the mount is skipped.
_WORLD_UI_DIR = Path(__file__).resolve().parent.parent / "world_ui" / "dist"
if _WORLD_UI_DIR.is_dir():
    app.mount("/world", StaticFiles(directory=str(_WORLD_UI_DIR), html=True), name="world")


# ---------------------------------------------------------------------
# Operations board SPA at ``/`` (the new default landing).
#
# The React/Vite board is built into ``board_ui/dist/`` and served at the
# site root. The old cyberpunk terminal stays reachable at ``/ui/`` (mount
# above). This block is the LAST thing registered in the module so every
# API router wins over the SPA catch-all — only paths no router claimed
# fall through to ``index.html`` (client-side routing).
#
# When the board hasn't been built yet (dev checkout, no ``npm run build``),
# ``board_ui/dist/`` is absent: fall back to the old ``/ui/`` redirect so
# startup never crashes.
_BOARD_UI_DIR = _board_ui_dir()
_BOARD_INDEX = _BOARD_UI_DIR / "index.html"

# Non-SPA URL prefixes that must NEVER be rewritten to the board's
# ``index.html`` — they belong to API routers, the old UI, docs, or health.
_NON_SPA_PREFIXES = (
    "ui",
    "dashboard",
    "observability",
    "events",
    "agents",
    "health",
    "docs",
    "redoc",
    "openapi.json",
    "assets",
)

if _BOARD_INDEX.is_file():
    # Serve the hashed JS/CSS bundle. ``base: '/'`` in vite.config.ts makes
    # the SPA request ``/assets/*``, so mount the assets dir there.
    _BOARD_ASSETS = _BOARD_UI_DIR / "assets"
    if _BOARD_ASSETS.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(_BOARD_ASSETS)),
            name="board-assets",
        )

    @app.get("/", include_in_schema=False)
    def board_root() -> FileResponse:
        """Serve the operations board at the site root."""
        return FileResponse(str(_BOARD_INDEX))

    @app.get("/{full_path:path}", include_in_schema=False)
    def board_spa(full_path: str) -> FileResponse:
        """SPA catch-all: return ``index.html`` for client-side routes.

        Registered after every API router, so real endpoints win. Only
        unknown, non-API GET paths reach here; they get the board shell and
        React Router resolves the route in the browser. API/UI/docs prefixes
        are excluded so a stray miss 404s instead of masking a real route.
        """
        first = full_path.split("/", 1)[0]
        if first in _NON_SPA_PREFIXES:
            raise HTTPException(status_code=404)
        return FileResponse(str(_BOARD_INDEX))

else:  # pragma: no cover - depends on build artifacts being present

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        """Board not built yet — fall back to the cyberpunk terminal."""
        return RedirectResponse(url="/ui/", status_code=307)
