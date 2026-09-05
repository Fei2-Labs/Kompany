"""Customer extensions REST surface (07-24 four-layer). Thin delegates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kompany.interfaces.api_parts.deps import get_engine

router = APIRouter()


class InstallRequest(BaseModel):
    path: str


class RunRequest(BaseModel):
    job: dict[str, Any] = {}
    timeout_seconds: int = 120


class EnabledRequest(BaseModel):
    enabled: bool


@router.get("/extensions")
def extensions_list() -> list[dict]:
    return get_engine().extensions_list()


@router.get("/extensions/{extension_id}")
def extension_show(extension_id: str) -> dict:
    row = get_engine().extension_show(extension_id)
    if row is None:
        raise HTTPException(status_code=404, detail="extension not found")
    return row


@router.post("/extensions/install")
def extension_install(req: InstallRequest) -> dict:
    try:
        return get_engine().extension_install(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/extensions/{extension_id}/run")
def extension_run(extension_id: str, req: RunRequest) -> dict:
    try:
        return get_engine().extension_run(extension_id, req.job, timeout_seconds=req.timeout_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/extensions/{extension_id}/enabled")
def extension_set_enabled(extension_id: str, req: EnabledRequest) -> dict:
    row = get_engine().extension_set_enabled(extension_id, req.enabled)
    if row is None:
        raise HTTPException(status_code=404, detail="extension not found")
    return row


@router.delete("/extensions/{extension_id}")
def extension_remove(extension_id: str) -> dict:
    row = get_engine().extension_remove(extension_id)
    if row is None:
        raise HTTPException(status_code=404, detail="extension not found")
    return row
