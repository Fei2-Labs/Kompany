"""Retrospectives, episodes, agent memories.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations

from typing import Any

from kompany.core.run_context import current_run_id, run_scope
from kompany.state.models import Reflection, Retrospective



class LearningMixin:
    def run_retrospective(self, project_id: str) -> dict:
        """Deterministic CoS retrospective: persist one reflection per agent.

        Idempotent: if a retrospective already exists for the project,
        returns ``status="already_recorded"`` without writing or auditing.
        """
        if current_run_id() is None:
            with run_scope():
                return self._run_retrospective_inner(project_id)
        return self._run_retrospective_inner(project_id)

    def _run_retrospective_inner(self, project_id: str) -> dict:
        project = self.projects.get(project_id)
        if project is None:
            self.audit.record(
                "learning.retrospective_skipped",
                "Retrospective skipped: project not found",
                detail={"project_id": project_id},
            )
            return Retrospective(
                project_id=project_id,
                status="skipped_no_project",
            ).model_dump(mode="json")

        existing_rows = self.db.execute(
            """SELECT agent_role, content FROM agent_memories
               WHERE category = 'reflection' AND context = ?""",
            (f"project:{project_id}",),
        ).fetchall()
        if existing_rows:
            reflections = [
                Reflection(agent_role=r["agent_role"], content=r["content"])
                for r in existing_rows
            ]
            self.audit.record(
                "learning.retrospective_skipped",
                "Retrospective already recorded for project",
                detail={"project_id": project_id},
                project_id=project_id,
            )
            return Retrospective(
                project_id=project_id,
                status="already_recorded",
                summary=project.name,
                reflections=reflections,
            ).model_dump(mode="json")

        tasks = self.projects.list_tasks(project_id)
        completed = sum(1 for t in tasks if t.status.value == "completed")
        failed = sum(1 for t in tasks if t.status.value == "failed")

        agents = list(dict.fromkeys(project.assigned_agents)) or ["coo"]
        reflections: list[Reflection] = []
        for role in agents:
            agent_tasks = [t for t in tasks if t.assigned_agent == role]
            agent_failed = sum(1 for t in agent_tasks if t.status.value == "failed")
            content = (
                f"Project '{project.name}' completed with "
                f"{len(agent_tasks)} task(s) assigned to {role}, "
                f"{agent_failed} failed; {completed} completed and {failed} "
                f"failed across the project."
            )
            self.memory.remember(
                agent_role=role,
                content=content,
                category="reflection",
                knowledge_type="experiential",
                context=f"project:{project_id}",
            )
            reflections.append(Reflection(agent_role=role, content=content))

        self.audit.record(
            "learning.retrospective_completed",
            "CoS retrospective recorded",
            detail={
                "project_id": project_id,
                "tasks_completed": completed,
                "tasks_failed": failed,
                "agent_roles": agents,
            },
            project_id=project_id,
        )

        # Glossary drift scan (glossary-and-drift-detection task 05-19).
        # Runs *after* reflections land in agent_memories but *before*
        # episode materialization so the resulting health event + drift
        # rows are already on disk when ``Episodes.materialize`` reads
        # them. Wrapped in try/except: a drift-scan bug must never block
        # the canonical retrospective output.
        try:
            self._run_glossary_drift_scan(
                project_id=project_id,
                reflections=reflections,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.audit.record(
                "glossary.drift_scan_failed",
                "Glossary drift scan failed",
                detail={"project_id": project_id, "error": str(exc)},
                project_id=project_id,
            )

        # Materialize the structured episode record + enforce retention.
        # Wrapped in try/except so that a materialization bug never blocks
        # a retrospective from being written (reflections are the user-visible
        # output; episodes are the durable analysis substrate).
        try:
            episode_row = self.episodes.record_or_update(project_id)
            self.audit.record(
                "learning.episode_recorded",
                "Materialized project episode",
                detail={
                    "project_id": project_id,
                    "retention_tier": episode_row["retention_tier"],
                },
                project_id=project_id,
            )
            max_full = self._get_int_config(
                "episode_retention_full_count", default=50
            )
            trimmed = self.episodes.trim_to_retention_window(max_full)
            for entry in trimmed:
                self.audit.record(
                    "learning.episode_trimmed",
                    "Episode demoted to summary retention",
                    detail=entry,
                    project_id=entry["project_id"],
                )
        except Exception as exc:  # pragma: no cover - defensive
            self.audit.record(
                "learning.episode_failed",
                "Episode materialization failed",
                detail={"project_id": project_id, "error": str(exc)},
                project_id=project_id,
            )

        return Retrospective(
            project_id=project_id,
            status="recorded",
            summary=project.name,
            tasks_completed=completed,
            tasks_failed=failed,
            reflections=reflections,
        ).model_dump(mode="json")

    def list_episodes(
        self,
        retention_tier: str | None = None,
    ) -> list[dict[str, Any]]:
        """List materialized project episodes (no payload)."""
        rows = self.episodes.list(retention_tier=retention_tier)
        # Strip the heavy payload column from the list view; callers who
        # want the full payload should call ``get_episode``.
        return [
            {k: v for k, v in row.items() if k != "payload_json"}
            for row in rows
        ]

    def get_episode(self, project_id: str) -> dict[str, Any] | None:
        """Fetch one episode row including its ``payload_json``."""
        return self.episodes.get(project_id)

    def rebuild_episode(self, project_id: str) -> dict[str, Any]:
        """Force re-materialization of one project's episode payload.

        Use this after manually mutating source-table rows (e.g. backfilling
        a missing audit event) to refresh the cached payload. The operation
        is idempotent and re-applies retention trimming.
        """
        if current_run_id() is None:
            with run_scope():
                return self._rebuild_episode_inner(project_id)
        return self._rebuild_episode_inner(project_id)

    def _rebuild_episode_inner(self, project_id: str) -> dict[str, Any]:
        row = self.episodes.record_or_update(project_id)
        self.audit.record(
            "learning.episode_recorded",
            "Episode rebuilt on demand",
            detail={
                "project_id": project_id,
                "retention_tier": row["retention_tier"],
                "trigger": "rebuild",
            },
            project_id=project_id,
        )
        max_full = self._get_int_config("episode_retention_full_count", default=50)
        trimmed = self.episodes.trim_to_retention_window(max_full)
        for entry in trimmed:
            self.audit.record(
                "learning.episode_trimmed",
                "Episode demoted to summary retention",
                detail=entry,
                project_id=entry["project_id"],
            )
        return row

    def list_memories(
        self,
        agent_role: str,
        limit: int = 20,
        include_stale: bool = False,
        knowledge_type: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        """List memories for an agent, with stale/knowledge_type filters."""
        return self.memory.recall(
            agent_role=agent_role,
            limit=limit,
            category=category,
            include_stale=include_stale,
            knowledge_type=knowledge_type,
        )

