"""Lane registry — the concurrency-without-interference layer (ADR-0005).

The daemon ticks one task at a time today. ADR-0005 extends it into a
dispatcher over N independent lane-workers; this registry owns the
``lanes`` + ``lane_leases`` tables that make that safe:

* :meth:`ensure_default` seeds a single ``main`` lane (max_concurrent=1)
  when none exist, so a fresh / upgraded engine behaves exactly like the
  pre-lane ticker.
* :meth:`acquire_lease` enforces the lane-worker contract's "own
  lease/lock — never re-enters / double-runs": at most ONE unexpired
  lease per lane. A second acquire while a lease is live is rejected;
  it succeeds again only after expiry or :meth:`release_lease`.

The clock is injectable (Watchdog precedent) so lease-expiry tests run
without real waiting.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from kompany.state.database import Database

DEFAULT_LANE_ID = "main"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LaneRegistry:
    """SQLite-backed registry of lanes and their leases."""

    def __init__(self, db: Database, clock: Callable[[], datetime] | None = None):
        self.db = db
        # ``clock`` returns an aware UTC datetime. Injectable so tests can
        # advance time across a lease TTL without sleeping.
        self._clock = clock if clock is not None else _utcnow

    # ------------------------------------------------------------------
    # Lane lifecycle
    # ------------------------------------------------------------------

    def ensure_default(self) -> None:
        """Create the ``main`` lane (max_concurrent=1) when no lane exists.

        Idempotent: a no-op once any lane is present, so existing
        single-lane behaviour is preserved across restarts.
        """
        row = self.db.execute("SELECT COUNT(*) AS n FROM lanes").fetchone()
        if int(row["n"] or 0) > 0:
            return
        self.db.execute(
            """INSERT INTO lanes (lane_id, name, status, max_concurrent)
               VALUES (?, ?, 'healthy', 1)""",
            (DEFAULT_LANE_ID, "main"),
        )
        self.db.commit()

    def list_healthy(self) -> list[dict[str, Any]]:
        """All lanes with status ``healthy`` (creation order)."""
        rows = self.db.execute(
            "SELECT * FROM lanes WHERE status = 'healthy' "
            "ORDER BY created_at ASC, lane_id ASC"
        ).fetchall()
        return [self._decode(dict(r)) for r in rows]

    def get(self, lane_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM lanes WHERE lane_id = ?", (lane_id,)
        ).fetchone()
        return self._decode(dict(row)) if row else None

    def mark_stalled(self, lane_id: str, reason: str) -> None:
        """Flag a lane ``stalled`` so the dispatcher stops scheduling it."""
        self.db.execute(
            """UPDATE lanes
                   SET status = 'stalled',
                       detail_json = ?
               WHERE lane_id = ?""",
            (json.dumps({"reason": reason}), lane_id),
        )
        self.db.commit()

    # ------------------------------------------------------------------
    # Leases (own-lock / no double-run)
    # ------------------------------------------------------------------

    def acquire_lease(
        self,
        lane_id: str,
        task_id: str | None,
        run_id: str | None,
        ttl_seconds: int,
    ) -> bool:
        """Acquire the lane's single lease. Returns False if one is live.

        One unexpired lease per lane — the no-double-run guard. An expired
        lease (or none) is replaced; a still-valid one rejects the
        acquire so a second worker cannot re-enter the same lane.
        """
        now = self._clock()
        existing = self.db.execute(
            "SELECT expires_at FROM lane_leases WHERE lane_id = ?", (lane_id,)
        ).fetchone()
        if existing is not None and not self._is_expired(existing["expires_at"], now):
            return False
        expires = (now + timedelta(seconds=int(ttl_seconds))).isoformat()
        iso_now = now.isoformat()
        # REPLACE so an expired row is overwritten atomically.
        self.db.execute(
            """INSERT OR REPLACE INTO lane_leases
                   (lane_id, task_id, run_id, acquired_at, heartbeat_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (lane_id, task_id, run_id, iso_now, iso_now, expires),
        )
        self.db.commit()
        return True

    def release_lease(self, lane_id: str) -> None:
        """Drop the lane's lease so the next dispatch can re-acquire it."""
        self.db.execute(
            "DELETE FROM lane_leases WHERE lane_id = ?", (lane_id,)
        )
        self.db.commit()

    def record_heartbeat(self, lane_id: str) -> None:
        """Stamp liveness on both the lease and the lane row."""
        iso_now = self._clock().isoformat()
        self.db.execute(
            "UPDATE lane_leases SET heartbeat_at = ? WHERE lane_id = ?",
            (iso_now, lane_id),
        )
        self.db.execute(
            "UPDATE lanes SET last_heartbeat = ? WHERE lane_id = ?",
            (iso_now, lane_id),
        )
        self.db.commit()

    def has_active_lease(self, lane_id: str) -> bool:
        row = self.db.execute(
            "SELECT expires_at FROM lane_leases WHERE lane_id = ?", (lane_id,)
        ).fetchone()
        if row is None:
            return False
        return not self._is_expired(row["expires_at"], self._clock())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _is_expired(expires_at: str | None, now: datetime) -> bool:
        if not expires_at:
            return True
        try:
            exp = datetime.fromisoformat(expires_at)
        except ValueError:
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return now >= exp

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("detail_json")
        if raw:
            try:
                row["detail"] = json.loads(raw)
            except (TypeError, ValueError):
                row["detail"] = None
        else:
            row["detail"] = None
        return row


__all__ = ["LaneRegistry", "DEFAULT_LANE_ID"]
