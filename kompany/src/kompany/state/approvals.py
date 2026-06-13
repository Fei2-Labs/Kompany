"""Approval request persistence for AutonomyGate decisions.

Beyond the original three-state flow (``pending`` -> ``approved`` /
``rejected``), this service now backs the **approval thread + RPG inbox**
(05-18-approval-thread-and-rpg):

* New terminal states: ``revision_requested``, ``cancelled``.
* New non-terminal state: ``snoozed`` (Watchdog auto-unsnoozes it).
* ``approval_comments`` sub-table for a re-playable decision timeline.
* ``predecessor_id`` self-reference so a player counter-proposal can
  chain back to the agent's original request.

The state machine is enforced inside this service — callers should never
write a raw status string to the row; use the dedicated method
(``approve`` / ``reject`` / ``request_revision`` / ``snooze`` / ``cancel``
/ ``unsnooze``) and let ``_assert_transition`` reject illegal moves.

Implementation is split across ``approvals_parts/`` (ADR-0003); this module
is the sole public entry point.
"""

from __future__ import annotations

from kompany.state.database import Database
from kompany.state.models import ApprovalRequest  # re-exported for call sites
from kompany.state.approvals_parts._shared import (
    COMMENT_BY_TYPES,
    IllegalApprovalTransition,
)
from kompany.state.approvals_parts._crud import _CrudMixin
from kompany.state.approvals_parts._transitions import _TransitionsMixin
from kompany.state.approvals_parts._comments_thread import _CommentsThreadMixin


class ApprovalRequests(_CrudMixin, _TransitionsMixin, _CommentsThreadMixin):
    """Stores pending and resolved user approval requests."""

    def __init__(self, db: Database):
        self.db = db


__all__ = [
    "ApprovalRequest",
    "ApprovalRequests",
    "COMMENT_BY_TYPES",
    "IllegalApprovalTransition",
]
