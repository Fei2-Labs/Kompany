"""Autopilot learning steps (09-26-autopilot-reports).

Two ticker steps that close the "someone has to start it" gap in the
self-learning loop:

``distill_tick``
    Runs cross-episode distillation on its own. Checked once per UTC
    day; runs when at least ``distill_min_new_episodes`` episodes changed
    since the last run, or when any changed and the gap since the last
    run exceeds ``distill_max_gap_days``. Never more than the 50-episode
    cap (newest first).

``evolution_tick``
    Creates artifact-evolution proposals (soul YAML, artifact lane —
    full-auto apply with doctor-gated revert and the existing daily cap)
    from two signals, checked once per UTC day:

    * one agent role failed >= ``auto_evolution_failure_threshold`` tasks
      in the last 7 days → evolve that role's soul;
    * one health-event kind opened >= ``auto_evolution_health_threshold``
      times in the last 7 days → evolve the Chief of Staff soul with the
      recurrence as instruction.

    Each cause yields at most one proposal per 7 days (``company_config``
    key per cause). Code changes are NOT proposed here — the code lane is
    approval-gated by the CONSTITUTION and stays human-initiated.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from kompany.state.ui_preferences import _read_config, _write_config

log = logging.getLogger(__name__)

DISTILL_CHECK_KEY = "autopilot.distill.last_check"
DISTILL_RUN_KEY = "autopilot.distill.last_run"
EVOLUTION_CHECK_KEY = "autopilot.evolution.last_check"
EVOLUTION_CAUSE_PREFIX = "autopilot.evolution.cause."
DEDUPE_DAYS = 7
WINDOW_DAYS = 7
DISTILL_CAP = 50
HEALTH_TARGET_ROLE = "cos"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _today() -> str:
    return _utcnow().date().isoformat()


def _sqlite_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _checked_today(db: Any, key: str) -> bool:
    return _read_config(db, key) == _today()


def _mark_checked(db: Any, key: str) -> None:
    _write_config(db, key, _today())
    db.commit()


# ---------------------------------------------------------------------------
# Distillation
# ---------------------------------------------------------------------------


def distill_tick(engine: Any) -> list[str]:
    settings = engine.settings
    if not bool(getattr(settings, "distill_auto_enabled", True)):
        return []
    db = engine.db
    if _checked_today(db, DISTILL_CHECK_KEY):
        return []
    _mark_checked(db, DISTILL_CHECK_KEY)
    last_run = _read_config(db, DISTILL_RUN_KEY)
    since_ts = last_run.replace("T", " ")[:19] if last_run else "1970-01-01 00:00:00"
    rows = db.execute(
        "SELECT project_id FROM project_episodes "
        "WHERE replace(updated_at, 'T', ' ') > ? "
        "ORDER BY updated_at DESC LIMIT ?",
        (since_ts, DISTILL_CAP),
    ).fetchall()
    ids = [r["project_id"] for r in rows]
    if not ids:
        return []
    min_new = int(getattr(settings, "distill_min_new_episodes", 10))
    gap_days = int(getattr(settings, "distill_max_gap_days", 7))
    gap_exceeded = last_run is None or (
        _utcnow() - datetime.fromisoformat(last_run).replace(tzinfo=UTC)
    ) >= timedelta(days=gap_days)
    if len(ids) < min_new and not gap_exceeded:
        return []
    try:
        result = engine.distill(episode_ids=ids)
    except Exception as exc:  # noqa: BLE001 — recorded, never raised into the tick
        log.exception("autopilot distillation failed")
        engine.audit.record(
            "autopilot.distill_failed",
            f"Automatic distillation failed: {type(exc).__name__}",
            detail={"episodes": len(ids), "error": str(exc)},
        )
        return ["autopilot_distill:error"]
    _write_config(db, DISTILL_RUN_KEY, _utcnow().isoformat())
    db.commit()
    engine.audit.record(
        "autopilot.distill_run",
        f"Automatic distillation over {len(ids)} episode(s)",
        detail={"episodes": len(ids), "result_keys": sorted((result or {}).keys())},
    )
    return [f"autopilot_distill:{len(ids)}"]


# ---------------------------------------------------------------------------
# Evolution triggers
# ---------------------------------------------------------------------------


def _cause_recent(db: Any, cause: str) -> bool:
    last = _read_config(db, EVOLUTION_CAUSE_PREFIX + cause)
    if not last:
        return False
    try:
        when = datetime.fromisoformat(last).replace(tzinfo=UTC)
    except ValueError:
        return False
    return _utcnow() - when < timedelta(days=DEDUPE_DAYS)


def _soul_exists(engine: Any, role: str) -> bool:
    from kompany.core.artifact_evolution.workspace import ArtifactWorkspace

    ws = ArtifactWorkspace(engine.settings.data_dir)
    return (ws.souls / f"{role}.yaml").is_file()


def failure_signals(engine: Any, threshold: int) -> list[dict[str, Any]]:
    since = _sqlite_ts(_utcnow() - timedelta(days=WINDOW_DAYS))
    rows = engine.db.execute(
        "SELECT assigned_agent AS role, COUNT(*) AS n, "
        "group_concat(title, ' | ') AS titles FROM tasks "
        "WHERE status = 'failed' AND replace(updated_at, 'T', ' ') > ? "
        "GROUP BY assigned_agent HAVING n >= ? ORDER BY n DESC",
        (since, int(threshold)),
    ).fetchall()
    return [
        {"role": str(r["role"]).lower(), "count": int(r["n"]), "titles": r["titles"] or ""}
        for r in rows
    ]


def health_signals(engine: Any, threshold: int) -> list[dict[str, Any]]:
    since = _sqlite_ts(_utcnow() - timedelta(days=WINDOW_DAYS))
    rows = engine.db.execute(
        "SELECT kind, COUNT(*) AS n FROM health_events "
        "WHERE replace(created_at, 'T', ' ') > ? "
        "GROUP BY kind HAVING n >= ? ORDER BY n DESC",
        (since, int(threshold)),
    ).fetchall()
    return [{"kind": r["kind"], "count": int(r["n"])} for r in rows]


def _propose(engine: Any, cause: str, role: str, instruction: str) -> str:
    db = engine.db
    if not _soul_exists(engine, role):
        # Mark the cause anyway: re-checking daily would only re-audit.
        _write_config(db, EVOLUTION_CAUSE_PREFIX + cause, _utcnow().isoformat())
        db.commit()
        engine.audit.record(
            "autopilot.evolution_skipped",
            f"No soul file for role {role!r}; cannot evolve",
            detail={"cause": cause, "role": role},
        )
        return f"autopilot_evolution:skip:{cause}"
    row = engine.evolution_propose("soul", role, instruction)
    _write_config(db, EVOLUTION_CAUSE_PREFIX + cause, _utcnow().isoformat())
    db.commit()
    engine.audit.record(
        "autopilot.evolution_proposed",
        f"Automatic soul evolution for {role} ({cause})",
        detail={
            "cause": cause,
            "role": role,
            "proposal_id": row.get("id"),
            "status": row.get("status"),
        },
    )
    return f"autopilot_evolution:{cause}:{row.get('status')}"


def evolution_tick(engine: Any) -> list[str]:
    settings = engine.settings
    if not bool(getattr(settings, "auto_evolution_enabled", True)):
        return []
    if not bool(getattr(settings, "artifact_evolution_enabled", True)):
        return []
    db = engine.db
    if _checked_today(db, EVOLUTION_CHECK_KEY):
        return []
    _mark_checked(db, EVOLUTION_CHECK_KEY)
    actions: list[str] = []
    fail_threshold = int(getattr(settings, "auto_evolution_failure_threshold", 2))
    for sig in failure_signals(engine, fail_threshold):
        cause = f"failures:{sig['role']}"
        if _cause_recent(db, cause):
            continue
        instruction = (
            f"In the last {WINDOW_DAYS} days this role failed {sig['count']} "
            f"task(s): {sig['titles'][:400]}. Revise the soul so the same "
            "class of task succeeds: tighten the operating rules, add a "
            "pre-flight check, or narrow the scope. Keep role and identity."
        )
        try:
            actions.append(_propose(engine, cause, sig["role"], instruction))
        except Exception:  # noqa: BLE001 — one proposal must not block the rest
            log.exception("autopilot evolution (failures) failed")
            actions.append(f"autopilot_evolution:error:{cause}")
    health_threshold = int(getattr(settings, "auto_evolution_health_threshold", 3))
    for sig in health_signals(engine, health_threshold):
        cause = f"health:{sig['kind']}"
        if _cause_recent(db, cause):
            continue
        instruction = (
            f"The health event '{sig['kind']}' opened {sig['count']} times in "
            f"the last {WINDOW_DAYS} days. Revise the Chief of Staff soul so "
            "operations prevent or resolve this recurrence: add an explicit "
            "rule, cadence or escalation path. Keep role and identity."
        )
        try:
            actions.append(_propose(engine, cause, HEALTH_TARGET_ROLE, instruction))
        except Exception:  # noqa: BLE001
            log.exception("autopilot evolution (health) failed")
            actions.append(f"autopilot_evolution:error:{cause}")
    return actions


__all__ = [
    "distill_tick",
    "evolution_tick",
    "failure_signals",
    "health_signals",
]
