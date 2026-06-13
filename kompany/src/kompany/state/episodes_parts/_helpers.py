"""Pure helper functions for the episodes materializer.

These do not perform DB writes and carry no class state.
"""

from __future__ import annotations

import json
from typing import Any

from kompany.state.database import Database
from kompany.state.episode_payload import EpisodePayloadV1


def resolve_mission(db: Database, project_row: Any) -> str | None:
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
    row = db.execute(
        "SELECT value FROM company_config WHERE key = ?", ("mission",)
    ).fetchone()
    if row and row["value"]:
        return row["value"]
    return None


def stringify_decision_summary(result_payload: Any) -> str:
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


def build_summary(payload: EpisodePayloadV1) -> str:
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


def row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a ``project_episodes`` DB row to a plain dict."""
    return {
        "project_id": row["project_id"],
        "summary": row["summary"],
        "payload_json": row["payload_json"],
        "retention_tier": row["retention_tier"],
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
