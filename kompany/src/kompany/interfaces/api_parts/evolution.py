"""Artifact-evolution REST surface (08-29 R2). Thin delegates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kompany.interfaces.api_parts.deps import get_engine

router = APIRouter()


class ProposeRequest(BaseModel):
    kind: str
    target: str
    instruction: str


class RevertRequest(BaseModel):
    reason: str = "founder revert"


@router.get("/evolution")
def evolution_status() -> dict:
    return get_engine().evolution_status()


@router.get("/evolution/proposals")
def evolution_list(limit: int = 20, status: str | None = None) -> list[dict]:
    return get_engine().evolution_list(limit=limit, status=status)


@router.get("/evolution/proposals/{proposal_id}")
def evolution_show(proposal_id: str) -> dict:
    row = get_engine().evolution_show(proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return row


@router.post("/evolution/propose")
def evolution_propose(req: ProposeRequest) -> dict:
    try:
        return get_engine().evolution_propose(req.kind, req.target, req.instruction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/evolution/proposals/{proposal_id}/revert")
def evolution_revert(proposal_id: str, req: RevertRequest) -> dict:
    row = get_engine().evolution_revert(proposal_id, req.reason)
    if row is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return row
