"""Shared constants, exceptions, and helpers for approvals_parts."""

from __future__ import annotations

from kompany.core.event_hub import get_event_hub


# Comment ``by_type`` enumeration. ``user`` = player; ``agent`` = a C-level
# or worker role (``by_id`` carries the role); ``system`` = scanner /
# auto-transition / audit note.
COMMENT_BY_TYPES: frozenset[str] = frozenset({"user", "agent", "system"})


# Defensive cap for ``list_thread`` predecessor walks. We don't expect any
# real chain to come close to this, but a corrupted ``predecessor_id``
# cycle would otherwise hang the inbox renderer.
_MAX_THREAD_HOPS = 10


class IllegalApprovalTransition(ValueError):
    """Raised when a state transition is not legal under the state machine.

    Subclass of ``ValueError`` so existing callers that catch ``ValueError``
    (engine ``raise ValueError(...)`` patterns) keep working.
    """


def _publish_inbox_updated(reason: str, request_id: str | None = None) -> None:
    """Best-effort SSE push that the inbox state changed."""
    try:
        get_event_hub().publish(
            "inbox.updated",
            {"reason": reason, "approval_id": request_id},
        )
    except Exception:  # pragma: no cover — best-effort live feed
        pass
