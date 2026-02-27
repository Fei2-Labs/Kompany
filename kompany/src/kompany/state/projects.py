"""Project management — tracks revenue and operational projects."""

from __future__ import annotations

import json

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

    def create_task(self, task: Task) -> Task:
        """Create a task within a project."""
        self.db.execute(
            """INSERT INTO tasks
               (id, project_id, title, status, assigned_agent, parent_task_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task.id, task.project_id, task.title,
             task.status.value, task.assigned_agent, task.parent_task_id),
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
        """Update a task's status and optionally its result."""
        completed = "datetime('now')" if status == TaskStatus.COMPLETED else "NULL"
        result_json = json.dumps(result) if result else None
        self.db.execute(
            f"""UPDATE tasks SET status = ?, result = ?,
                completed_at = CASE WHEN ? = 'completed' THEN datetime('now') ELSE NULL END
                WHERE id = ?""",
            (status.value, result_json, status.value, task_id),
        )
        self.db.commit()

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
