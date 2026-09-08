"""One-button update REST surface (Stage C step 9). Thin delegates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kompany.interfaces.api_parts.deps import get_engine

router = APIRouter()


class ApplyRequest(BaseModel):
    version: str | None = None


class ModeRequest(BaseModel):
    mode: str


@router.get("/update")
def update_status() -> dict:
    """Installed vs latest release, layout, phase of a running update."""
    return get_engine().update_status()


@router.post("/update/check")
def update_check() -> dict:
    return get_engine().update_check()


@router.post("/update/apply")
def update_apply(req: ApplyRequest | None = None) -> dict:
    """Start the update in the background; poll GET /update for progress."""
    return get_engine().update_apply((req.version if req else None) or None)


@router.post("/update/rollback")
def update_rollback() -> dict:
    return get_engine().update_rollback()


@router.post("/update/mode")
def update_mode(req: ModeRequest) -> dict:
    try:
        return get_engine().update_set_mode(req.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
