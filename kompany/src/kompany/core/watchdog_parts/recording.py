"""Reactive-recording mixin for :class:`kompany.core.watchdog.Watchdog`.

Groups the ``record_*`` / ``resolve`` / ``list_open`` methods + the
``_mirror_audit`` helper so the main ``Watchdog`` class file stays under
500 lines.

Do **not** import from here directly — use ``kompany.core.watchdog``.
"""

from __future__ import annotations

import logging
from typing import Any

from kompany.core.event_hub import get_event_hub
from kompany.core.watchdog_parts.constants import (
    KIND_GLOSSARY_DRIFT_ALERT,
    KIND_RECOVERED,
    KIND_RETRY_EXHAUSTED,
    KIND_RUNWAY_ALERT,
    KIND_SILENT_RUN,
    KIND_STRANDED_IN_PROGRESS,
)
from kompany.state.audit import AuditLog
from kompany.state.health_events import HealthEvents

log = logging.getLogger(__name__)


class RecordingMixin:
    """Reactive health-event recording methods.

    Expects the host class to expose:
    - ``self.health_events: HealthEvents``
    - ``self.audit: AuditLog``
    """

    health_events: HealthEvents
    audit: AuditLog

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

    def record_runway_alert(
        self,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write one ``runway_alert`` health event.

        Exposed publicly so tests + the engine's manual ``targets review``
        path can fire an alert without going through the timed scanner.
        """
        event = self.health_events.record(
            kind=KIND_RUNWAY_ALERT,
            detail=detail or {},
        )
        self._mirror_audit("health.runway_alert", event)
        return event

    def record_glossary_drift(
        self,
        episode_id: str | None,
        drifts: list[Any],
        project_id: str | None = None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Write one ``glossary_drift_alert`` health event.

        Called from the CoS retrospective drift-scan hook (see
        :mod:`kompany.agents.cos_glossary_scan`). ``drifts`` is the
        list of :class:`DriftHit` Pydantic models the scanner produced
        — we serialise them through ``model_dump`` so the JSON column
        round-trips cleanly. ``approval_id`` carries the matching
        ``glossary_review`` approval request id (set after the engine
        creates the approval row).
        """
        serialised: list[dict[str, Any]] = []
        for hit in drifts:
            if hasattr(hit, "model_dump"):
                serialised.append(hit.model_dump(mode="json"))
            elif isinstance(hit, dict):
                serialised.append(hit)
        detail: dict[str, Any] = {
            "episode_id": episode_id,
            "drift_count": len(serialised),
            "drifts": serialised,
        }
        if approval_id is not None:
            detail["approval_id"] = approval_id
        event = self.health_events.record(
            kind=KIND_GLOSSARY_DRIFT_ALERT,
            project_id=project_id,
            detail=detail,
        )
        self._mirror_audit("health.glossary_drift_alert", event)
        return event

    # ------------------------------------------------------------------
    # Founder-facing convenience
    # ------------------------------------------------------------------

    def resolve(
        self,
        event_id: str,
        action: str,
        resolved_by: str = "player",
        snooze_minutes: int | None = None,
    ) -> dict[str, Any] | None:
        """Apply a founder decision to one event. See HealthEvents.resolve."""
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
