"""Project episode materialization for self-learning.

When a project is delivered, its raw data lives across six tables
(``projects``, ``tasks``, ``ledger``, ``decisions``, ``audit_log``,
``agent_memories``) plus the new ``debates`` table. This module is the
**single place** that flattens those rows into one
:class:`~kompany.state.episode_payload.EpisodePayloadV1` JSON document and
writes it into ``project_episodes``.

The materializer is intentionally a pure function: ``materialize(project_id)``
re-reads the source tables every time and never depends on the previous
episode row. That keeps ``rebuild`` trivially correct and lets us recover
from a corrupt write by simply re-running it.

Retention strategy
------------------
``trim_to_retention_window(N)`` keeps the most recent ``N`` episodes at
``retention_tier='full'`` and degrades older rows to ``'summary'``: the
detailed ``payload_json`` is cleared (set to ``NULL``) but the ``summary``
string remains. The original 6 source tables are untouched — re-running
``record_or_update`` on a trimmed project will re-materialize the full
payload from raw rows.
"""

from __future__ import annotations

import json
from typing import Any

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.approvals import ApprovalRequests
from kompany.state.episode_payload import (
    ApprovalComment,
    ApprovalEvent,
    AuditEvent,
    DecisionEntry,
    EpisodePayloadV1,
    HealthEvent,
    LedgerSummary,
    LifecycleEvent,
    ProjectMeta,
    ReflectionEntry,
    TaskEntry,
)
from kompany.state.health_events import HealthEvents


# Audit event types that are deliberately *included* in episode payloads.
# Everything else (chatty per-step events, internal traces) is filtered out
# to keep payloads under the "10–50 KB" target documented in the PRD.
_KEY_AUDIT_EVENT_TYPES = frozenset({
    "project.created",
    "project.completed",
    "project.failed",
    "directive.classified",
    "directive.routed",
    "directive.completed",
    "directive.failed",
    "governed_execution.released",
    "governed_execution.delivered",
    "learning.retrospective_completed",
    "learning.episode_recorded",
    "learning.episode_trimmed",
    "approval.approved",
    "approval.rejected",
    "debate.recorded",
})


class Episodes:
    """Service for the ``project_episodes`` table."""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Pure materialization
    # ------------------------------------------------------------------

    def materialize(self, project_id: str) -> EpisodePayloadV1:
        """Build a fresh :class:`EpisodePayloadV1` from raw source tables.

        Pure: makes no writes, depends only on the current DB rows.
        Raises ``LookupError`` if the project does not exist.
        """
        project_row = self.db.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project_row is None:
            raise LookupError(f"project not found: {project_id}")

        project_meta = ProjectMeta(
            id=project_row["id"],
            name=project_row["name"],
            mission=self._resolve_mission(project_row),
            target_funded=[
                float(project_row["target_amount"] or 0.0),
                float(project_row["funded_amount"] or 0.0),
            ],
            status=project_row["status"],
            created_at=project_row["created_at"],
            delivered_at=(
                project_row["updated_at"]
                if project_row["status"] in {"completed", "failed"}
                else None
            ),
        )

        tasks = self._collect_tasks(project_id)
        ledger_summary = self._collect_ledger_summary(project_id, tasks)
        decisions, debate_ids = self._collect_decisions_and_debates(
            project_id, project_row["triggers_directive_id"]
        )
        audit_events, run_ids = self._collect_audit_events(project_id)
        reflections = self._collect_reflections(project_id)
        health_events = self._collect_health_events(project_id)
        approval_events = self._collect_approval_events(project_id)

        return EpisodePayloadV1(
            project_meta=project_meta,
            tasks=tasks,
            ledger_summary=ledger_summary,
            decisions=decisions,
            debate_ids=debate_ids,
            audit_events=audit_events,
            reflections=reflections,
            run_ids=run_ids,
            health_events=health_events,
            approval_events=approval_events,
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_or_update(self, project_id: str) -> dict[str, Any]:
        """Materialize and persist (idempotent — keeps ``created_at``)."""
        payload = self.materialize(project_id)
        summary = self._build_summary(payload)
        payload_json = payload.model_dump_json()
        rid = current_run_id()

        # Idempotent upsert: keep the original created_at on rewrite, bump
        # updated_at and the latest run_id.
        self.db.execute(
            """INSERT INTO project_episodes
               (project_id, summary, payload_json, retention_tier,
                run_id, created_at, updated_at)
               VALUES (?, ?, ?, 'full', ?, datetime('now'), datetime('now'))
               ON CONFLICT(project_id) DO UPDATE SET
                 summary = excluded.summary,
                 payload_json = excluded.payload_json,
                 retention_tier = 'full',
                 run_id = excluded.run_id,
                 updated_at = datetime('now')""",
            (project_id, summary, payload_json, rid),
        )
        self.db.commit()

        row = self.get(project_id)
        # ``get`` always returns a row here because we just wrote it.
        assert row is not None
        return row

    def trim_to_retention_window(self, max_full_count: int) -> list[dict[str, Any]]:
        """Demote oldest ``full`` rows beyond ``max_full_count`` to ``summary``.

        Returns a list of ``{project_id, old_tier, new_tier}`` dicts so the
        caller (e.g. the engine) can emit one audit event per trimmed row.
        """
        if max_full_count < 0:
            raise ValueError("max_full_count must be >= 0")

        # Tie-break on rowid DESC so newer inserts win when ``updated_at``
        # collisions occur (SQLite's datetime('now') is second-resolution).
        rows = self.db.execute(
            """SELECT project_id FROM project_episodes
               WHERE retention_tier = 'full'
               ORDER BY updated_at DESC, rowid DESC"""
        ).fetchall()
        to_trim = [r["project_id"] for r in rows[max_full_count:]]

        trimmed: list[dict[str, Any]] = []
        for pid in to_trim:
            self.db.execute(
                """UPDATE project_episodes
                   SET retention_tier = 'summary',
                       payload_json = NULL,
                       updated_at = datetime('now')
                   WHERE project_id = ?""",
                (pid,),
            )
            trimmed.append({
                "project_id": pid,
                "old_tier": "full",
                "new_tier": "summary",
            })
        if trimmed:
            self.db.commit()
        return trimmed

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, project_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM project_episodes WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, retention_tier: str | None = None) -> list[dict[str, Any]]:
        if retention_tier is None:
            rows = self.db.execute(
                "SELECT * FROM project_episodes ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM project_episodes WHERE retention_tier = ? "
                "ORDER BY updated_at DESC",
                (retention_tier,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_mission(self, project_row) -> str | None:
        """Return the mission string to embed in ``ProjectMeta.mission``.

        Priority:

        1. ``project.plan['mission']`` — set when a project is created
           from a template (``Templates.apply``). This is per-project so
           a future redesign that swaps templates mid-flight keeps each
           project pinned to the mission it was born under.
        2. ``company_config['mission']`` — global fallback for projects
           that pre-date templates.
        3. ``None`` — no mission has been set.
        """
        plan_raw = project_row["plan"] if "plan" in project_row.keys() else None
        if plan_raw:
            try:
                plan_obj = json.loads(plan_raw)
            except (TypeError, ValueError):
                plan_obj = None
            if isinstance(plan_obj, dict):
                mission = plan_obj.get("mission")
                if isinstance(mission, str) and mission.strip():
                    return mission
        row = self.db.execute(
            "SELECT value FROM company_config WHERE key = ?", ("mission",)
        ).fetchone()
        if row and row["value"]:
            return row["value"]
        return None

    def _collect_tasks(self, project_id: str) -> list[TaskEntry]:
        rows = self.db.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        tasks: list[TaskEntry] = []
        for row in rows:
            lifecycle: list[LifecycleEvent] = []
            if row["completed_at"]:
                lifecycle.append(
                    LifecycleEvent(
                        at=row["completed_at"],
                        state=row["status"],
                        reason=None,
                    )
                )
            tasks.append(
                TaskEntry(
                    id=row["id"],
                    title=row["title"],
                    assigned_agent=row["assigned_agent"],
                    status=row["status"],
                    result=row["result"],
                    run_id=row["run_id"],
                    lifecycle_events=lifecycle,
                )
            )
        return tasks

    def _collect_ledger_summary(
        self,
        project_id: str,
        tasks: list[TaskEntry],
    ) -> LedgerSummary:
        rows = self.db.execute(
            "SELECT category, amount FROM ledger WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        total_income = 0.0
        total_expense = 0.0
        ai_cost = 0.0
        by_category: dict[str, float] = {}
        for row in rows:
            amount = float(row["amount"] or 0.0)
            category = row["category"]
            by_category[category] = by_category.get(category, 0.0) + amount
            if amount > 0:
                total_income += amount
            else:
                total_expense += amount
            if category == "ai_cost":
                ai_cost += amount

        # AI cost is reported as a positive magnitude (it's stored negative
        # because it's an expense, but the summary should be human-readable).
        return LedgerSummary(
            total_income=total_income,
            total_expense=total_expense,
            ai_cost=abs(ai_cost),
            by_category=by_category,
            by_agent={},
        )

    def _collect_decisions_and_debates(
        self,
        project_id: str,
        triggering_directive_id: str | None,
    ) -> tuple[list[DecisionEntry], list[str]]:
        """Pull decisions that mention this project and aggregate debate ids.

        Decisions don't carry ``project_id`` directly (their ``result`` JSON
        does, when relevant), so we use two strategies:

        1. Decisions whose ``directive_id`` matches the project's triggering
           directive — exact link.
        2. Decisions whose ``result`` JSON mentions the project id — useful
           when a single strategic debate later spawned this project.

        Debate ids are pulled from ``debates.project_id`` *and* from any
        ``result.debate_id`` field surfaced in the matched decisions.
        """
        candidate_decisions: dict[str, Any] = {}

        if triggering_directive_id:
            for row in self.db.execute(
                "SELECT * FROM decisions WHERE directive_id = ? "
                "ORDER BY timestamp",
                (triggering_directive_id,),
            ).fetchall():
                candidate_decisions[row["id"]] = row

        # Pick up decisions whose result mentions the project id. The
        # ``result`` column is JSON-encoded text; LIKE is sufficient and
        # cheap for the volumes we care about.
        for row in self.db.execute(
            "SELECT * FROM decisions WHERE result LIKE ? ORDER BY timestamp",
            (f"%{project_id}%",),
        ).fetchall():
            candidate_decisions.setdefault(row["id"], row)

        decisions: list[DecisionEntry] = []
        debate_ids: set[str] = set()
        for row in candidate_decisions.values():
            try:
                result_payload = json.loads(row["result"]) if row["result"] else {}
            except (TypeError, ValueError):
                result_payload = {}
            try:
                agents = json.loads(row["agents_involved"])
                if not isinstance(agents, list):
                    agents = []
            except (TypeError, ValueError):
                agents = []
            summary = self._stringify_decision_summary(result_payload)
            decisions.append(
                DecisionEntry(
                    id=row["id"],
                    directive_id=row["directive_id"],
                    run_id=row["run_id"],
                    summary=summary,
                    agents_involved=agents,
                )
            )
            ref = result_payload.get("debate_id") if isinstance(result_payload, dict) else None
            if isinstance(ref, str) and ref:
                debate_ids.add(ref)

        # Debates linked directly to the project.
        for row in self.db.execute(
            "SELECT id FROM debates WHERE project_id = ?",
            (project_id,),
        ).fetchall():
            debate_ids.add(row["id"])

        return decisions, sorted(debate_ids)

    def _stringify_decision_summary(self, result_payload: Any) -> str:
        """Build a short text summary from a decision's ``result`` payload."""
        if isinstance(result_payload, dict):
            status = result_payload.get("status") or ""
            message = result_payload.get("message") or ""
            if status and message:
                return f"{status}: {message[:300]}"
            if message:
                return str(message)[:300]
            if status:
                return str(status)
            return json.dumps(result_payload)[:300]
        return str(result_payload)[:300]

    def _collect_audit_events(
        self,
        project_id: str,
    ) -> tuple[list[AuditEvent], list[str]]:
        rows = self.db.execute(
            "SELECT * FROM audit_log WHERE project_id = ? "
            "ORDER BY id",
            (project_id,),
        ).fetchall()
        events: list[AuditEvent] = []
        run_ids: list[str] = []
        seen_run_ids: set[str] = set()
        for row in rows:
            if row["run_id"] and row["run_id"] not in seen_run_ids:
                seen_run_ids.add(row["run_id"])
                run_ids.append(row["run_id"])
            if row["event_type"] not in _KEY_AUDIT_EVENT_TYPES:
                continue
            detail: dict[str, Any] = {}
            if row["detail"]:
                try:
                    parsed = json.loads(row["detail"])
                    if isinstance(parsed, dict):
                        detail = parsed
                    else:
                        detail = {"value": parsed}
                except (TypeError, ValueError):
                    detail = {"raw": row["detail"]}
            events.append(
                AuditEvent(
                    at=row["timestamp"],
                    type=row["event_type"],
                    run_id=row["run_id"],
                    detail=detail,
                )
            )
        return events, run_ids

    def _collect_health_events(self, project_id: str) -> list[HealthEvent]:
        """Pull resilience-watchdog events tied to this project.

        Built by ``05-18-resilience-foundation``. ``id`` / ``status`` /
        ``project_id`` / ``resolved_by`` / ``resolved_at`` / ``snoozed_until``
        are propagated into the payload so distillation can later learn
        from player resolutions, not just from raw warnings.
        """
        store = HealthEvents(self.db)
        rows = store.list_for_project(project_id)
        events: list[HealthEvent] = []
        for row in rows:
            detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
            events.append(
                HealthEvent(
                    at=row["created_at"],
                    run_id=row.get("run_id"),
                    kind=row["kind"],
                    task_id=row.get("task_id"),
                    detail=detail,
                    id=row.get("id"),
                    project_id=row.get("project_id") or project_id,
                    status=row.get("status") or "open",
                    resolved_by=row.get("resolved_by"),
                    resolved_at=row.get("resolved_at"),
                    snoozed_until=row.get("snoozed_until"),
                )
            )
        return events

    def _collect_approval_events(self, project_id: str) -> list[ApprovalEvent]:
        """Materialize approvals + comments tied to this project.

        Built by ``05-18-approval-thread-and-rpg``. Each ``ApprovalEvent``
        carries the approval's outcome (``status``), all comments in the
        thread, and ``decided_at`` so distillation can later pattern-match
        player decision speed + counter-proposal frequency.
        """
        store = ApprovalRequests(self.db)
        requests = store.list_for_project(project_id)
        # Pull ``run_id`` from the DB row separately (the ApprovalRequest
        # model omits it for backward compat) so the episode payload can
        # group approvals by run alongside audit_events / health_events.
        rid_rows = self.db.execute(
            "SELECT id, run_id FROM approval_requests WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        rid_lookup = {r["id"]: r["run_id"] for r in rid_rows}

        events: list[ApprovalEvent] = []
        for request in requests:
            comments = store.list_comments(request.id)
            comment_models: list[ApprovalComment] = []
            for c in comments:
                # ``by`` is rendered as ``"<by_type>:<by_id>"`` when an id
                # is present (e.g. ``"agent:cfo"``) so distillation has a
                # single string key without re-joining two columns.
                by = (
                    f"{c.by_type}:{c.by_id}"
                    if c.by_id
                    else c.by_type
                )
                created_str = (
                    c.created_at.isoformat()
                    if hasattr(c.created_at, "isoformat")
                    else str(c.created_at)
                )
                comment_models.append(
                    ApprovalComment(
                        by=by,
                        at=created_str,
                        text=c.body,
                    )
                )
            decided_at = (
                request.resolved_at.isoformat()
                if request.resolved_at and hasattr(request.resolved_at, "isoformat")
                else (str(request.resolved_at) if request.resolved_at else None)
            )
            events.append(
                ApprovalEvent(
                    id=request.id,
                    run_id=rid_lookup.get(request.id),
                    kind=request.action_type,
                    outcome=request.status.value,
                    comments=comment_models,
                    decided_at=decided_at,
                )
            )
        return events

    def _collect_reflections(self, project_id: str) -> list[ReflectionEntry]:
        rows = self.db.execute(
            """SELECT agent_role, category, content FROM agent_memories
               WHERE context = ? AND category = 'reflection'
               ORDER BY id""",
            (f"project:{project_id}",),
        ).fetchall()
        return [
            ReflectionEntry(
                agent_role=row["agent_role"],
                category=row["category"],
                content=row["content"],
            )
            for row in rows
        ]

    def _build_summary(self, payload: EpisodePayloadV1) -> str:
        """One-line human summary kept even after a row is trimmed."""
        completed = sum(1 for t in payload.tasks if t.status == "completed")
        failed = sum(1 for t in payload.tasks if t.status == "failed")
        return (
            f"{payload.project_meta.name} "
            f"({payload.project_meta.status}): "
            f"{completed} completed, {failed} failed, "
            f"{len(payload.decisions)} decision(s), "
            f"{len(payload.debate_ids)} debate(s)"
        )

    def _row_to_dict(self, row) -> dict[str, Any]:
        return {
            "project_id": row["project_id"],
            "summary": row["summary"],
            "payload_json": row["payload_json"],
            "retention_tier": row["retention_tier"],
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
