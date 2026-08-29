"""Runtime safety controls for recurring soul cycles."""

from __future__ import annotations

from typing import Any

VALID_MODES = frozenset({"disabled", "dry_run", "native"})


def resolve_cycle_controls(
    settings: Any,
    role: str,
    cadence: dict[str, Any],
) -> dict[str, Any]:
    resolved = dict(cadence)
    overrides = getattr(settings, "soul_cycle_overrides", {}) or {}
    override = overrides.get(role, {}) if isinstance(overrides, dict) else {}
    if isinstance(override, dict):
        resolved.update(override)
    mode = str(resolved.get("scheduler_mode", "native")).strip().lower()
    resolved["scheduler_mode"] = mode if mode in VALID_MODES else "disabled"
    for key in (
        "max_comments_per_cycle",
        "max_original_posts_per_day",
        "max_external_proposals_per_cycle",
    ):
        if key in resolved:
            try:
                resolved[key] = max(0, int(resolved[key]))
            except (TypeError, ValueError):
                resolved[key] = 0
    return resolved


def enforce_cycle_proposal_gate(
    engine: Any,
    *,
    role: str,
    project_id: str | None,
    task_id: str | None,
    controls: dict[str, Any] | None,
) -> None:
    if not task_id:
        return
    if controls is None:
        return
    controls = resolve_cycle_controls(engine.settings, role, controls)
    mode = controls.get("scheduler_mode", "disabled")
    if mode != "native":
        reason = "scheduler_disabled" if mode == "disabled" else "scheduler_not_native"
        _audit_refusal(engine, reason, role, project_id, task_id)
        raise ValueError(reason)
    try:
        limit = max(
            0,
            int(controls.get("max_external_proposals_per_cycle", 1)),
        )
    except (TypeError, ValueError):
        limit = 0
    count = engine.db.execute(
        """SELECT COUNT(*) AS n FROM approval_requests
           WHERE action_type = 'tool_action'
             AND json_extract(payload, '$.task_id') = ?""",
        (task_id,),
    ).fetchone()["n"]
    if int(count) >= limit:
        _audit_refusal(
            engine,
            "proposal_budget_exhausted",
            role,
            project_id,
            task_id,
        )
        raise ValueError("proposal_budget_exhausted")


def _audit_refusal(
    engine: Any,
    reason: str,
    role: str,
    project_id: str | None,
    task_id: str,
) -> None:
    engine.audit.record(
        "soul_cycle.proposal_refused",
        f"Soul cycle proposal refused: {reason}",
        detail={"reason": reason, "role": role, "task_id": task_id},
        agent_role=role,
        project_id=project_id,
    )
