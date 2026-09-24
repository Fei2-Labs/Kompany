"""Flip-rate metrics for the two self-evolution lanes (08-29 R7).

Headlong's single headline metric: how often the checker actually changes
the decision. If the founder has approved every code-lane proposal and the
doctor has kept every artifact-lane apply in the last ``WINDOW_DAYS``, the
gates are rubber stamps — nobody is looking, or the doctor has no teeth.

Both rates are pure reads off the proposal stores (no writes here). The
``evolution_rubber_stamp`` health event is reconciled on each doctor run by
``core/doctor.py`` ``persist_report``, mirroring ``doctor_failed``:
one open event while any lane sits at 0, resolved automatically when the
rate recovers.
"""

from __future__ import annotations

from typing import Any

KIND_RUBBER_STAMP = "evolution_rubber_stamp"
WINDOW_DAYS = 30
MIN_SAMPLE = 4  # ignore a zero rate until this many decisions exist to judge it


def _count(
    db: Any,
    table: str,
    statuses: tuple[str, ...],
    window_days: int,
    extra: str | None = None,
) -> int:
    """Rows of ``table`` whose ``status`` and ``updated_at`` fall in the window."""
    if not statuses:
        return 0
    marks = ",".join("?" for _ in statuses)
    sql = (
        f"SELECT COUNT(*) AS n FROM {table} "
        f"WHERE updated_at >= datetime('now', ?) AND status IN ({marks})"
    )
    params: list[Any] = [f"-{int(window_days)} days", *statuses]
    if extra:
        sql += " AND " + extra
    row = db.execute(sql, tuple(params)).fetchone()
    return int(row["n"] or 0)


def flip_rates(
    engine: Any,
    window_days: int = WINDOW_DAYS,
    min_sample: int = MIN_SAMPLE,
) -> dict[str, Any]:
    """Flip-rate snapshot for the code and artifact lanes over the rolling window.

    Code lane: ``rejected`` ÷ (``approved`` + ``rejected``) — how often the
    founder said no instead of approving the proposal wholesale.

    Artifact lane: doctor-triggered ``reverted`` (``doctor_status='fail'``)
    ÷ total auto-applies (``applied`` + ``reverted`` — every reverted row was
    auto-applied first). Founder reverts count toward the denominator (they
    were auto-applies too) but never the numerator, so only the doctor's
    teeth move the rate.

    ``rubber_stamp`` is true when a lane has at least ``min_sample``
    decisions in the window and still flipped zero of them.
    """
    db = engine.db
    code_approved = _count(db, "self_update_proposals", ("approved",), window_days)
    code_rejected = _count(db, "self_update_proposals", ("rejected",), window_days)
    code_decisions = code_approved + code_rejected

    artifact_applied = _count(db, "artifact_proposals", ("applied",), window_days)
    artifact_reverted = _count(
        db, "artifact_proposals", ("reverted",), window_days,
        extra="doctor_status = 'fail'",
    )
    artifact_all_reverted = _count(db, "artifact_proposals", ("reverted",), window_days)
    artifact_auto_applies = artifact_applied + artifact_all_reverted

    return {
        "window_days": int(window_days),
        "min_sample": int(min_sample),
        "code_lane": {
            "decisions": code_decisions,
            "approved": code_approved,
            "rejected": code_rejected,
            "rate": round(code_rejected / code_decisions, 4) if code_decisions else 0.0,
            "rubber_stamp": code_decisions >= int(min_sample) and code_rejected == 0,
        },
        "artifact_lane": {
            "auto_applies": artifact_auto_applies,
            "applied": artifact_applied,
            "doctor_reverts": artifact_reverted,
            "rate": round(artifact_reverted / artifact_auto_applies, 4) if artifact_auto_applies else 0.0,
            "rubber_stamp": artifact_auto_applies >= int(min_sample) and artifact_reverted == 0,
        },
        "rubber_stamp": (
            code_decisions >= int(min_sample) and code_rejected == 0
        ) or (
            artifact_auto_applies >= int(min_sample) and artifact_reverted == 0
        ),
    }


def reconcile_rubber_stamp(engine: Any) -> None:
    """Keep exactly one open ``evolution_rubber_stamp`` event while any lane sits at 0.

    Advisory: a failure to write the event is swallowed, exactly like the
    ``doctor_failed`` reconciliation.
    """
    try:
        rates = flip_rates(engine)
        he = getattr(engine, "health_events", None)
        if he is None:
            return
        open_events = he.list(status="open", kind=KIND_RUBBER_STAMP, limit=10)
        if rates["rubber_stamp"] and not open_events:
            he.record(
                kind=KIND_RUBBER_STAMP,
                detail={
                    "window_days": rates["window_days"],
                    "min_sample": rates["min_sample"],
                    "code_lane": rates["code_lane"],
                    "artifact_lane": rates["artifact_lane"],
                    "hint": ("The self-evolution gate never says no. Review proposals "
                             "in the audit feed (`kompany evolve status`) or strengthen "
                             "the doctor gate."),
                },
            )
        elif not rates["rubber_stamp"] and open_events:
            for ev in open_events:
                he.resolve(ev["id"], "continue", resolved_by="system")
    except Exception:  # noqa: BLE001 — advisory, like doctor_failed
        pass


__all__ = [
    "KIND_RUBBER_STAMP",
    "MIN_SAMPLE",
    "WINDOW_DAYS",
    "flip_rates",
    "reconcile_rubber_stamp",
]