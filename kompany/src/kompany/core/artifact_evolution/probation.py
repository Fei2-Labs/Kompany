"""Probation gate for evolved souls (09-26-evolution-probation).

Borrowed shape (MemOS local plugin: probationary → active/retired after a
fixed trial count; skillopt: keep an edit only when it scores strictly
better). Kompany version is *online* — no replay cost: the doctor already
proved the evolved soul loads, so it stays applied and the role keeps
working; the next ``trial_target`` finished tasks of that role are the
trial. Compared with the baseline failure rate (same role, the
``window_days`` before the proposal):

* trial rate > baseline rate **and** at least two trial failures
  → auto-revert (git revert of the proposal commit, same path the doctor
  uses), audit + notification;
* trial rate <= baseline → ``passed``;
* fewer than ``trial_target`` tasks after ``max_days`` → ``inconclusive``
  (soul stays; the row says nobody could tell).

Founder reverts close the probation as ``closed``. Everything here is
plain SQL over ``tasks``; no LLM call.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

TERMINAL = ("completed", "delivered", "failed")
MIN_TRIAL_FAILURES = 2


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sqlite_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def role_from_target(target: str) -> str:
    t = str(target)
    return (t[:-5] if t.endswith(".yaml") else t).lower()


def _counts(db: Any, role: str, since: str, until: str | None = None) -> tuple[int, int]:
    sql = (
        "SELECT SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
        "COUNT(*) AS total FROM tasks WHERE lower(assigned_agent) = ? "
        "AND status IN ('completed', 'delivered', 'failed') "
        "AND replace(updated_at, 'T', ' ') > ?"
    )
    params: list[Any] = [role, since]
    if until is not None:
        sql += " AND replace(updated_at, 'T', ' ') <= ?"
        params.append(until)
    row = db.execute(sql, tuple(params)).fetchone()
    return int(row["failed"] or 0), int(row["total"] or 0)


def start_probation(engine: Any, proposal: dict[str, Any]) -> dict[str, Any] | None:
    """Open a probation row for an applied soul proposal. Best-effort."""
    settings = engine.settings
    if not bool(getattr(settings, "evolution_probation_enabled", True)):
        return None
    if proposal.get("kind") != "soul" or proposal.get("status") != "applied":
        return None
    role = role_from_target(proposal["target"])
    window = int(getattr(settings, "evolution_probation_window_days", 14))
    now = _utcnow()
    failed, total = _counts(
        engine.db, role, _sqlite_ts(now - timedelta(days=window)), _sqlite_ts(now)
    )
    row = engine.artifact_probations.start(
        proposal_id=proposal["id"],
        role=role,
        baseline_failed=failed,
        baseline_total=total,
        trial_target=int(getattr(settings, "evolution_probation_trials", 5)),
    )
    engine.audit.record(
        "artifact_evolution.probation_started",
        f"Soul {role} on probation after proposal {proposal['id']}: "
        f"baseline {failed}/{total} failed over {window}d",
        detail={"proposal_id": proposal["id"], "role": role, "baseline_failed": failed,
                "baseline_total": total, "trial_target": row.get("trial_target")},
    )
    return row


def close_probation(engine: Any, proposal_id: str, note: str) -> None:
    """Founder or doctor revert: the trial is moot."""
    store = getattr(engine, "artifact_probations", None)
    if store is None:
        return
    row = store.get(proposal_id)
    if row and row["status"] == "probation":
        store.decide(proposal_id, "closed", note)


def _verdict(
    row: dict[str, Any], failed: int, total: int, max_days: int
) -> tuple[str, str] | None:
    baseline_rate = (
        row["baseline_failed"] / row["baseline_total"] if row["baseline_total"] else 0.0
    )
    if total >= int(row["trial_target"]):
        rate = failed / total
        if rate > baseline_rate and failed >= MIN_TRIAL_FAILURES:
            return "reverted", (
                f"trial {failed}/{total} failed ({rate:.0%}) vs baseline "
                f"{row['baseline_failed']}/{row['baseline_total']} ({baseline_rate:.0%})"
            )
        return "passed", (
            f"trial {failed}/{total} failed ({rate:.0%}) vs baseline "
            f"{row['baseline_failed']}/{row['baseline_total']} ({baseline_rate:.0%})"
        )
    started = datetime.fromisoformat(str(row["started_at"]).replace(" ", "T")).replace(tzinfo=UTC)
    if _utcnow() - started > timedelta(days=max_days):
        return "inconclusive", f"only {total} trial task(s) in {max_days} days"
    return None


def probation_tick(engine: Any) -> list[str]:
    """Ticker step: advance every open probation; revert / pass / expire."""
    store = getattr(engine, "artifact_probations", None)
    if store is None or not bool(getattr(engine.settings, "evolution_probation_enabled", True)):
        return []
    max_days = int(getattr(engine.settings, "evolution_probation_max_days", 30))
    actions: list[str] = []
    for row in store.list(status="probation", limit=50):
        since = str(row["started_at"]).replace("T", " ")[:19]
        failed, total = _counts(engine.db, row["role"], since)
        store.progress(row["proposal_id"], trial_failed=failed, trial_total=total)
        verdict = _verdict(row, failed, total, max_days)
        if verdict is None:
            continue
        status, note = verdict
        pid = row["proposal_id"]
        if status == "reverted":
            try:
                from kompany.core.artifact_evolution.pipeline import revert_artifact_proposal

                revert_artifact_proposal(engine, pid, reason=f"probation failed: {note}")
            except Exception:  # noqa: BLE001 — recorded; the row still says why
                log.exception("probation revert failed for %s", pid)
                actions.append(f"evolution_probation:{pid}:revert_error")
                continue
        store.decide(pid, status, note)
        engine.audit.record(
            f"artifact_evolution.probation_{status}",
            f"Soul {row['role']} probation {status}: {note}",
            detail={"proposal_id": pid, "role": row["role"], "trial_failed": failed,
                    "trial_total": total, "note": note},
        )
        actions.append(f"evolution_probation:{pid}:{status}")
    return actions


__all__ = [
    "MIN_TRIAL_FAILURES",
    "close_probation",
    "probation_tick",
    "role_from_target",
    "start_probation",
]
