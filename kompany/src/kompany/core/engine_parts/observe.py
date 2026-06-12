"""Observability snapshot, RPG office view, run tracing.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations


from kompany.state.models import ObservabilitySnapshot, RPGCharacter, RPGOfficeRoom



class ObservabilityMixin:
    def observability_snapshot(self) -> dict:
        """Return an LLM-free operational snapshot for dashboards and RPG views."""
        cfo = self.registry.get("cfo")
        finance = cfo.get_summary()
        runtime = self.get_runtime_state()
        approvals = self.list_approvals()
        active_projects = self.projects.list_active()
        all_agents = self._observability_agents()
        tool_policies = self.list_tool_policies()
        notifications = self.heartbeat_once()["notifications"]

        project_rows = []
        task_totals = {"pending": 0, "active": 0, "completed": 0, "failed": 0}
        for project in active_projects:
            tasks = self.projects.list_tasks(project.id)
            counts = {"pending": 0, "active": 0, "completed": 0, "failed": 0}
            for task in tasks:
                status = task.status.value if hasattr(task.status, "value") else task.status
                counts[status] = counts.get(status, 0) + 1
                task_totals[status] = task_totals.get(status, 0) + 1
            project_rows.append({
                "id": project.id,
                "name": project.name,
                "type": project.type.value,
                "status": project.status.value,
                "target_amount": project.target_amount,
                "funded_amount": project.funded_amount,
                "assigned_agents": project.assigned_agents,
                "tasks": counts,
            })

        blocked = []
        if runtime["state"] == "suspended":
            blocked.append({"kind": "runtime", "summary": runtime.get("reason") or "suspended"})
        for approval in approvals:
            blocked.append({
                "kind": "approval",
                "id": approval["id"],
                "summary": approval["summary"],
            })

        office = self._rpg_office(all_agents, project_rows, blocked)
        snapshot = ObservabilitySnapshot(
            company={
                "name": self.settings.company_name,
                "goal": self.settings.company_goal,
                "stage": self.settings.company_stage,
                "time_horizon": self.settings.company_time_horizon,
                "exclusions": self.settings.company_exclusions,
            },
            runtime=runtime,
            finance={
                "balance": finance["balance"],
                "total_income": finance["total_income"],
                "total_expenses": finance["total_expenses"],
                "total_ai_costs": abs(finance["total_ai_costs"]),
            },
            approvals={
                "pending": len(approvals),
                "items": approvals,
                "blockers": blocked,
            },
            projects={
                "active": len(active_projects),
                "items": project_rows,
                "task_totals": task_totals,
            },
            agents={
                "total": len(all_agents),
                "active": sum(1 for a in all_agents if a["status"] != "idle"),
                "items": all_agents,
            },
            tools={
                "policies": len(tool_policies),
                "allowed": sum(1 for p in tool_policies if p["allowed"]),
                "denied": sum(1 for p in tool_policies if not p["allowed"]),
            },
            notifications=notifications,
            office=office,
            # Daemon tick loop visibility (06-12 D5): recent autonomous
            # ticks join the existing observability operation.
            recent_ticks=self.daemon_ticks.recent(10),
        ).model_dump(mode="json")
        self.audit.record(
            "observability.snapshot",
            "Generated observability snapshot",
            detail={
                "runtime_state": runtime["state"],
                "pending_approvals": len(approvals),
                "active_projects": len(active_projects),
                "active_agents": snapshot["agents"]["active"],
            },
        )
        return snapshot

    def _observability_agents(self) -> list[dict]:
        roles = [
            "ceo", "cfo", "cto", "cpo", "cro", "cmo", "coo", "cos",
            "ciso", "csa", "cv", "analyst", "builder", "procurement",
            "researcher", "writer",
        ]
        current = {row["agent_role"]: row for row in self.agent_status.list_all()}
        agents = []
        for role in roles:
            row = current.get(role, {})
            agents.append({
                "role": role,
                "status": row.get("status", "idle"),
                "current_task": row.get("current_task") or "",
                "updated_at": row.get("updated_at"),
            })
        return agents

    def _rpg_office(
        self,
        agents: list[dict],
        projects: list[dict],
        blockers: list[dict],
    ) -> dict:
        room_map = {
            "executive_suite": {
                "purpose": "Strategy, final direction, and governance.",
                "roles": {"ceo", "cos", "cfo"},
            },
            "growth_floor": {
                "purpose": "Revenue, product, marketing, and customer work.",
                "roles": {"cro", "cpo", "cmo", "cv"},
            },
            "operations_room": {
                "purpose": "Execution, delivery, and project coordination.",
                "roles": {"coo", "analyst", "writer", "researcher", "builder", "procurement"},
            },
            "security_lab": {
                "purpose": "Security, compliance, and tool authorization.",
                "roles": {"cto", "ciso", "csa"},
            },
        }
        rooms = []
        for name, spec in room_map.items():
            characters = [
                RPGCharacter(
                    role=agent["role"],
                    room=name,
                    status=agent["status"],
                    current_task=agent["current_task"],
                    updated_at=agent["updated_at"],
                )
                for agent in agents
                if agent["role"] in spec["roles"]
            ]
            rooms.append(RPGOfficeRoom(
                name=name,
                purpose=spec["purpose"],
                characters=characters,
            ).model_dump(mode="json"))
        return {
            "theme": "virtual_company_floor",
            "rooms": rooms,
            "active_projects": [p["name"] for p in projects],
            "blockers": blockers,
        }

    def trace_run(self, run_id: str) -> dict:
        """Return all state writes tagged with ``run_id``, time-ordered.

        Pulls from audit_log, decisions, ledger, agent_memories, tasks,
        and approval_requests. Each record carries a ``kind`` so callers
        can format mixed streams without re-introspecting columns.
        """
        events: list[dict] = []

        for row in self.db.execute(
            """SELECT timestamp, event_type, agent_role, action, detail,
                      directive_id, project_id, run_id
               FROM audit_log WHERE run_id = ? ORDER BY id""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "audit",
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "agent_role": row["agent_role"],
                "action": row["action"],
                "detail": row["detail"],
                "directive_id": row["directive_id"],
                "project_id": row["project_id"],
            })

        for row in self.db.execute(
            """SELECT timestamp, id, directive_id, directive_type,
                      result, agents_involved, total_ai_cost,
                      duration_seconds
               FROM decisions WHERE run_id = ? ORDER BY timestamp""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "decision",
                "timestamp": row["timestamp"],
                "id": row["id"],
                "directive_id": row["directive_id"],
                "directive_type": row["directive_type"],
                "result": row["result"],
                "agents_involved": row["agents_involved"],
                "total_ai_cost": row["total_ai_cost"],
                "duration_seconds": row["duration_seconds"],
            })

        for row in self.db.execute(
            """SELECT timestamp, amount, balance_after, description,
                      category, directive_id, project_id
               FROM ledger WHERE run_id = ? ORDER BY id""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "ledger",
                "timestamp": row["timestamp"],
                "amount": row["amount"],
                "balance_after": row["balance_after"],
                "description": row["description"],
                "category": row["category"],
                "directive_id": row["directive_id"],
                "project_id": row["project_id"],
            })

        for row in self.db.execute(
            """SELECT created_at, agent_role, category, knowledge_type,
                      content, context, directive_id
               FROM agent_memories WHERE run_id = ? ORDER BY id""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "memory",
                "timestamp": row["created_at"],
                "agent_role": row["agent_role"],
                "category": row["category"],
                "knowledge_type": row["knowledge_type"],
                "content": row["content"],
                "context": row["context"],
                "directive_id": row["directive_id"],
            })

        for row in self.db.execute(
            """SELECT created_at, id, project_id, title, status,
                      assigned_agent
               FROM tasks WHERE run_id = ? ORDER BY created_at""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "task",
                "timestamp": row["created_at"],
                "id": row["id"],
                "project_id": row["project_id"],
                "title": row["title"],
                "status": row["status"],
                "assigned_agent": row["assigned_agent"],
            })

        for row in self.db.execute(
            """SELECT created_at, id, status, action_type, summary,
                      directive_id, project_id, requested_by, resolved_by
               FROM approval_requests WHERE run_id = ? ORDER BY created_at""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "approval",
                "timestamp": row["created_at"],
                "id": row["id"],
                "status": row["status"],
                "action_type": row["action_type"],
                "summary": row["summary"],
                "directive_id": row["directive_id"],
                "project_id": row["project_id"],
                "requested_by": row["requested_by"],
                "resolved_by": row["resolved_by"],
            })

        events.sort(key=lambda e: (e.get("timestamp") or "", e.get("kind", "")))
        return {
            "run_id": run_id,
            "event_count": len(events),
            "events": events,
        }

