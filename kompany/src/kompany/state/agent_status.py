"""Agent activity status persistence.

Each agent row carries two orthogonal axes:

* ``status`` — the lifecycle phase (idle / working / thinking / dispatching).
* ``activity_kind`` — the *kind* of work, derived from the role (see
  :mod:`kompany.state.activity`). Advisory: clients may use it or derive their own.

Plus the raw project context (``project_id`` / ``project_type``) so a client can
join to the project itself. Every ``set()`` also streams an ``agent.activity``
event carrying the full contract, for real-time consumers (dashboard + the
future sprite-world client).
"""

from __future__ import annotations

from kompany.state.activity import derive_activity_kind
from kompany.state.database import Database


class AgentStatusStore:
    """Stores current activity status for each agent."""

    def __init__(self, db: Database):
        self.db = db

    def set(
        self,
        agent_role: str,
        status: str,
        current_task: str | None = None,
        project_id: str | None = None,
        project_type: str | None = None,
    ) -> None:
        activity_kind = derive_activity_kind(agent_role, status, project_type)
        self.db.execute(
            """INSERT INTO agent_status
                   (agent_role, status, current_task, project_id, project_type,
                    activity_kind, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(agent_role) DO UPDATE SET
                   status = excluded.status,
                   current_task = excluded.current_task,
                   project_id = excluded.project_id,
                   project_type = excluded.project_type,
                   activity_kind = excluded.activity_kind,
                   updated_at = datetime('now')""",
            (agent_role, status, current_task, project_id, project_type, activity_kind),
        )
        self.db.commit()
        self._emit(agent_role, status, current_task, project_id, project_type, activity_kind)

    def get(self, agent_role: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM agent_status WHERE agent_role = ?",
            (agent_role,),
        ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM agent_status ORDER BY agent_role",
        ).fetchall()
        return [dict(row) for row in rows]

    def reset_all_working_to_idle(self) -> list[dict]:
        """One-shot startup reconciliation: flip every non-idle row to
        ``idle`` and clear ``current_task``.

        A process crash/kill leaves ``agent_status`` rows showing
        ``working``/``thinking``/``dispatching`` for agents that are not
        actually running anything in the *new* process — there is no
        heartbeat/liveness signal on this table (see module docstring),
        so nothing else corrects it until the agent happens to be
        dispatched again. Returns the rows that were changed, for audit
        logging by the caller.
        """
        rows = self.db.execute(
            "SELECT * FROM agent_status WHERE status != 'idle'",
        ).fetchall()
        changed = [dict(row) for row in rows]
        if changed:
            self.db.execute(
                """UPDATE agent_status SET status = 'idle', current_task = NULL,
                       updated_at = datetime('now')
                   WHERE status != 'idle'""",
            )
            self.db.commit()
        return changed

    @staticmethod
    def _emit(
        agent_role: str,
        status: str,
        current_task: str | None,
        project_id: str | None,
        project_type: str | None,
        activity_kind: str,
    ) -> None:
        """Stream the activity contract. Best-effort — a publish failure must
        never break a status write.

        ``run_id`` is read from the active :func:`current_run_id` scope so SSE
        clients can demux concurrent CEO-channel sessions (each
        ``process_directive`` runs in its own ``run_scope``). It is ``None``
        for status writes made outside any run (bootstrap, ad-hoc test sets).
        The field is purely additive — existing consumers that read
        ``agent_role`` / ``status`` / ``project_id`` are unaffected.
        """
        try:
            from kompany.core.event_hub import get_event_hub
            from kompany.core.run_context import current_run_id

            get_event_hub().publish(
                "agent.activity",
                {
                    "agent_role": agent_role,
                    "status": status,
                    "current_task": current_task,
                    "project_id": project_id,
                    "project_type": project_type,
                    "activity_kind": activity_kind,
                    "run_id": current_run_id(),
                },
            )
        except Exception:
            pass
