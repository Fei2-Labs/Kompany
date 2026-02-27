"""Kompany Python SDK — programmatic access to the engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kompany.core.engine import KompanyEngine


class Kompany:
    """High-level SDK for interacting with Kompany programmatically.

    Usage:
        from kompany import Kompany
        k = Kompany()
        k.init("MyCompany", "AI tools", balance=50.0)
        result = k.directive("Buy a Mac Studio M4 128GB")
        print(result.message)
    """

    def __init__(self, config_path: str | None = None):
        self._engine = KompanyEngine(config_path=config_path)

    def init(
        self,
        name: str,
        product: str,
        balance: float = 0.0,
        stage: str = "solo",
    ) -> None:
        """Initialize a new company."""
        self._engine.initialize_company(
            name=name, product=product, balance=balance, stage=stage
        )

    def directive(self, text: str) -> dict[str, Any]:
        """Send a directive and return the result as a dict."""
        result = self._engine.process_directive(text)
        return {
            "status": result.status,
            "message": result.message,
            "project_id": result.project_id,
            "total_ai_cost": result.total_ai_cost,
            "agents_used": result.agents_used,
        }

    def status(self) -> dict[str, Any]:
        """Get company status."""
        cfo = self._engine.registry.get("cfo")
        summary = cfo.get_summary()
        active = self._engine.projects.list_active()
        return {
            "company": self._engine.settings.company_name,
            "balance": summary["balance"],
            "total_income": summary["total_income"],
            "total_expenses": summary["total_expenses"],
            "total_ai_costs": summary["total_ai_costs"],
            "active_projects": len(active),
        }

    def projects(self) -> list[dict[str, Any]]:
        """List active projects."""
        active = self._engine.projects.list_active()
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

    def project(self, project_id: str) -> dict[str, Any] | None:
        """Get a specific project by ID."""
        p = self._engine.projects.get(project_id)
        if not p:
            return None
        tasks = self._engine.projects.list_tasks(p.id)
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

    def balance(self) -> float:
        """Get current balance."""
        return self._engine.ledger.get_balance()

    def ledger(self, limit: int = 10) -> list[dict]:
        """Get recent ledger entries."""
        return self._engine.ledger.get_recent(limit=limit)

    def execute_project(self, project_id: str) -> dict:
        """Execute a revenue project's tasks autonomously."""
        return self._engine.execute_project(project_id)
