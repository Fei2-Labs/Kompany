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

Implementation note
-------------------
The bulk of the logic lives in :mod:`kompany.state.episodes_parts` (ADR-0003
≤500 lines per file). This module is the public surface; all symbols previously
importable from here remain importable from here.
"""

from __future__ import annotations

from typing import Any

from kompany.core.event_hub import get_event_hub
from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.episode_payload import EpisodePayloadV1, ProjectMeta

from kompany.state.episodes_parts._collectors import (
    collect_approval_events,
    collect_audit_events,
    collect_decisions_and_debates,
    collect_glossary_drift,
    collect_health_events,
    collect_ledger_summary,
    collect_reflections,
    collect_targets_bundle,
    collect_tasks,
)
from kompany.state.episodes_parts._helpers import (
    build_summary,
    resolve_mission,
    row_to_dict,
)


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
            mission=resolve_mission(self.db, project_row),
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

        tasks = collect_tasks(self.db, project_id)
        ledger_summary = collect_ledger_summary(self.db, project_id, tasks)
        decisions, debate_ids = collect_decisions_and_debates(
            self.db, project_id, project_row["triggers_directive_id"]
        )
        audit_events, run_ids = collect_audit_events(self.db, project_id)
        reflections = collect_reflections(self.db, project_id)
        health_events = collect_health_events(self.db, project_id)
        approval_events = collect_approval_events(self.db, project_id)
        targets_bundle = collect_targets_bundle(self.db)
        glossary_drift = collect_glossary_drift(health_events)

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
            targets=targets_bundle,
            glossary_drift=glossary_drift,
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_or_update(self, project_id: str) -> dict[str, Any]:
        """Materialize and persist (idempotent — keeps ``created_at``)."""
        payload = self.materialize(project_id)
        summary = build_summary(payload)
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
        try:
            get_event_hub().publish(
                "episode.recorded",
                {
                    "project_id": project_id,
                    "summary": summary,
                    "run_id": rid,
                },
            )
        except Exception:  # pragma: no cover — best-effort live feed
            pass
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
        return row_to_dict(row) if row else None

    def list(
        self,
        retention_tier: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if limit is not None and limit <= 0:
            return []

        if retention_tier is None:
            sql = "SELECT * FROM project_episodes ORDER BY updated_at DESC"
            params: tuple[Any, ...] = ()
        else:
            sql = (
                "SELECT * FROM project_episodes WHERE retention_tier = ? "
                "ORDER BY updated_at DESC"
            )
            params = (retention_tier,)

        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)

        rows = self.db.execute(sql, params).fetchall()
        return [row_to_dict(r) for r in rows]
