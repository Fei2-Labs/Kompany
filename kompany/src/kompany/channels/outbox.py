"""Outbound channel drafts — approval-gated, never auto-posted.

PRD ``06-12-channels`` D3, Tier 1 (MVP): anything Anima wants to
publish (diary excerpt, milestone) becomes a ``channel_outbox`` row
with status ``draft`` plus one ``channel_post`` approval card. Approve
= mark approved + "copy/post manually" comment — NO network call is
ever made in this module (constitution publishing gate). Reject =
discarded. Tier 2/3 (standing grants, auto-post) land with the X/Weibo
integration task, not here.
"""

from __future__ import annotations

import uuid
from typing import Any

from kompany.state.models import ApprovalRequest

ACTION_CHANNEL_POST = "channel_post"

OUTBOX_STATUSES = ("draft", "approved", "discarded")

# Max chars of a diary entry carried into an outbox draft.
DIARY_EXCERPT_CHARS = 400

APPROVE_COMMENT = (
    "Approved — copy/post manually; auto-posting lands with the "
    "X/Weibo integration."
)


class OutboxStore:
    """SQLite-backed ``channel_outbox`` rows."""

    def __init__(self, db: Any):
        self.db = db

    def add(self, channel: str, text: str, source: str = "") -> dict[str, Any]:
        row_id = uuid.uuid4().hex[:8]
        self.db.execute(
            """INSERT INTO channel_outbox (id, channel, text, source)
               VALUES (?, ?, ?, ?)""",
            (row_id, channel, text, source),
        )
        self.db.commit()
        return self.get(row_id)  # type: ignore[return-value]

    def get(self, row_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM channel_outbox WHERE id = ?", (row_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_status(self, row_id: str, status: str) -> None:
        if status not in OUTBOX_STATUSES:
            raise ValueError(f"invalid outbox status {status!r}")
        self.db.execute(
            """UPDATE channel_outbox
               SET status = ?, updated_at = datetime('now') WHERE id = ?""",
            (status, row_id),
        )
        self.db.commit()

    def set_approval_id(self, row_id: str, approval_id: str) -> None:
        self.db.execute(
            """UPDATE channel_outbox
               SET approval_id = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (approval_id, row_id),
        )
        self.db.commit()

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT * FROM channel_outbox
               ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]

    def counts_by_status(self) -> dict[str, int]:
        counts = {s: 0 for s in OUTBOX_STATUSES}
        for row in self.db.execute(
            "SELECT status, COUNT(*) AS n FROM channel_outbox GROUP BY status"
        ).fetchall():
            counts[row["status"]] = int(row["n"])
        return counts


# ---------------------------------------------------------------------------
# Draft creation (files the approval card)
# ---------------------------------------------------------------------------


def create_draft(
    engine: Any, channel: str, text: str, source: str = ""
) -> dict[str, Any]:
    """Persist a draft and file its ``channel_post`` approval card."""
    store = OutboxStore(engine.db)
    row = store.add(channel=channel, text=text, source=source)
    request = engine.approvals.create(
        ApprovalRequest(
            action_type=ACTION_CHANNEL_POST,
            summary=f"Post to {channel}: {text[:80]}",
            payload={
                "outbox_id": row["id"],
                "channel": channel,
                "text": text,
                "source": source,
            },
            requested_by="anima",
            severity="low",
        )
    )
    store.set_approval_id(row["id"], request.id)
    engine.audit.record(
        "channel_outbox.drafted",
        f"Outbox draft created for {channel}",
        detail={"outbox_id": row["id"], "approval_id": request.id},
    )
    return {**row, "approval_id": request.id}


def diary_outbox_hook(engine: Any, date: str, entry: str) -> dict[str, Any]:
    """Flag-gated diary → outbox draft (PRD D3). Caller checks the flag."""
    excerpt = entry.strip()[:DIARY_EXCERPT_CHARS]
    return create_draft(
        engine, channel="x", text=excerpt, source=f"anima_diary:{date}"
    )


# ---------------------------------------------------------------------------
# Approval effects (wired through core/approval_effects.py)
# ---------------------------------------------------------------------------


def approve_channel_post(engine: Any, request: Any) -> dict[str, Any]:
    """Mark the draft approved. NO posting happens here (Tier 1)."""
    payload = request.payload or {}
    outbox_id = payload.get("outbox_id")
    store = OutboxStore(engine.db)
    row = store.get(str(outbox_id)) if outbox_id else None
    if row is None:
        return {"status": "invalid_payload"}
    store.set_status(row["id"], "approved")
    engine.approvals.update_payload(request.id, {"effect_applied": True})
    try:
        engine.approvals.add_comment(
            request.id, body=APPROVE_COMMENT, by_type="system", by_id=None
        )
    except Exception:  # noqa: BLE001 — outcome is audited below
        pass
    engine.audit.record(
        "channel_outbox.approved",
        f"Outbox draft approved for {row['channel']} (manual post)",
        detail={"outbox_id": row["id"], "approval_id": request.id},
    )
    return {"status": "approved_for_manual_post", "outbox_id": row["id"]}


def reject_channel_post(engine: Any, request: Any) -> dict[str, Any]:
    """Discard the draft."""
    payload = request.payload or {}
    outbox_id = payload.get("outbox_id")
    store = OutboxStore(engine.db)
    row = store.get(str(outbox_id)) if outbox_id else None
    if row is None:
        return {"status": "invalid_payload"}
    store.set_status(row["id"], "discarded")
    engine.audit.record(
        "channel_outbox.discarded",
        f"Outbox draft discarded for {row['channel']}",
        detail={"outbox_id": row["id"], "approval_id": request.id},
    )
    return {"status": "discarded", "outbox_id": row["id"]}


__all__ = [
    "ACTION_CHANNEL_POST",
    "APPROVE_COMMENT",
    "OutboxStore",
    "approve_channel_post",
    "create_draft",
    "diary_outbox_hook",
    "reject_channel_post",
]
