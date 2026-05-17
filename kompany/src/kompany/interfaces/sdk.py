"""Kompany Python SDK — programmatic access to the engine."""

from __future__ import annotations

from typing import Any

from kompany.core.debate import DebateEngine
from kompany.core.engine import KompanyEngine


class Kompany:
    """High-level SDK for interacting with Kompany programmatically.

    Usage:
        from kompany import Kompany
        k = Kompany()
        k.init("MyCompany", capital=50.0, goal="Buy a Mac Studio M4 128GB")
        result = k.directive("Buy a Mac Studio M4 128GB")
        print(result["message"])
    """

    def __init__(self, config_path: str | None = None):
        self._engine = KompanyEngine(config_path=config_path)

    def init(
        self,
        name: str,
        capital: float = 0.0,
        goal: str = "",
        time_horizon: str = "",
        exclusions: str = "",
    ) -> dict[str, Any]:
        """Initialize a new company."""
        self._engine.initialize_company(
            name=name,
            capital=capital,
            goal=goal,
            time_horizon=time_horizon,
            exclusions=exclusions,
        )
        return {
            "status": "initialized",
            "name": name,
            "capital": capital,
            "goal": goal,
            "time_horizon": time_horizon,
            "exclusions": exclusions,
            "stage": "solo",
        }

    def directive(self, text: str) -> dict[str, Any]:
        """Send a directive and return the result as a dict."""
        result = self._engine.process_directive(text)
        return {
            "status": result.status,
            "message": result.message,
            "project_id": result.project_id,
            "approval_id": result.approval_id,
            "total_ai_cost": result.total_ai_cost,
            "agents_used": result.agents_used,
        }

    def debate(self, question: str) -> dict[str, Any]:
        """Run a full multi-agent debate on a strategic question."""
        stage = self._engine.settings.company_stage or "solo"
        debate_engine = DebateEngine(self._engine.registry, stage=stage)
        result = debate_engine.run(
            question=question,
            company_state=self._engine.get_company_state(),
        )
        return {
            "question": result.question,
            "rounds": [[pos.model_dump() for pos in rnd] for rnd in result.rounds],
            "synthesis": result.synthesis.model_dump() if result.synthesis else None,
            "decision": result.decision.model_dump() if result.decision else None,
        }

    def remote_command(
        self,
        source: str,
        text: str,
        chat_id: str = "",
        bearer_token: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle an authenticated inbound remote command."""
        return self._engine.handle_remote_command({
            "source": source,
            "text": text,
            "chat_id": chat_id,
            "bearer_token": bearer_token,
            "payload": payload or {},
        })

    def cleanup_remote_replays(self, ttl_seconds: int | None = None) -> dict[str, Any]:
        """Delete expired remote replay records."""
        return self._engine.cleanup_remote_replays(ttl_seconds=ttl_seconds)

    def observability(self) -> dict[str, Any]:
        """Return an operational observability/RPG snapshot."""
        return self._engine.observability_snapshot()

    def status(self) -> dict[str, Any]:
        """Get company status."""
        cfo = self._engine.registry.get("cfo")
        summary = cfo.get_summary()
        active = self._engine.projects.list_active()
        return {
            "company": self._engine.settings.company_name,
            "goal": self._engine.settings.company_goal,
            "time_horizon": self._engine.settings.company_time_horizon,
            "exclusions": self._engine.settings.company_exclusions,
            "stage": self._engine.settings.company_stage,
            "balance": summary["balance"],
            "total_income": summary["total_income"],
            "total_expenses": summary["total_expenses"],
            "total_ai_costs": abs(summary["total_ai_costs"]),
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

    def resume_project(self, project_id: str) -> dict[str, Any]:
        """Resume a project from persisted task/checkpoint state."""
        return self._engine.resume_project(project_id)

    def prepare_decision_packet(
        self,
        text: str,
        target_amount: float | None = None,
    ) -> dict[str, Any]:
        """Prepare a full decision-chain packet without executing it."""
        return self._engine.prepare_decision_packet(text, target_amount=target_amount)

    def execute_decision_packet(self, approval_id: str) -> dict[str, Any]:
        """Execute an approved decision-chain packet under governance."""
        return self._engine.execute_decision_packet(approval_id)

    def release_delivery(self, approval_id: str) -> dict[str, Any]:
        """Release a delivery package after delivery_approval is approved."""
        return self._engine.release_delivery(approval_id)

    def run_retrospective(self, project_id: str) -> dict[str, Any]:
        """Run (or replay) the CoS retrospective for a project."""
        return self._engine.run_retrospective(project_id)

    def runtime_status(self) -> dict[str, Any]:
        """Return engine runtime state."""
        return self._engine.get_runtime_state()

    def heartbeat(
        self,
        dispatch: bool = False,
        adapter: str = "dry-run",
    ) -> dict[str, Any]:
        """Run one heartbeat check."""
        return self._engine.heartbeat_once(dispatch=dispatch, adapter=adapter)

    def dispatch_notifications(
        self,
        events: list[dict[str, Any]],
        adapter: str = "dry-run",
    ) -> list[dict[str, Any]]:
        """Dispatch notification events."""
        return self._engine.dispatch_notifications(events, adapter=adapter)

    def suspend(self, reason: str = "manual") -> dict[str, Any]:
        """Suspend the engine."""
        return self._engine.suspend(reason=reason)

    def resume(self) -> dict[str, Any]:
        """Resume the engine."""
        return self._engine.resume()

    def create_backup(self, label: str = "manual") -> dict[str, Any]:
        """Create a labeled SQLite snapshot."""
        return self._engine.create_backup(label=label)

    def list_backups(self) -> list[dict[str, Any]]:
        """List SQLite snapshots, newest first."""
        return self._engine.list_backups()

    def restore_backup(self, backup_id: str) -> dict[str, Any]:
        """Restore a SQLite snapshot."""
        return self._engine.restore_backup(backup_id)

    def list_credentials(self) -> list[dict[str, Any]]:
        return self._engine.list_credentials()

    def set_credential(self, name: str, value: str) -> dict[str, Any]:
        return self._engine.set_credential(name, value)

    def delete_credential(self, name: str) -> dict[str, Any]:
        return self._engine.delete_credential(name)

    def rotate_credential_key(self, new_vault_key: str) -> dict[str, Any]:
        return self._engine.rotate_credential_key(new_vault_key)

    def list_tool_policies(self, agent_role: str | None = None) -> list[dict[str, Any]]:
        """List tool authorization policies."""
        return self._engine.list_tool_policies(agent_role=agent_role)

    def set_tool_policy(
        self,
        agent_role: str,
        tool_name: str,
        allowed: bool,
        reason: str = "",
        requires_approval: bool = False,
    ) -> dict[str, Any]:
        """Create or update a tool authorization policy."""
        return self._engine.set_tool_policy(
            agent_role,
            tool_name,
            allowed,
            reason=reason,
            requires_approval=requires_approval,
        )

    def authorize_tool(
        self,
        agent_role: str,
        tool_name: str,
        purpose: str = "",
    ) -> dict[str, Any]:
        """Check whether an agent may use a tool."""
        return self._engine.authorize_tool(agent_role, tool_name, purpose=purpose)

    def use_tool(
        self,
        agent_role: str,
        tool_name: str,
        purpose: str = "",
        arguments: dict[str, Any] | None = None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Authorize a tool use without attaching an execution handler."""
        return self._engine.use_tool(
            agent_role,
            tool_name,
            purpose=purpose,
            arguments=arguments,
            approval_id=approval_id,
        )

    def list_memories(
        self,
        agent_role: str,
        limit: int = 20,
        include_stale: bool = False,
        knowledge_type: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """List memories for an agent."""
        return self._engine.list_memories(
            agent_role,
            limit=limit,
            include_stale=include_stale,
            knowledge_type=knowledge_type,
            category=category,
        )

    def override(self, text: str) -> dict[str, Any]:
        """Request an override with a risk briefing."""
        return self._engine.process_override(text)

    def approvals(self) -> list[dict[str, Any]]:
        """List pending approval requests."""
        return self._engine.list_approvals()

    def approve(self, approval_id: str) -> dict[str, Any] | None:
        """Approve a pending request."""
        return self._engine.approve_request(approval_id)

    def reject(self, approval_id: str, reason: str = "") -> dict[str, Any] | None:
        """Reject a pending request."""
        return self._engine.reject_request(approval_id, reason=reason)
