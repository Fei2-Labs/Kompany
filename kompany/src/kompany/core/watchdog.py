"""Resilience watchdog — stranded-task scanner + silent-run book-keeping.

Created by ``.trellis/tasks/05-18-resilience-foundation``. The watchdog is
**one engine-scoped singleton** that the :class:`KompanyEngine` owns. Two
responsibilities:

1. **Reactive** — :class:`kompany.llm.client.LLMClient` calls into
   :meth:`Watchdog.record_silent_run` / :meth:`Watchdog.record_recovered`
   / :meth:`Watchdog.record_retry_exhausted` from its retry wrapper. The
   watchdog only writes ``health_events`` rows; it never kills the
   in-flight LLM call (paperclip-validated: a slow call that eventually
   succeeds is much better than a force-killed one).

2. **Proactive** — :meth:`start` launches an ``asyncio.Task`` that, every
   ``stranded_scan_interval_seconds``, looks for ``in_progress`` tasks
   whose ``updated_at`` is older than ``task_stale_threshold_seconds``
   and flips them to ``stranded_in_progress`` + writes a health event.
   :meth:`stop` cancels that task and awaits it with
   ``contextlib.suppress(asyncio.CancelledError)`` so engine shutdown
   never hangs.

The watchdog is intentionally **stateless** beyond its asyncio task
handle — every fact lives in the database.

Single-process design
---------------------
This is a single-process runtime; there is no cross-machine leader
election, no heartbeat ping, no orphan-run reaper. paperclip's complexity
on those fronts is explicitly rejected (see PRD's "Non-goals").
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Callable

from kompany.core.event_hub import get_event_hub
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.health_events import HealthEvents
from kompany.state.projects import Projects

log = logging.getLogger(__name__)


# Sentinel kinds the LLM wrapper writes. Re-exported for callers that
# want to avoid magic strings.
KIND_SILENT_RUN = "silent_run"
KIND_RECOVERED = "recovered"
KIND_RETRY_EXHAUSTED = "retry_exhausted"
KIND_STRANDED_IN_PROGRESS = "stranded_in_progress"
KIND_STRANDED_TODO = "stranded_todo"


class Watchdog:
    """Engine-scoped resilience supervisor."""

    def __init__(
        self,
        health_events: HealthEvents,
        projects: Projects,
        audit: AuditLog,
        scan_interval_seconds: int = 60,
        stale_threshold_seconds: int = 600,
        clock: Callable[[], float] | None = None,
        approvals: ApprovalRequests | None = None,
    ):
        self.health_events = health_events
        self.projects = projects
        self.audit = audit
        # Approvals is optional so legacy tests that construct a watchdog
        # without an approvals store keep working. The snooze scanner
        # silently no-ops when ``approvals`` is None.
        self.approvals = approvals
        self.scan_interval_seconds = max(1, int(scan_interval_seconds))
        self.stale_threshold_seconds = max(1, int(stale_threshold_seconds))
        # ``clock`` is only used by tests that want to run scans without
        # waiting on the asyncio sleep. Production keeps it None and the
        # background task uses ``asyncio.sleep``.
        self._clock = clock
        self._scan_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the periodic stranded scanner if not already running.

        Safe to call multiple times — the second call is a no-op. The
        caller must already be inside a running asyncio loop; the engine
        wires this from its async entry points.
        """
        if self._scan_task is not None and not self._scan_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("watchdog.start called outside a running loop; deferring")
            return
        self._stopped.clear()
        self._scan_task = loop.create_task(self._run_scanner_loop())

    async def stop(self) -> None:
        """Cancel and await the scanner task. Idempotent."""
        task = self._scan_task
        self._scan_task = None
        self._stopped.set()
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ------------------------------------------------------------------
    # Reactive recording — called by LLMClient + engine
    # ------------------------------------------------------------------

    def record_silent_run(
        self,
        task_id: str | None = None,
        project_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Note that an LLM call has gone silent past its timeout.

        The wrapper writes this **without** killing the call — the
        in-flight call gets one more chance to complete on its own. If it
        eventually succeeds, :meth:`record_recovered` is called and this
        event is resolved.
        """
        event = self.health_events.record(
            kind=KIND_SILENT_RUN,
            task_id=task_id,
            project_id=project_id,
            detail=detail or {},
        )
        self._mirror_audit("health.silent_run", event)
        return event

    def record_recovered(
        self,
        task_id: str | None = None,
        project_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Note that a previously-silent LLM call has come back alive.

        Also closes any matching open ``silent_run`` rows for the same
        (task_id, run_id) so the operator UI doesn't show stale red
        warnings.
        """
        # The recovered event uses the *current* run_id; we read it
        # implicitly via HealthEvents.record's current_run_id() fallback.
        event = self.health_events.record(
            kind=KIND_RECOVERED,
            task_id=task_id,
            project_id=project_id,
            detail=detail or {},
        )
        # Close any open silent_run rows in the same run.
        from kompany.core.run_context import current_run_id
        closed = self.health_events.close_open(
            kind=KIND_SILENT_RUN,
            task_id=task_id,
            run_id=current_run_id(),
        )
        if closed:
            log.debug("watchdog.record_recovered closed %d open silent_run rows", closed)
        self._mirror_audit("health.recovered", event)
        return event

    def record_retry_exhausted(
        self,
        task_id: str | None = None,
        project_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Note that the single retry also failed.

        Engine catches the resulting ``LLMUnavailable`` and transitions the
        owning task to ``stranded_in_progress``. This row is written
        regardless of whether the engine can identify the task.
        """
        event = self.health_events.record(
            kind=KIND_RETRY_EXHAUSTED,
            task_id=task_id,
            project_id=project_id,
            detail=detail or {},
        )
        self._mirror_audit("health.retry_exhausted", event)
        return event

    def record_stranded_in_progress(
        self,
        task_id: str | None = None,
        project_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Note that a task has been forgotten while ``in_progress``."""
        event = self.health_events.record(
            kind=KIND_STRANDED_IN_PROGRESS,
            task_id=task_id,
            project_id=project_id,
            detail=detail or {},
        )
        self._mirror_audit("health.stranded_in_progress", event)
        return event

    # ------------------------------------------------------------------
    # Player-facing convenience
    # ------------------------------------------------------------------

    def resolve(
        self,
        event_id: str,
        action: str,
        resolved_by: str = "player",
        snooze_minutes: int | None = None,
    ) -> dict[str, Any] | None:
        """Apply a player decision to one event. See HealthEvents.resolve."""
        result = self.health_events.resolve(
            event_id=event_id,
            action=action,
            resolved_by=resolved_by,
            snooze_minutes=snooze_minutes,
        )
        if result is not None:
            self._mirror_audit("health.resolved", result, extra={"action": action})
        return result

    def list_open(self) -> list[dict[str, Any]]:
        return self.health_events.list(status="open")

    # ------------------------------------------------------------------
    # Scanner — proactive sweep
    # ------------------------------------------------------------------

    def scan_once(self) -> list[dict[str, Any]]:
        """One stranded-task sweep. Returns the events written this pass.

        Public so tests can drive it without waiting for the asyncio
        timer. Safe to call from any thread/task. Also drives the
        snooze-expiry sweep for approval requests — its return value is
        intentionally only the stranded-task events, so existing callers
        and tests keep their assertion shape.
        """
        # Approval snooze expiry sweep: separate try/except so a failure
        # here cannot break stranded-task detection (the more critical
        # of the two).
        try:
            self._scan_snoozed_approvals()
        except Exception:  # noqa: BLE001
            log.exception("watchdog._scan_snoozed_approvals failed")

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
            event = self.record_stranded_in_progress(
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
                self.audit.record(
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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mirror_audit(
        self,
        event_type: str,
        event_row: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Mirror a health event into audit_log for the timeline view.

        Failures are swallowed so a broken audit table never affects the
        watchdog's actual book-keeping in ``health_events``.
        """
        try:
            detail = {
                "event_id": event_row.get("id"),
                "kind": event_row.get("kind"),
                "task_id": event_row.get("task_id"),
                "status": event_row.get("status"),
            }
            if extra:
                detail.update(extra)
            self.audit.record(
                event_type=event_type,
                action=f"watchdog: {event_row.get('kind')}",
                detail=detail,
                project_id=event_row.get("project_id"),
                run_id=event_row.get("run_id"),
            )
        except Exception:  # noqa: BLE001
            log.debug("watchdog: audit mirror failed", exc_info=True)

        # Also fan out as a dedicated health.event so the UI's health
        # panel can react without filtering the general audit stream.
        try:
            get_event_hub().publish(
                "health.event",
                {
                    "event_type": event_type,
                    "event_id": event_row.get("id"),
                    "kind": event_row.get("kind"),
                    "status": event_row.get("status"),
                    "task_id": event_row.get("task_id"),
                    "project_id": event_row.get("project_id"),
                    "run_id": event_row.get("run_id"),
                },
            )
        except Exception:  # pragma: no cover — best-effort
            pass


class LLMUnavailable(RuntimeError):
    """Raised by :class:`kompany.llm.client.LLMClient` when both the
    primary call and the single retry fail.

    The engine catches this to transition the owning task to
    ``stranded_in_progress`` and emit a ``retry_exhausted`` event.
    """


__all__ = [
    "LLMUnavailable",
    "Watchdog",
    "KIND_RECOVERED",
    "KIND_RETRY_EXHAUSTED",
    "KIND_SILENT_RUN",
    "KIND_STRANDED_IN_PROGRESS",
    "KIND_STRANDED_TODO",
]
