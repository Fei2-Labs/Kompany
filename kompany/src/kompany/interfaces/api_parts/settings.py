"""Init, model setting, self-update, model-source and founder settings.

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


@router.post("/init")
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


class ModelSettingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_model: str = ""
    provider: str = ""
    base_url: str = ""
    available_models: list[str] = []
    error: str = ""


class SetModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(..., min_length=1)


@router.get("/settings/model", response_model=ModelSettingResponse)
def get_model_setting() -> ModelSettingResponse:
    """Current model + the models the configured endpoint advertises.

    Lets the founder switch model from the UI when their provider drops
    a model (e.g. swedeapi's gpt-5.x went down mid-session and the only
    fix was hand-editing .env). For custom endpoints we query the live
    /models list; other providers return their tier model with no list.
    """
    engine = get_engine()
    s = engine.settings
    current = getattr(s, "model_primary", "") or ""
    base_url = getattr(s, "custom_base_url", "") or ""
    available: list[str] = []
    err = ""
    provider = "custom" if base_url else "default"
    if base_url:
        try:
            from kompany.llm.providers import list_openai_compatible_models
            available = list_openai_compatible_models(
                base_url, getattr(s, "custom_api_key", "") or ""
            )
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
    return ModelSettingResponse(
        current_model=current, provider=provider, base_url=base_url,
        available_models=available, error=err,
    )


@router.post("/settings/model", response_model=ModelSettingResponse)
def set_model_setting(req: SetModelRequest) -> ModelSettingResponse:
    """Switch the model for all three tiers. Persists to company_config
    (``custom_model_picked``, the same key the engine applies on boot)
    AND updates the live engine so the change takes effect immediately —
    no restart needed."""
    engine = get_engine()
    model = req.model.strip()
    engine.db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES ('custom_model_picked', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value, updated_at = excluded.updated_at""",
        (model,),
    )
    engine.db.commit()
    engine.settings.model_apex = model
    engine.settings.model_primary = model
    engine.settings.model_economy = model
    engine.audit.record(
        "settings.model_changed",
        f"Founder switched model to {model}",
        detail={"model": model},
    )
    return get_model_setting()


class ModelSourceRequest(BaseModel):
    """Body for ``PUT /settings/model-source``.

    ``kind=None`` clears the source (legacy per-token billing). The
    execution loop is derived by the engine — there is deliberately no
    vehicle/runner input here (PRD 06-11 D1).
    """

    model_config = ConfigDict(extra="forbid")
    kind: str | None = None
    billing_mode: str | None = None
    monthly_fee_usd: float | None = None
    price_overrides: dict[str, tuple[float, float]] | None = None


class SelfUpdateProposeRequest(BaseModel):
    """Body for ``POST /self-update/propose``."""

    instruction: str


@router.post("/self-update/propose")
def self_update_propose(req: SelfUpdateProposeRequest) -> dict:
    """Governed self-update propose flow (06-12-self-update-pipeline)."""
    try:
        return get_engine().self_update_propose(req.instruction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/self-update/proposals")
def self_update_proposals(limit: int = 20) -> list[dict]:
    """Recent self-update proposals, newest first."""
    return get_engine().self_update_list(limit=limit)


@router.get("/self-update/proposals/{proposal_id}")
def self_update_proposal(proposal_id: str) -> dict:
    row = get_engine().self_update_show(proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return row


@router.get("/settings/model-source")
def get_model_source_setting() -> dict | None:
    """Active model source (or null). Same dict shape as SDK/MCP."""
    return get_engine().get_model_source()


@router.put("/settings/model-source")
def set_model_source_setting(req: ModelSourceRequest) -> dict:
    """Set or clear the active model source; persists to the settings YAML."""
    payload: dict | None = None
    if req.kind is not None:
        payload = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        return get_engine().set_model_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings/detect-clis")
def detect_clis_setting() -> dict:
    """Probe PATH for agent CLIs that unlock zero-key model sources."""
    return get_engine().detect_agent_clis()


class FounderProfileRequest(BaseModel):
    """Body for ``PUT /founder/profile`` (#7).

    Partial payloads merge over the stored profile; ``clear=true``
    removes it entirely.
    """

    model_config = ConfigDict(extra="forbid")
    address: str | None = None
    pronouns: str | None = None
    comms_style: str | None = None
    language: str | None = None
    working_hours: str | None = None
    timezone: str | None = None
    risk_tolerance: str | None = None
    clear: bool = False


class FounderRulesRequest(BaseModel):
    """Body for ``PUT /founder/rules`` (#6).

    ``hard`` is a list of ``{kind, match, action}`` entries (kind ∈
    exclude_capability | budget_cap | forbid_paid_category); ``soft``
    is free text. Top-level merge; ``clear=true`` removes both.
    """

    model_config = ConfigDict(extra="forbid")
    hard: list[dict] | None = None
    soft: str | None = None
    clear: bool = False


@router.get("/founder/profile")
def get_founder_profile_setting() -> dict | None:
    """Founder profile (or null). Same dict shape as SDK/MCP."""
    return get_engine().get_founder_profile()


@router.put("/founder/profile")
def set_founder_profile_setting(req: FounderProfileRequest) -> dict:
    """Merge-set (or clear) the founder profile."""
    payload: dict | None = None
    if not req.clear:
        payload = {
            k: v for k, v in req.model_dump().items()
            if k != "clear" and v is not None
        }
    try:
        return get_engine().set_founder_profile(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/founder/rules")
def get_founder_rules_setting() -> dict | None:
    """Founder rules {hard, soft} (or null). Same dict shape as SDK/MCP."""
    return get_engine().get_founder_rules()


@router.put("/founder/rules")
def set_founder_rules_setting(req: FounderRulesRequest) -> dict:
    """Merge-set (or clear) the founder rules."""
    payload: dict | None = None
    if not req.clear:
        payload = {
            k: v for k, v in req.model_dump().items()
            if k != "clear" and v is not None
        }
    try:
        return get_engine().set_founder_rules(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
