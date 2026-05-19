"""Project management — tracks revenue and operational projects."""

from __future__ import annotations

import json

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import Project, ProjectStatus, Task, TaskStatus


class Projects:
    """Project store backed by SQLite."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, project: Project) -> Project:
        self.db.execute(
            """INSERT INTO projects
               (id, name, type, status, target_amount, funded_amount,
                triggers_directive_id, plan, assigned_agents)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project.id,
                project.name,
                project.type.value,
                project.status.value,
                project.target_amount,
                project.funded_amount,
                project.triggers_directive_id,
                json.dumps(project.plan),
                json.dumps(project.assigned_agents),
            ),
        )
        self.db.commit()
        return project

    def list_active(self) -> list[Project]:
        rows = self.db.execute(
            "SELECT * FROM projects WHERE status = 'active' ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_project(r) for r in rows]

    def get(self, project_id: str) -> Project | None:
        row = self.db.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return self._row_to_project(row) if row else None

    def count_active(self) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) as c FROM projects WHERE status = 'active'"
        ).fetchone()
        return int(row["c"])

    def _row_to_project(self, row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            status=row["status"],
            target_amount=row["target_amount"],
            funded_amount=row["funded_amount"],
            triggers_directive_id=row["triggers_directive_id"],
            plan=json.loads(row["plan"]) if row["plan"] else {},
            assigned_agents=json.loads(row["assigned_agents"]),
        )

    def update_status(self, project_id: str, status: ProjectStatus) -> None:
        """Update a project's status."""
        self.db.execute(
            "UPDATE projects SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status.value, project_id),
        )
        self.db.commit()

    def add_funding(self, project_id: str, amount: float) -> Project | None:
        """Add funding to a project. Returns updated project or None."""
        project = self.get(project_id)
        if not project:
            return None
        new_funded = project.funded_amount + amount
        self.db.execute(
            "UPDATE projects SET funded_amount = ?, updated_at = datetime('now') WHERE id = ?",
            (new_funded, project_id),
        )
        self.db.commit()
        return self.get(project_id)

    def is_fully_funded(self, project_id: str) -> bool:
        """Check if a project has reached its target amount."""
        project = self.get(project_id)
        if not project or not project.target_amount:
            return False
        return project.funded_amount >= project.target_amount

    def list_all(self) -> list[Project]:
        """List all projects regardless of status."""
        rows = self.db.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_project(r) for r in rows]

    # --- Task management ---

    def create_task(self, task: Task, run_id: str | None = None) -> Task:
        """Create a task within a project."""
        rid = run_id if run_id is not None else current_run_id()
        self.db.execute(
            """INSERT INTO tasks
               (id, project_id, title, status, assigned_agent,
                parent_task_id, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task.id, task.project_id, task.title,
             task.status.value, task.assigned_agent, task.parent_task_id, rid),
        )
        self.db.commit()
        return task

    def list_tasks(self, project_id: str) -> list[Task]:
        """List all tasks for a project."""
        rows = self.db.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def update_task_status(
        self, task_id: str, status: TaskStatus, result: dict | None = None
    ) -> None:
        """Update a task's status and optionally its result.

        Also bumps ``updated_at`` so the resilience watchdog scanner can
        distinguish fresh activity from a forgotten ``in_progress`` row.
        """
        result_json = json.dumps(result) if result else None
        status_value = status.value if isinstance(status, TaskStatus) else str(status)
        self.db.execute(
            """UPDATE tasks SET status = ?, result = ?,
                updated_at = datetime('now'),
                completed_at = CASE WHEN ? = 'completed' THEN datetime('now') ELSE NULL END
                WHERE id = ?""",
            (status_value, result_json, status_value, task_id),
        )
        self.db.commit()

    def update_task_status_raw(
        self,
        task_id: str,
        status: str,
        result: dict | None = None,
    ) -> None:
        """Set a non-enum task status (e.g. ``stranded_in_progress``).

        The resilience foundation introduces task states that live outside
        :class:`TaskStatus` — they are valid transient values written by
        the watchdog only, never typed into agents. Kept narrow on purpose.
        """
        result_json = json.dumps(result) if result else None
        self.db.execute(
            """UPDATE tasks SET status = ?, result = COALESCE(?, result),
                   updated_at = datetime('now')
               WHERE id = ?""",
            (status, result_json, task_id),
        )
        self.db.commit()

    def touch_task(self, task_id: str) -> None:
        """Bump a task's ``updated_at`` without changing other fields."""
        self.db.execute(
            "UPDATE tasks SET updated_at = datetime('now') WHERE id = ?",
            (task_id,),
        )
        self.db.commit()

    def list_stale_in_progress(self, stale_seconds: int) -> list[Task]:
        """Return ``in_progress`` tasks whose ``updated_at`` is older than ``stale_seconds``.

        Used by the resilience scanner. ``stale_seconds`` must be positive;
        zero would match every row which is never useful.
        """
        if stale_seconds <= 0:
            return []
        # ``active`` is the in-flight state in this codebase
        # (``TaskStatus.ACTIVE``). ``in_progress`` is accepted too because
        # the PRD uses the paperclip-style vocabulary; future migrations to
        # that name should just keep working without a code change here.
        rows = self.db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('active', 'in_progress')
                 AND updated_at IS NOT NULL
                 AND updated_at <= datetime('now', ?)""",
            (f"-{int(stale_seconds)} seconds",),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def _row_to_task(self, row) -> Task:
        return Task(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            status=row["status"],
            assigned_agent=row["assigned_agent"],
            result=json.loads(row["result"]) if row["result"] else None,
            parent_task_id=row["parent_task_id"],
        )
