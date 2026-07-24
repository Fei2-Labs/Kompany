"""Proactive scanner mixin for :class:`kompany.core.watchdog.Watchdog`.

Groups ``scan_once``, the approval snooze sweep, the runway scan, and
the background asyncio loop so the main module file stays under 500 lines.

Do **not** import from here directly — use ``kompany.core.watchdog``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from kompany.core.watchdog_parts.constants import (
    KIND_RUNWAY_ALERT,
    KIND_STRANDED_IN_PROGRESS,
)
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.health_events import HealthEvents
from kompany.state.projects import Projects

log = logging.getLogger(__name__)


class ScanningMixin:
    """Proactive scanner methods.

    Expects the host class to expose:
    - ``self.health_events: HealthEvents``
    - ``self.projects: Projects``
    - ``self.agent_status: AgentStatusStore``
    - ``self.approvals: ApprovalRequests | None``
    - ``self.runway_provider: Callable | None``
    - ``self.scan_interval_seconds: int``
    - ``self.stale_threshold_seconds: int``
    - ``self._stopped: asyncio.Event``
    - ``self.record_stranded_in_progress(...)``
    - ``self.record_runway_alert(...)``
    """

    health_events: HealthEvents
    projects: Projects
    agent_status: "AgentStatusStore | None"
    approvals: "ApprovalRequests | None"
    runway_provider: "Callable[[], dict[str, Any] | None] | None"
    scan_interval_seconds: int
    stale_threshold_seconds: int
    _stopped: asyncio.Event

    # ------------------------------------------------------------------
    # Scanner — proactive sweep
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Startup reconciliation (Stage A deployment plan: session-persistence)
    # ------------------------------------------------------------------

    def reconcile_on_startup(self) -> dict[str, Any]:
        """One-shot reconciliation for a fresh process boot.

        Unlike the periodic ``scan_once`` sweep (staleness-threshold
        based, meant to catch a task an *already-running* process
        silently dropped), this runs exactly once when the daemon
        actually starts. At that moment nothing in the new process has
        executed a single tool call yet, so ANY task still marked
        ``active``/``in_progress`` and ANY ``agent_status`` row still
        showing ``working``/``thinking``/``dispatching`` is provably
        orphaned from a previous process that crashed, was killed, or
        was hard-restarted without a clean drain/suspend. Call this from
        the engine's real daemon entry point (``Engine.start()``), never
        from a one-shot CLI ``KompanyEngine()`` construction — CLI
        commands run alongside a live daemon and must not stomp on its
        genuinely active work.
        """
        stranded: list[dict[str, Any]] = []
        for task in self.projects.list_active_tasks():
            existing = self.health_events.find_active_snoozed(
                kind=KIND_STRANDED_IN_PROGRESS,
                task_id=task.id,
            )
            if existing is not None:
                continue
            try:
                self.projects.update_task_status_raw(
                    task_id=task.id,
                    status=KIND_STRANDED_IN_PROGRESS,
                )
            except Exception as exc:  # defensive — db errors don't kill boot
                log.warning(
                    "watchdog startup reconciliation: failed to mark task %s stranded: %s",
                    task.id,
                    exc,
                )
                continue
            event = self.record_stranded_in_progress(  # type: ignore[attr-defined]
                task_id=task.id,
                project_id=task.project_id,
                detail={
                    "title": task.title,
                    "assigned_agent": task.assigned_agent,
                    "reason": "startup_reconciliation",
                },
            )
            stranded.append(event)

        reset_agents: list[dict[str, Any]] = []
        if self.agent_status is not None:
            reset_agents = self.agent_status.reset_all_working_to_idle()
        if reset_agents:
            try:
                self.audit.record(
                    "agent_status.startup_reset",
                    f"Reset {len(reset_agents)} stale agent_status row(s) to idle on boot",
                    detail={
                        "agents": [row["agent_role"] for row in reset_agents],
                    },
                )
            except Exception:  # noqa: BLE001 — audit mirror is best-effort
                log.debug("watchdog: audit mirror for agent_status reset failed", exc_info=True)

        return {"stranded_tasks": stranded, "reset_agents": reset_agents}

    def scan_once(self) -> list[dict[str, Any]]:
        """One stranded-task sweep. Returns the events written this pass.

        Public so tests can drive it without waiting for the asyncio
        timer. Safe to call from any thread/task. Also drives the
        snooze-expiry sweep for approval requests + the runway scan —
        its return value is intentionally only the stranded-task events,
        so existing callers and tests keep their assertion shape.
        """
        # Approval snooze expiry sweep: separate try/except so a failure
        # here cannot break stranded-task detection (the more critical
        # of the two).
        try:
            self._scan_snoozed_approvals()
        except Exception:  # noqa: BLE001
            log.exception("watchdog._scan_snoozed_approvals failed")

        # Runway scan: same defensive boundary. Mission-targets task
        # (05-19) added this so a deadline-vs-burn check runs every
        # scanner tick.
        try:
            self._scan_runway()
        except Exception:  # noqa: BLE001
            log.exception("watchdog._scan_runway failed")

        stale_tasks = self.projects.list_stale_in_progress(
            stale_seconds=self.stale_threshold_seconds
        )
        emitted: list[dict[str, Any]] = []
        for task in stale_tasks:
            # Skip tasks already covered by a still-active snooze on
            # stranded_in_progress for this task.
            existing = self.health_events.find_active_snoozed(
                kind=KIND_STRANDED_IN_PROGRESS,
                task_id=task.id,
            )
            if existing is not None:
                continue
            # Flip the task status and write the event.
            try:
                self.projects.update_task_status_raw(
                    task_id=task.id,
                    status=KIND_STRANDED_IN_PROGRESS,
                )
            except Exception as exc:  # defensive — db errors don't kill scanner
                log.warning("watchdog: failed to mark task %s stranded: %s", task.id, exc)
                continue
            event = self.record_stranded_in_progress(  # type: ignore[attr-defined]
                task_id=task.id,
                project_id=task.project_id,
                detail={
                    "title": task.title,
                    "assigned_agent": task.assigned_agent,
                    "stale_threshold_seconds": self.stale_threshold_seconds,
                },
            )
            emitted.append(event)
        return emitted

    def _scan_snoozed_approvals(self) -> list[dict[str, Any]]:
        """Flip every snooze-expired approval back to ``pending``.

        Returns the list of approval rows that were just transitioned, so
        tests can assert on the side-effects without re-reading the table.
        No-ops when no approvals store is wired in (legacy constructor
        path used by some unit tests).

        The system comment records the original snooze window in
        minutes — recovered by scanning the most recent user comment
        whose body begins with ``"snoozed for "`` (the canonical body
        ``ApprovalRequests.snooze`` writes). This is durable: even if
        the player swaps the comment via the ``--comment`` flag, the
        snooze method *also* writes its own ``"snoozed for Nm"`` line.
        """
        if self.approvals is None:
            return []
        unsnoozed: list[dict[str, Any]] = []
        for approval in self.approvals.list_due_snoozed():
            duration_label = self._lookup_snooze_window(approval.id)
            back = self.approvals.unsnooze(approval.id, by="system")
            if back is None:
                continue
            self.approvals.add_comment(
                approval_id=approval.id,
                body=f"auto-unsnoozed after {duration_label}",
                by_type="system",
                by_id=None,
            )
            try:
                self.audit.record(  # type: ignore[attr-defined]
                    event_type="approval.auto_unsnoozed",
                    action=f"watchdog: auto-unsnoozed approval {approval.id}",
                    detail={
                        "approval_id": approval.id,
                        "action_type": approval.action_type,
                        "duration": duration_label,
                    },
                    project_id=approval.project_id,
                    directive_id=approval.directive_id,
                )
            except Exception:  # noqa: BLE001
                log.debug("watchdog: audit mirror for unsnooze failed", exc_info=True)
            unsnoozed.append(back.model_dump(mode="json"))
        return unsnoozed

    def _lookup_snooze_window(self, approval_id: str) -> str:
        """Best-effort recovery of the original snooze duration label.

        Looks for the most recent ``"snoozed for Nm"`` comment so we can
        print "auto-unsnoozed after 30m". Falls back to a generic label
        when the comment is missing (e.g. third-party DB writes).
        """
        if self.approvals is None:
            return "snooze window"
        try:
            comments = self.approvals.list_comments(approval_id)
        except Exception:  # noqa: BLE001
            return "snooze window"
        for comment in reversed(comments):
            body = comment.body or ""
            if body.startswith("snoozed for ") and body.endswith("m"):
                # Trim leading "snoozed for " — keep the "Nm" suffix.
                return body[len("snoozed for "):]
        return "snooze window"

    # ------------------------------------------------------------------
    # Runway scan (mission-targets task 05-19)
    # ------------------------------------------------------------------

    def _scan_runway(self) -> dict[str, Any] | None:
        """Check whether projected burn through ``deadline`` exceeds cash.

        Hot loop:

        1. Pull ``cash`` + ``burn_rate`` + ``deadline`` + raw ``targets``
           from :attr:`runway_provider`. ``None`` disables the scan for
           this tick (engine hasn't wired it yet, no agreed targets
           exist, ledger unavailable, etc.).
        2. Skip when ``burn_rate <= 0`` (not enough signal yet) or no
           deadline is set.
        3. Compute projected burn = ``burn_rate * hours_remaining``.
           If that exceeds ``cash`` AND there isn't already an open
           ``runway_alert`` row, write one.

        Returns the freshly-written event row, or ``None`` if nothing was
        emitted this tick.
        """
        if self.runway_provider is None:
            return None
        try:
            snapshot = self.runway_provider()
        except Exception:  # noqa: BLE001
            log.debug("watchdog.runway_provider raised", exc_info=True)
            return None
        if not snapshot:
            return None
        try:
            cash = float(snapshot.get("cash") or 0.0)
            burn_rate = float(snapshot.get("burn_rate") or 0.0)
        except (TypeError, ValueError):
            return None
        deadline_raw = snapshot.get("deadline")
        if not deadline_raw or burn_rate <= 0:
            return None
        from datetime import datetime, timezone  # noqa: F401

        try:
            deadline = datetime.fromisoformat(str(deadline_raw))
        except (TypeError, ValueError):
            return None
        now = datetime.now(deadline.tzinfo) if deadline.tzinfo else datetime.utcnow()
        try:
            hours_remaining = (deadline - now).total_seconds() / 3600.0
        except TypeError:
            return None
        # Already past deadline → no actionable alert (the watchdog can't
        # un-spend money). A separate "deadline expired" signal can be
        # added later; v1 stays narrowly scoped.
        if hours_remaining <= 0:
            return None
        projected_burn = burn_rate * hours_remaining
        if projected_burn <= cash:
            return None
        # Skip if an open alert already exists — we don't want to spam
        # the inbox each scanner tick. Match by ``(kind, task_id=None)``
        # because runway alerts are company-scoped, not task-scoped.
        existing = self.health_events.list(kind=KIND_RUNWAY_ALERT, status="open", limit=1)
        if existing:
            return None
        detail: dict[str, Any] = {
            "cash": cash,
            "burn_rate_per_hour": burn_rate,
            "hours_remaining": hours_remaining,
            "projected_burn": projected_burn,
            "deadline": str(deadline_raw),
        }
        targets = snapshot.get("targets")
        if isinstance(targets, dict):
            detail["targets"] = targets
        return self.record_runway_alert(detail=detail)  # type: ignore[attr-defined]

    async def _run_scanner_loop(self) -> None:
        """Background task: sleep + scan + repeat until cancelled."""
        log.debug(
            "watchdog scanner loop started (interval=%ds, stale=%ds)",
            self.scan_interval_seconds,
            self.stale_threshold_seconds,
        )
        try:
            while not self._stopped.is_set():
                try:
                    self.scan_once()
                except Exception:  # noqa: BLE001 — never let a scan kill the loop
                    log.exception("watchdog scan_once failed")
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(),
                        timeout=self.scan_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
        except asyncio.CancelledError:
            log.debug("watchdog scanner loop cancelled")
            raise
