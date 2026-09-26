"""Heartbeat change push (09-26-autopilot-reports).

The 5-minute heartbeat already emits ``pending_approvals`` /
``runtime_suspended`` events but only to the audit log. Pushing every
tick would spam the founder; never pushing hides a stuck approval for a
day. This step pushes ONLY when the fingerprint (runtime state + sorted
pending approval ids) changes — a new approval or a suspension shows up
once, then stays quiet until the next change.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from kompany.state.ui_preferences import _read_config, _write_config

log = logging.getLogger(__name__)

FINGERPRINT_KEY = "autopilot.heartbeat.last_push_fp"
PUSH_KINDS = ("pending_approvals", "runtime_suspended")


def fingerprint(payload: dict[str, Any]) -> str:
    runtime = (payload.get("runtime") or {}).get("state")
    ids: list[str] = []
    for ev in payload.get("notifications") or []:
        if ev.get("kind") == "pending_approvals":
            ids = sorted(str(i) for i in (ev.get("payload") or {}).get("approval_ids", []))
    raw = json.dumps({"runtime": runtime, "approvals": ids}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def heartbeat_push(engine: Any, payload: dict[str, Any]) -> list[str]:
    """Push heartbeat events through the auto adapter when state changed."""
    if not bool(getattr(engine.settings, "heartbeat_push_on_change", True)):
        return []
    db = engine.db
    fp = fingerprint(payload)
    if _read_config(db, FINGERPRINT_KEY) == fp:
        return []
    _write_config(db, FINGERPRINT_KEY, fp)
    db.commit()
    events = [
        ev for ev in payload.get("notifications") or [] if ev.get("kind") in PUSH_KINDS
    ]
    if not events:
        # State changed back to "nothing pending": remember it, say nothing.
        return ["heartbeat_push:quiet"]
    try:
        deliveries = engine.dispatch_notifications(events, adapter="auto")
    except Exception:  # noqa: BLE001 — a failed push must not kill the tick
        log.exception("heartbeat push failed")
        return ["heartbeat_push:error"]
    return [f"heartbeat_push:{d.get('status')}" for d in deliveries]


__all__ = ["fingerprint", "heartbeat_push"]
