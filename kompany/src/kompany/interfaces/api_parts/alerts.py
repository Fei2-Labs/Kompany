"""External alert ingestion — files system_alert approval cards.

External processes that run OUTSIDE the Kompany daemon (systemd-managed
browsers via ``kompany-browser@<name>.service``, the LinkedIn growth worker,
future X/Weibo workers, cron jobs, …) cannot reach the in-process
:class:`~kompany.core.event_hub.EventHub` directly. They need an HTTP seam to
push errors into the founder's inbox so failures surface in the board UI
"Needs You" column instead of rotting in a journal nobody reads.

Design
------
* ``POST /alerts`` files an ``action_type="system_alert"`` approval card.
  ``system_alert`` is deliberately NOT in
  :data:`kompany.core.approval_effects.HARNESS_EFFECT_ACTIONS`, so
  approving/dismissing it is a safe no-op (no money moves, no tool fires).
  The card shows in ``/inbox`` → board "Needs You" column automatically and
  live-updates via the existing ``inbox.updated`` SSE event.
* **Dedup by source**: if a pending ``system_alert`` with the same
  ``payload.source`` already exists, the request refreshes its
  ``summary`` / ``message`` / ``created_at`` instead of creating a duplicate.
  This prevents a restart-looping service from spamming N cards.
* ``POST /alerts/{source}/resolve`` marks the matching pending alert
  ``approved`` (acknowledged). Called by workers on a successful cycle after
  a prior failure, or by the founder dismissing the card in the UI (the
  existing ``POST /approvals/{id}/approve`` path works too).

This is the minimal right-sized seam: no new table, no new SSE channel, no
board UI change. Alerts ride the existing inbox rails.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from kompany.state.models import APPROVAL_SEVERITIES, ApprovalRequest
from kompany.interfaces.api_parts.deps import get_engine

router = APIRouter()

ACTION_SYSTEM_ALERT = "system_alert"


class AlertRequest(BaseModel):
    """File a system alert into the founder inbox.

    ``source`` is the stable dedup key — e.g. ``"browser:linkedin"``,
    ``"linkedin:session"``, ``"worker:linkedin"``. Re-filing the same source
    refreshes the existing pending card instead of stacking duplicates.
    """

    model_config = ConfigDict(extra="forbid")
    source: str = Field(..., min_length=1, max_length=120)
    severity: str = Field(default="high")
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(default="")


def _find_pending_alert(engine: Any, source: str) -> ApprovalRequest | None:
    """Return the pending ``system_alert`` card for ``source``, if any."""
    row = engine.db.execute(
        """SELECT * FROM approval_requests
           WHERE status = 'pending' AND action_type = ?
           AND json_extract(payload, '$.source') = ?
           ORDER BY created_at DESC LIMIT 1""",
        (ACTION_SYSTEM_ALERT, source),
    ).fetchone()
    if row is None:
        return None
    return _row_to_request(row)


def _row_to_request(row: Any) -> ApprovalRequest:
    payload = json.loads(row["payload"]) if row["payload"] else {}
    return ApprovalRequest(
        id=row["id"],
        status=row["status"],
        action_type=row["action_type"],
        summary=row["summary"],
        payload=payload,
        severity=row["severity"],
        created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if isinstance(row["created_at"], str)
        else row["created_at"],
    )


@router.post("/alerts")
def file_alert(req: AlertRequest) -> dict[str, Any]:
    """File or refresh a system alert card in the founder inbox."""
    if req.severity not in APPROVAL_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid severity {req.severity!r}; expected one of "
            f"{sorted(APPROVAL_SEVERITIES)}",
        )
    engine = get_engine()
    existing = _find_pending_alert(engine, req.source)
    now_iso = datetime.now(UTC).isoformat(sep=" ")
    if existing is not None:
        # Refresh: update summary + message + timestamp so the card surfaces
        # the latest failure rather than the first one. Stays pending.
        engine.db.execute(
            """UPDATE approval_requests
               SET summary = ?, payload = ?, severity = ?, created_at = ?
               WHERE id = ?""",
            (
                req.title,
                json.dumps({"source": req.source, "message": req.message}),
                req.severity,
                now_iso,
                existing.id,
            ),
        )
        engine.db.commit()
        # Best-effort live push so the board refetches the refreshed card.
        try:
            from kompany.core.event_hub import get_event_hub

            get_event_hub().publish("inbox.updated", {"reason": "alert_refreshed"})
        except Exception:  # pragma: no cover — best-effort
            pass
        return {"id": existing.id, "status": "updated", "source": req.source}

    request = ApprovalRequest(
        action_type=ACTION_SYSTEM_ALERT,
        severity=req.severity,
        summary=req.title,
        payload={"source": req.source, "message": req.message},
        requested_by="system",
    )
    engine.approvals.create(request)
    return {"id": request.id, "status": "filed", "source": req.source}


@router.post("/alerts/{source}/resolve")
def resolve_alert(source: str) -> dict[str, Any]:
    """Mark a pending system alert for ``source`` as resolved (acknowledged)."""
    engine = get_engine()
    existing = _find_pending_alert(engine, source)
    if existing is None:
        return {"status": "no_pending", "source": source}
    result = engine.approve_request(existing.id, approved_by="system")
    if result is None:
        return {"status": "no_pending", "source": source}
    return {"id": existing.id, "status": "resolved", "source": source}
