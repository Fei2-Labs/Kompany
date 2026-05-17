"""Approval request persistence for AutonomyGate decisions."""

from __future__ import annotations

import json

from kompany.state.database import Database
from kompany.state.models import ApprovalRequest, ApprovalStatus


class ApprovalRequests:
    """Stores pending and resolved user approval requests."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        self.db.execute(
            """INSERT INTO approval_requests
               (id, status, action_type, summary, payload, directive_id,
                project_id, requested_by, resolved_by, resolution_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        self.db.commit()
        return request

    def list_pending(self) -> list[ApprovalRequest]:
        rows = self.db.execute(
            """SELECT * FROM approval_requests
               WHERE status = 'pending'
               ORDER BY created_at""",
        ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def get(self, request_id: str) -> ApprovalRequest | None:
        row = self.db.execute(
            "SELECT * FROM approval_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        return self._row_to_request(row) if row else None

    def approve(self, request_id: str, approved_by: str = "master") -> ApprovalRequest | None:
        return self._resolve(request_id, ApprovalStatus.APPROVED, approved_by, None)

    def reject(
        self,
        request_id: str,
        rejected_by: str = "master",
        reason: str | None = None,
    ) -> ApprovalRequest | None:
        return self._resolve(request_id, ApprovalStatus.REJECTED, rejected_by, reason)

    def update_payload(self, request_id: str, patch: dict) -> ApprovalRequest | None:
        """Merge `patch` into the request's payload without changing status."""
        request = self.get(request_id)
        if request is None:
            return None
        merged = {**(request.payload or {}), **patch}
        self.db.execute(
            "UPDATE approval_requests SET payload = ? WHERE id = ?",
            (json.dumps(merged), request_id),
        )
        self.db.commit()
        return self.get(request_id)

    def _resolve(
        self,
        request_id: str,
        status: ApprovalStatus,
        resolved_by: str,
        reason: str | None,
    ) -> ApprovalRequest | None:
        self.db.execute(
            """UPDATE approval_requests
               SET status = ?, resolved_by = ?, resolution_reason = ?,
                   resolved_at = datetime('now')
               WHERE id = ? AND status = 'pending'""",
            (status.value, resolved_by, reason, request_id),
        )
        self.db.commit()
        return self.get(request_id)

    @staticmethod
    def _row_to_request(row) -> ApprovalRequest:
        return ApprovalRequest(
            id=row["id"],
            status=row["status"],
            action_type=row["action_type"],
            summary=row["summary"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            directive_id=row["directive_id"],
            project_id=row["project_id"],
            requested_by=row["requested_by"],
            resolved_by=row["resolved_by"],
            resolution_reason=row["resolution_reason"],
        )
