"""State-machine transition methods for ApprovalRequests."""

from __future__ import annotations

from kompany.state.database import Database
from kompany.state.models import (
    APPROVAL_TERMINAL_STATUSES,
    ApprovalRequest,
    ApprovalStatus,
)

from ._shared import IllegalApprovalTransition, _publish_inbox_updated


class _TransitionsMixin:
    """State-machine transition methods for :class:`ApprovalRequests`."""

    db: Database

    # NOTE: ``get`` and ``add_comment`` are provided by the sibling mixins
    # (_CrudMixin / _CommentsThreadMixin) in the composed ``ApprovalRequests``
    # class. They are intentionally NOT redeclared here — a stub body would
    # shadow the real implementations via MRO.

    # ------------------------------------------------------------------
    # State machine transitions
    # ------------------------------------------------------------------

    def approve(
        self,
        request_id: str,
        approved_by: str = "master",
        comment_body: str | None = None,
    ) -> ApprovalRequest | None:
        result = self._resolve(request_id, ApprovalStatus.APPROVED, approved_by, None)
        if result is not None and comment_body:
            self.add_comment(
                approval_id=request_id,
                body=comment_body,
                by_type="user",
                by_id=approved_by if approved_by != "master" else None,
            )
        return result

    def reject(
        self,
        request_id: str,
        rejected_by: str = "master",
        reason: str | None = None,
        comment_body: str | None = None,
    ) -> ApprovalRequest | None:
        result = self._resolve(request_id, ApprovalStatus.REJECTED, rejected_by, reason)
        if result is not None and comment_body:
            self.add_comment(
                approval_id=request_id,
                body=comment_body,
                by_type="user",
                by_id=rejected_by if rejected_by != "master" else None,
            )
        return result

    def request_revision(
        self,
        request_id: str,
        comment_body: str,
        by_type: str = "user",
        by_id: str | None = None,
    ) -> ApprovalRequest | None:
        """Transition to ``revision_requested`` and write the counter-proposal.

        Terminal — the agent does not modify this row again. A successor
        approval (created by the engine's revision handler) carries the
        ``predecessor_id`` link.
        """
        existing = self.get(request_id)
        if existing is None:
            return None
        self._assert_transition(existing.status, ApprovalStatus.REVISION_REQUESTED)
        if not comment_body or not comment_body.strip():
            raise ValueError("request_revision requires a non-empty comment_body")
        # Always log the counter-proposal as a comment.
        self.add_comment(
            approval_id=request_id,
            body=comment_body,
            by_type=by_type,
            by_id=by_id,
        )
        self.db.execute(
            """UPDATE approval_requests
               SET status = ?, resolved_by = ?, resolution_reason = ?,
                   resolved_at = datetime('now')
               WHERE id = ?""",
            (
                ApprovalStatus.REVISION_REQUESTED.value,
                by_id if by_type == "user" else by_id or by_type,
                comment_body[:500],
                request_id,
            ),
        )
        self.db.commit()
        _publish_inbox_updated("revision_requested", request_id)
        return self.get(request_id)

    def snooze(
        self,
        request_id: str,
        minutes: int,
        by_type: str = "user",
        by_id: str | None = None,
        comment_body: str | None = None,
    ) -> ApprovalRequest | None:
        """Transition to ``snoozed``. Auto-unsnoozes after ``minutes`` minutes.

        ``snoozed_until`` is computed from SQLite's ``datetime('now')`` so
        the watchdog's later comparison (also against ``datetime('now')``)
        works without timezone mismatch.
        """
        if minutes <= 0:
            raise ValueError("snooze minutes must be > 0")
        existing = self.get(request_id)
        if existing is None:
            return None
        self._assert_transition(existing.status, ApprovalStatus.SNOOZED)
        self.db.execute(
            """UPDATE approval_requests
               SET status = ?, snoozed_until = datetime('now', ?),
                   snoozed_by = ?, resolved_at = NULL
               WHERE id = ?""",
            (
                ApprovalStatus.SNOOZED.value,
                f"+{int(minutes)} minutes",
                by_id or by_type,
                request_id,
            ),
        )
        self.db.commit()
        body = comment_body or f"snoozed for {int(minutes)}m"
        self.add_comment(
            approval_id=request_id,
            body=body,
            by_type=by_type,
            by_id=by_id,
        )
        _publish_inbox_updated("snoozed", request_id)
        return self.get(request_id)

    def cancel(
        self,
        request_id: str,
        reason: str | None = None,
        by_type: str = "user",
        by_id: str | None = None,
        comment_body: str | None = None,
    ) -> ApprovalRequest | None:
        """Terminal: the request is withdrawn (player decides "not now")."""
        existing = self.get(request_id)
        if existing is None:
            return None
        self._assert_transition(existing.status, ApprovalStatus.CANCELLED)
        self.db.execute(
            """UPDATE approval_requests
               SET status = ?, resolved_by = ?, resolution_reason = ?,
                   resolved_at = datetime('now'),
                   snoozed_until = NULL
               WHERE id = ?""",
            (
                ApprovalStatus.CANCELLED.value,
                by_id or by_type,
                reason,
                request_id,
            ),
        )
        self.db.commit()
        body = comment_body or (reason or "cancelled")
        self.add_comment(
            approval_id=request_id,
            body=body,
            by_type=by_type,
            by_id=by_id,
        )
        _publish_inbox_updated("cancelled", request_id)
        return self.get(request_id)

    def unsnooze(self, request_id: str, by: str = "system") -> ApprovalRequest | None:
        """Return a ``snoozed`` row back to ``pending``.

        Called by the watchdog's ``_scan_snoozed_approvals`` tick. Idempotent
        — if the row is not currently ``snoozed`` it returns the current
        state unchanged (no exception) so a racing manual unsnooze does not
        crash the scanner.
        """
        existing = self.get(request_id)
        if existing is None:
            return None
        if existing.status != ApprovalStatus.SNOOZED:
            return existing
        self.db.execute(
            """UPDATE approval_requests
               SET status = ?, snoozed_until = NULL, snoozed_by = NULL
               WHERE id = ? AND status = 'snoozed'""",
            (ApprovalStatus.PENDING.value, request_id),
        )
        self.db.commit()
        _publish_inbox_updated("unsnoozed", request_id)
        return self.get(request_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(
        self,
        request_id: str,
        status: ApprovalStatus,
        resolved_by: str,
        reason: str | None,
    ) -> ApprovalRequest | None:
        existing = self.get(request_id)
        if existing is None:
            return None
        # Idempotent: re-approving an already-approved row (or
        # re-rejecting) is a no-op that returns the existing row. This
        # preserves the legacy ``UPDATE ... WHERE status = 'pending'``
        # semantics that several call sites (remote command replay,
        # double-tap CLI) rely on.
        if existing.status == status:
            return existing
        self._assert_transition(existing.status, status)
        self.db.execute(
            """UPDATE approval_requests
               SET status = ?, resolved_by = ?, resolution_reason = ?,
                   resolved_at = datetime('now')
               WHERE id = ?""",
            (status.value, resolved_by, reason, request_id),
        )
        self.db.commit()
        _publish_inbox_updated(f"resolved.{status.value}", request_id)
        return self.get(request_id)

    @staticmethod
    def _assert_transition(
        current: ApprovalStatus,
        target: ApprovalStatus,
    ) -> None:
        """Validate ``current -> target`` against the state machine.

        Legal moves:

        * ``pending``    -> approved, rejected, revision_requested, snoozed, cancelled
        * ``snoozed``    -> pending, approved, rejected, revision_requested, cancelled
        * everything else (terminal) -> nothing
        """
        if current in APPROVAL_TERMINAL_STATUSES:
            raise IllegalApprovalTransition(
                f"approval is in terminal state {current.value!r}; "
                f"cannot transition to {target.value!r}"
            )
        if current == target:
            raise IllegalApprovalTransition(
                f"approval is already in state {current.value!r}"
            )
        # pending and snoozed both accept all non-self targets; the only
        # forbidden non-terminal->non-terminal move would be pending->snoozed
        # back to itself, which the equality check above already blocks. We
        # intentionally allow snoozed -> revision_requested / approved / etc.
        # so a player who acts before the watchdog wakes up isn't blocked.
