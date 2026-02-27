"""Kompany REST API — FastAPI interface to the engine."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from kompany.core.engine import KompanyEngine

app = FastAPI(
    title="Kompany API",
    description="Autonomous business operating system for solo founders.",
    version="0.1.0",
)

_engine: KompanyEngine | None = None


def get_engine() -> KompanyEngine:
    global _engine
    if _engine is None:
        _engine = KompanyEngine()
    return _engine


class DirectiveRequest(BaseModel):
    text: str


class InitRequest(BaseModel):
    name: str
    product: str
    balance: float = 0.0
    stage: str = "solo"


@app.post("/init")
def init_company(req: InitRequest) -> dict[str, Any]:
    """Initialize a new Kompany."""
    engine = get_engine()
    engine.initialize_company(
        name=req.name, product=req.product,
        balance=req.balance, stage=req.stage,
    )
    return {"status": "initialized", "name": req.name, "balance": req.balance}


@app.post("/directive")
def send_directive(req: DirectiveRequest) -> dict[str, Any]:
    """Send a directive to Kompany."""
    engine = get_engine()
    result = engine.process_directive(req.text)
    return {
        "status": result.status,
        "message": result.message,
        "project_id": result.project_id,
        "total_ai_cost": result.total_ai_cost,
        "agents_used": result.agents_used,
    }


@app.get("/status")
def get_status() -> dict[str, Any]:
    """Get company status."""
    engine = get_engine()
    cfo = engine.registry.get("cfo")
    summary = cfo.get_summary()
    active = engine.projects.list_active()
    return {
        "company": engine.settings.company_name,
        "balance": summary["balance"],
        "total_income": summary["total_income"],
        "total_expenses": summary["total_expenses"],
        "total_ai_costs": summary["total_ai_costs"],
        "active_projects": len(active),
    }


@app.get("/projects")
def list_projects() -> list[dict[str, Any]]:
    """List active projects."""
    engine = get_engine()
    active = engine.projects.list_active()
    return [
        {
            "id": p.id,
            "name": p.name,
            "type": p.type.value,
            "status": p.status.value,
            "target_amount": p.target_amount,
            "funded_amount": p.funded_amount,
        }
        for p in active
    ]


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    """Get a specific project by ID."""
    engine = get_engine()
    p = engine.projects.get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    tasks = engine.projects.list_tasks(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "type": p.type.value,
        "status": p.status.value,
        "target_amount": p.target_amount,
        "funded_amount": p.funded_amount,
        "plan": p.plan,
        "assigned_agents": p.assigned_agents,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "agent": t.assigned_agent,
                "status": t.status.value,
            }
            for t in tasks
        ],
    }


@app.get("/ledger")
def get_ledger(limit: int = 10) -> list[dict]:
    """Get recent ledger entries."""
    engine = get_engine()
    return engine.ledger.get_recent(limit=limit)


@app.post("/projects/{project_id}/execute")
def execute_project(project_id: str) -> dict[str, Any]:
    """Execute a revenue project's tasks autonomously."""
    engine = get_engine()
    p = engine.projects.get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return engine.execute_project(project_id)
