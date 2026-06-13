"""Comments, thread-walk, and row-mapping internals for ApprovalRequests."""

from __future__ import annotations

import json

from kompany.state.database import Database
from kompany.state.models import (
    ApprovalComment,
    ApprovalRequest,
)

from ._shared import COMMENT_BY_TYPES, _MAX_THREAD_HOPS


class _CommentsThreadMixin:
    """Comment, thread-walk, helper-update, and row-mapping methods
    for :class:`ApprovalRequests`."""

    db: Database

    # Forward-declared by the concrete class that mixes everything together.
    def get(self, request_id: str) -> ApprovalRequest | None: ...

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def add_comment(
        self,
        approval_id: str,
        body: str,
        by_type: str,
        by_id: str | None = None,
    ) -> ApprovalComment:
        if by_type not in COMMENT_BY_TYPES:
            raise ValueError(
                f"invalid by_type {by_type!r}; expected one of {sorted(COMMENT_BY_TYPES)}"
            )
        if not body or not body.strip():
            raise ValueError("comment body must be non-empty")
        comment = ApprovalComment(
            approval_id=approval_id,
            by_type=by_type,
            by_id=by_id,
            body=body,
        )
        self.db.execute(
            """INSERT INTO approval_comments
               (id, approval_id, by_type, by_id, body)
               VALUES (?, ?, ?, ?, ?)""",
            (
                comment.id,
                comment.approval_id,
                comment.by_type,
                comment.by_id,
                comment.body,
            ),
        )
        self.db.commit()
        # Re-read to pick up the SQL-side ``created_at`` default.
        return self.get_comment(comment.id) or comment

    def list_comments(self, approval_id: str) -> list[ApprovalComment]:
        """All comments on one approval, oldest-first.

        ``created_at`` is second-resolution in SQLite; rows that share a
        timestamp are tie-broken on ``rowid`` so the rendered order matches
        insertion order even within a single second.
        """
        rows = self.db.execute(
            """SELECT * FROM approval_comments WHERE approval_id = ?
               ORDER BY created_at ASC, rowid ASC""",
            (approval_id,),
        ).fetchall()
        return [self._row_to_comment(r) for r in rows]

    def get_comment(self, comment_id: str) -> ApprovalComment | None:
        row = self.db.execute(
            "SELECT * FROM approval_comments WHERE id = ?",
            (comment_id,),
        ).fetchone()
        return self._row_to_comment(row) if row else None

    # ------------------------------------------------------------------
    # Thread walk
    # ------------------------------------------------------------------

    def list_thread(self, approval_id: str) -> list[ApprovalRequest]:
        """Return the full revision chain containing ``approval_id``.

        Walks ``predecessor_id`` backwards from the given row to the root,
        then walks forward via ``predecessor_id = this.id`` to find every
        successor. Result is oldest-first (root first).

        Defensive: caps the walk at :data:`_MAX_THREAD_HOPS` per direction to
        survive a malformed cycle (logged but not raised).
        """
        seed = self.get(approval_id)
        if seed is None:
            return []

        # Walk backwards to root.
        chain: list[ApprovalRequest] = [seed]
        visited: set[str] = {seed.id}
        current = seed
        for _ in range(_MAX_THREAD_HOPS):
            if not current.predecessor_id or current.predecessor_id in visited:
                break
            parent = self.get(current.predecessor_id)
            if parent is None:
                break
            chain.insert(0, parent)
            visited.add(parent.id)
            current = parent

        # Walk forward from the seed (and from every ancestor we collected
        # above) to find branches/successors. A single approval can be the
        # predecessor of multiple successors only via misuse; the loop
        # tolerates fan-out anyway.
        frontier = [c.id for c in chain]
        while frontier and len(visited) < _MAX_THREAD_HOPS * 2:
            next_frontier: list[str] = []
            for pid in frontier:
                rows = self.db.execute(
                    "SELECT * FROM approval_requests WHERE predecessor_id = ? "
                    "ORDER BY created_at ASC, rowid ASC",
                    (pid,),
                ).fetchall()
                for row in rows:
                    if row["id"] in visited:
                        continue
                    succ = self._row_to_request(row)
                    chain.append(succ)
                    visited.add(succ.id)
                    next_frontier.append(succ.id)
            frontier = next_frontier

        return chain

    # ------------------------------------------------------------------
    # Internals — helper updates
    # ------------------------------------------------------------------

    def update_payload(self, request_id: str, patch: dict) -> ApprovalRequest | None:
        """Merge ``patch`` into the request's payload without changing status."""
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

    def set_predecessor(
        self,
        request_id: str,
        predecessor_id: str | None,
    ) -> ApprovalRequest | None:
        """Stamp a ``predecessor_id`` link onto an existing approval.

        Used by the target feasibility revise flow: the re-review path
        creates a fresh approval first (so ``run_target_feasibility_review``
        stays callable from any entry point) then back-links it to the
        original via this helper.
        """
        request = self.get(request_id)
        if request is None:
            return None
        self.db.execute(
            "UPDATE approval_requests SET predecessor_id = ? WHERE id = ?",
            (predecessor_id, request_id),
        )
        self.db.commit()
        return self.get(request_id)

    def update_summary(
        self,
        request_id: str,
        summary: str,
    ) -> ApprovalRequest | None:
        """Replace an approval's summary line (e.g. add a ``[Revised]`` prefix)."""
        request = self.get(request_id)
        if request is None:
            return None
        self.db.execute(
            "UPDATE approval_requests SET summary = ? WHERE id = ?",
            (summary, request_id),
        )
        self.db.commit()
        return self.get(request_id)

    def list_due_snoozed(self) -> list[ApprovalRequest]:
        """Approvals whose snooze window has elapsed.

        Used by the watchdog's ``_scan_snoozed_approvals`` tick. Compares
        ``snoozed_until`` against SQLite's ``datetime('now')`` so test
        manipulations of ``snoozed_until`` (with relative offsets) round-trip
        correctly.
        """
        rows = self.db.execute(
            """SELECT * FROM approval_requests
               WHERE status = 'snoozed'
                 AND snoozed_until IS NOT NULL
                 AND snoozed_until <= datetime('now')
               ORDER BY snoozed_until ASC"""
        ).fetchall()
        return [self._row_to_request(row) for row in rows]

    # ------------------------------------------------------------------
    # Row mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_request(row) -> ApprovalRequest:
        # ``severity`` / ``predecessor_id`` / ``snoozed_until`` / ``snoozed_by``
        # may be absent on databases that pre-date the migration; fall back
        # to safe defaults via Row's ``keys()`` introspection.
        keys = set(row.keys())
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
            severity=row["severity"] if "severity" in keys and row["severity"] else "medium",
            predecessor_id=row["predecessor_id"] if "predecessor_id" in keys else None,
            snoozed_until=row["snoozed_until"] if "snoozed_until" in keys else None,
            snoozed_by=row["snoozed_by"] if "snoozed_by" in keys else None,
        )

    @staticmethod
    def _row_to_comment(row) -> ApprovalComment:
        return ApprovalComment(
            id=row["id"],
            approval_id=row["approval_id"],
            by_type=row["by_type"],
            by_id=row["by_id"],
            body=row["body"],
            created_at=row["created_at"],
        )
