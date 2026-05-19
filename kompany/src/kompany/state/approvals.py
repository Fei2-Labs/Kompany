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
"""

from __future__ import annotations

import json
from typing import Any

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import (
    APPROVAL_SEVERITIES,
    APPROVAL_TERMINAL_STATUSES,
    ApprovalComment,
    ApprovalRequest,
    ApprovalStatus,
)


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


class ApprovalRequests:
    """Stores pending and resolved user approval requests."""

    def __init__(self, db: Database):
        self.db = db

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
        return self.get(request_id)

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
    # Internals
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


__all__ = [
    "ApprovalRequests",
    "COMMENT_BY_TYPES",
    "IllegalApprovalTransition",
]
