"""Create and read operations for ApprovalRequests."""

from __future__ import annotations

import json

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import (
    APPROVAL_SEVERITIES,
    ApprovalRequest,
)

from ._shared import _publish_inbox_updated


class _CrudMixin:
    """Create / read methods for :class:`ApprovalRequests`."""

    db: Database

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        request: ApprovalRequest,
        run_id: str | None = None,
    ) -> ApprovalRequest:
        if request.severity not in APPROVAL_SEVERITIES:
            raise ValueError(
                f"invalid severity {request.severity!r}; expected one of "
                f"{sorted(APPROVAL_SEVERITIES)}"
            )
        rid = run_id if run_id is not None else current_run_id()
        snoozed_until = (
            request.snoozed_until.isoformat(sep=" ")
            if request.snoozed_until
            else None
        )
        self.db.execute(
            """INSERT INTO approval_requests
               (id, status, action_type, summary, payload, directive_id,
                project_id, requested_by, resolved_by, resolution_reason,
                run_id, severity, predecessor_id, snoozed_until, snoozed_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request.id,
                request.status.value,
                request.action_type,
                request.summary,
                json.dumps(request.payload),
                request.directive_id,
                request.project_id,
                request.requested_by,
                request.resolved_by,
                request.resolution_reason,
                rid,
                request.severity,
                request.predecessor_id,
                snoozed_until,
                request.snoozed_by,
            ),
        )
        self.db.commit()
        _publish_inbox_updated("created", request.id)
        return request

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_pending(self) -> list[ApprovalRequest]:
        rows = self.db.execute(
            """SELECT * FROM approval_requests
               WHERE status = 'pending'
               ORDER BY created_at""",
        ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def list_by_status(self, status: str | None = None) -> list[ApprovalRequest]:
        """List approvals, optionally filtered by status.

        Used by ``kompany inbox`` to show ``pending`` + ``snoozed`` together
        and by retrospective surfaces to show terminal states.
        """
        if status is None:
            rows = self.db.execute(
                "SELECT * FROM approval_requests ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM approval_requests WHERE status = ? "
                "ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def list_for_project(self, project_id: str) -> list[ApprovalRequest]:
        """All approvals tied to ``project_id`` (used by episode materializer)."""
        rows = self.db.execute(
            "SELECT * FROM approval_requests WHERE project_id = ? "
            "ORDER BY created_at, rowid",
            (project_id,),
        ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def get(self, request_id: str) -> ApprovalRequest | None:
        row = self.db.execute(
            "SELECT * FROM approval_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        return self._row_to_request(row) if row else None
