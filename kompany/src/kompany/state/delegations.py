"""Durable CEO-owned delegation aggregates and child task links."""

from __future__ import annotations

import json

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import (
    Delegation,
    DelegationStatus,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects


class DelegationStore:
    def __init__(self, db: Database, projects: Projects):
        self.db = db
        self.projects = projects

    def create(self, delegation: Delegation) -> Delegation:
        run_id = delegation.parent_run_id or current_run_id()
        with self.db.conn:
            self.db.execute(
                """INSERT INTO delegations
                   (id, session_id, directive_id, project_id, parent_agent_id,
                    parent_run_id, status, context_packet, budget_cap_usd,
                    depth, max_depth, max_concurrency, result)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    delegation.id,
                    delegation.session_id,
                    delegation.directive_id,
                    delegation.project_id,
                    delegation.parent_agent_id,
                    run_id,
                    delegation.status.value,
                    json.dumps(delegation.context_packet),
                    delegation.budget_cap_usd,
                    delegation.depth,
                    delegation.max_depth,
                    delegation.max_concurrency,
                    json.dumps(delegation.result)
                    if delegation.result is not None
                    else None,
                ),
            )
            for child in delegation.children:
                self.db.execute(
                    """INSERT INTO tasks
                       (id, project_id, title, status, assigned_agent,
                        parent_task_id, delegation_id, run_id, budget_cap_usd,
                        max_turns)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        child.id,
                        child.project_id,
                        child.title,
                        child.status.value,
                        child.assigned_agent,
                        child.parent_task_id,
                        delegation.id,
                        run_id,
                        child.budget_cap_usd,
                        child.max_turns,
                    ),
                )
        created = self.get(delegation.id)
        if created is None:
            raise RuntimeError(f"delegation {delegation.id!r} was not persisted")
        return created

    def get(self, delegation_id: str) -> Delegation | None:
        row = self.db.execute(
            "SELECT * FROM delegations WHERE id = ?",
            (delegation_id,),
        ).fetchone()
        if row is None:
            return None
        child_rows = self.db.execute(
            "SELECT id FROM tasks WHERE delegation_id = ? ORDER BY created_at, rowid",
            (delegation_id,),
        ).fetchall()
        children = [
            task
            for child_row in child_rows
            if (task := self.projects.get_task(child_row["id"])) is not None
        ]
        return Delegation(
            id=row["id"],
            session_id=row["session_id"],
            directive_id=row["directive_id"],
            project_id=row["project_id"],
            parent_agent_id=row["parent_agent_id"],
            parent_run_id=row["parent_run_id"],
            status=row["status"],
            context_packet=json.loads(row["context_packet"]),
            budget_cap_usd=row["budget_cap_usd"],
            depth=row["depth"],
            max_depth=row["max_depth"],
            max_concurrency=row["max_concurrency"],
            children=children,
            result=json.loads(row["result"]) if row["result"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    def cancel(self, delegation_id: str) -> Delegation | None:
        existing = self.get(delegation_id)
        if existing is None:
            return None
        if existing.status in {
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
            DelegationStatus.CANCELLED,
        }:
            return existing
        with self.db.conn:
            self.db.execute(
                """UPDATE delegations
                   SET status = 'cancelled',
                       updated_at = datetime('now'),
                       completed_at = datetime('now')
                   WHERE id = ?""",
                (delegation_id,),
            )
            self.db.execute(
                """UPDATE tasks
                   SET status = 'cancelled',
                       completed_at = datetime('now')
                   WHERE delegation_id = ?
                     AND status IN ('pending', 'active', 'blocked')""",
                (delegation_id,),
            )
        return self.get(delegation_id)

    def complete_child(
        self,
        delegation_id: str,
        task_id: str,
        result: dict,
    ) -> tuple[Delegation, bool]:
        delegation = self.get(delegation_id)
        if delegation is None:
            raise ValueError(f"delegation {delegation_id!r} not found")
        if delegation.status in {
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
            DelegationStatus.CANCELLED,
        }:
            return delegation, False
        task = next(
            (child for child in delegation.children if child.id == task_id),
            None,
        )
        if task is None:
            raise ValueError(
                f"task {task_id!r} does not belong to delegation "
                f"{delegation_id!r}"
            )
        if task.status != TaskStatus.COMPLETED:
            self.projects.update_task_status(
                task_id,
                TaskStatus.COMPLETED,
                result=result,
            )
        return self.reconcile_child(task_id)

    def reconcile_child(self, task_id: str) -> tuple[Delegation, bool]:
        task = self.projects.get_task(task_id)
        if task is None:
            raise ValueError(f"task {task_id!r} not found")
        if not task.delegation_id:
            raise ValueError(f"task {task_id!r} is not delegated")
        delegation = self.get(task.delegation_id)
        if delegation is None:
            raise ValueError(
                f"delegation {task.delegation_id!r} not found"
            )
        if delegation.status in {
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
            DelegationStatus.CANCELLED,
            DelegationStatus.SYNTHESIZING,
        }:
            return delegation, False
        terminal = TaskStatus.terminal()
        all_terminal = all(
            child.status in terminal for child in delegation.children
        )
        next_status = "synthesizing" if all_terminal else "active"
        cursor = self.db.execute(
            """UPDATE delegations
               SET status = ?, updated_at = datetime('now')
               WHERE id = ? AND status IN ('queued', 'active')""",
            (next_status, delegation.id),
        )
        self.db.commit()
        refreshed = self.get(delegation.id)
        if refreshed is None:
            raise RuntimeError(f"delegation {delegation.id!r} disappeared")
        return refreshed, bool(all_terminal and cursor.rowcount)

    def finish(self, delegation_id: str, result: dict) -> Delegation:
        self.db.execute(
            """UPDATE delegations
               SET status = 'completed',
                   result = ?,
                   updated_at = datetime('now'),
                   completed_at = datetime('now')
               WHERE id = ?
                 AND status IN ('queued', 'active', 'synthesizing')""",
            (json.dumps(result), delegation_id),
        )
        self.db.commit()
        delegation = self.get(delegation_id)
        if delegation is None:
            raise ValueError(f"delegation {delegation_id!r} not found")
        return delegation

    def fail(self, delegation_id: str, error: str) -> Delegation:
        self.db.execute(
            """UPDATE delegations
               SET status = 'failed',
                   result = ?,
                   updated_at = datetime('now'),
                   completed_at = datetime('now')
               WHERE id = ? AND status != 'cancelled'""",
            (json.dumps({"error": error}), delegation_id),
        )
        self.db.commit()
        delegation = self.get(delegation_id)
        if delegation is None:
            raise ValueError(f"delegation {delegation_id!r} not found")
        return delegation


__all__ = ["DelegationStore"]
