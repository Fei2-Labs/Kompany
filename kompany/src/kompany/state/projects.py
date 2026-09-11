"""Project management — tracks revenue and operational projects."""

from __future__ import annotations

import json

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import Project, ProjectStatus, Task, TaskStatus

# Legacy ``projects.type`` values from before the ProjectType enum was
# narrowed (pre-2026-06 schema). Coerced on read so old DBs / fresh clones
# of an existing DB don't ValidationError. See handoff 2026-06-15.
_LEGACY_PROJECT_TYPE_MAP = {
    "growth": "strategic",
    "dev": "operational",
}
_VALID_PROJECT_TYPES = {"revenue", "operational", "strategic"}


def _safe_json(raw, default):
    """Parse a JSON column defensively: one malformed row must never crash a
    list read. Returns ``default`` on missing/invalid JSON."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def _coerce_project_type(raw) -> str:
    """Map legacy/unknown ``type`` values onto the current ProjectType enum.
    Unknown values fall back to ``operational`` rather than crashing."""
    val = (raw or "").strip().lower()
    if val in _VALID_PROJECT_TYPES:
        return val
    return _LEGACY_PROJECT_TYPE_MAP.get(val, "operational")


_TASK_STATUS_VALUES = frozenset(m.value for m in TaskStatus)


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
            type=_coerce_project_type(row["type"]),
            status=row["status"],
            target_amount=row["target_amount"],
            funded_amount=row["funded_amount"],
            triggers_directive_id=row["triggers_directive_id"],
            plan=_safe_json(row["plan"], {}),
            assigned_agents=_safe_json(row["assigned_agents"], []),
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
                parent_task_id, delegation_id, run_id, budget_cap_usd, max_turns)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.id, task.project_id, task.title,
             task.status.value, task.assigned_agent, task.parent_task_id,
             task.delegation_id, rid, task.budget_cap_usd, task.max_turns),
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

    def get_task(self, task_id: str) -> Task | None:
        """Fetch one task by id."""
        row = self.db.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return self._row_to_task(row) if row else None

    def set_task_budget_cap(self, task_id: str, budget_cap_usd: float) -> None:
        """Set a task's AI-spend cap (founder-approved budget increase)."""
        self.db.execute(
            """UPDATE tasks SET budget_cap_usd = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (float(budget_cap_usd), task_id),
        )
        self.db.commit()

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
        reason: str | None = None,
    ) -> None:
        """Set a non-enum task status (e.g. ``stranded_in_progress``).

        The resilience foundation introduces task states that live outside
        :class:`TaskStatus` — they are valid transient values written by
        the watchdog only, never typed into agents. Kept narrow on purpose.
        """
        result_json = json.dumps(result) if result else None
        self.db.execute(
            """UPDATE tasks SET status = ?, result = COALESCE(?, result),
                   block_reason = COALESCE(?, block_reason),
                   updated_at = datetime('now')
               WHERE id = ?""",
            (status, result_json, reason, task_id),
        )
        self.db.commit()

    def requeue_task(self, task_id: str, reason: str, *, reset_retries: bool = False) -> Task | None:
        """Put a task back in the queue (``pending``) so the ticker runs it again.

        ``retry_count`` increments (or resets on a founder-initiated retry) and
        ``block_reason`` records why the previous run ended. Returns the task or
        ``None`` when it does not exist.
        """
        self.db.execute(
            """UPDATE tasks SET status = 'pending', block_reason = ?,
                   retry_count = CASE WHEN ? THEN 0 ELSE retry_count + 1 END,
                   completed_at = NULL, updated_at = datetime('now')
               WHERE id = ?""",
            (reason, 1 if reset_retries else 0, task_id),
        )
        self.db.commit()
        return self.get_task(task_id)

    def list_stale_stranded(self, stale_seconds: int) -> list[Task]:
        """Tasks the scanner already marked stranded and that nobody touched since.

        A live run that merely took long overwrites the transient status when it
        finishes; a row still stranded one full threshold later is truly dead.
        """
        if stale_seconds <= 0:
            return []
        rows = self.db.execute(
            """SELECT * FROM tasks
               WHERE status = 'stranded_in_progress'
                 AND updated_at <= datetime('now', ?)""",
            (f"-{int(stale_seconds)} seconds",),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def list_legacy_stranded(self) -> list[Task]:
        """Blocked rows with neither an outcome nor a reason: stranded by an
        older watchdog that had no requeue. Recovered once at boot."""
        rows = self.db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('blocked', 'stranded_in_progress')
                 AND result IS NULL AND block_reason IS NULL"""
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def set_task_harness_session(
        self, task_id: str, session_id: str, vehicle: str
    ) -> None:
        """Persist the vehicle session identity on a task (PRD D4).

        Written after every harness run so an engine restart (or a
        retry of a budget-capped task) can ``resume()`` the same session
        instead of starting from scratch.
        """
        self.db.execute(
            """UPDATE tasks SET harness_session_id = ?, harness_vehicle = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (session_id, vehicle, task_id),
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

    def list_active_tasks(self) -> list[Task]:
        """Return every ``active``/``in_progress`` task unconditionally.

        Unlike :meth:`list_stale_in_progress`, this ignores ``updated_at``
        entirely — it is used for one-shot startup reconciliation
        (``Watchdog.reconcile_on_startup``), where the fact that the
        process is only now booting proves every such row is orphaned
        from a previous process's crash/kill, regardless of how recently
        it was last touched.
        """
        rows = self.db.execute(
            "SELECT * FROM tasks WHERE status IN ('active', 'in_progress')"
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def _row_to_task(self, row) -> Task:
        keys = row.keys()
        status = row["status"]
        block_reason = row["block_reason"] if "block_reason" in keys else None
        if status not in _TASK_STATUS_VALUES:
            # Transient watchdog states (``stranded_in_progress``) are not
            # enum members; surface them as BLOCKED with the reason so every
            # reader keeps working and NEEDS YOU can explain the card.
            block_reason = block_reason or f"stranded ({status})"
            status = TaskStatus.BLOCKED.value
        return Task(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            status=status,
            retry_count=int(row["retry_count"] or 0) if "retry_count" in keys else 0,
            block_reason=block_reason,
            assigned_agent=row["assigned_agent"],
            result=json.loads(row["result"]) if row["result"] else None,
            parent_task_id=row["parent_task_id"],
            delegation_id=(
                row["delegation_id"]
                if "delegation_id" in row.keys()
                else None
            ),
            execution_run_id=(
                row["execution_run_id"]
                if "execution_run_id" in row.keys()
                else None
            ),
            budget_cap_usd=row["budget_cap_usd"],
            max_turns=row["max_turns"],
            harness_session_id=row["harness_session_id"],
            harness_vehicle=row["harness_vehicle"],
        )

    def set_task_execution_run(
        self,
        task_id: str,
        execution_run_id: str | None,
    ) -> None:
        self.db.execute(
            """UPDATE tasks
               SET execution_run_id = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (execution_run_id, task_id),
        )
        self.db.commit()
