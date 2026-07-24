"""Resilience watchdog — stranded-task scanner + silent-run book-keeping.

Created by the internal design spec. The watchdog is
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

Implementation note
-------------------
The ``Watchdog`` class is split across two mixins (in
:mod:`kompany.core.watchdog_parts`) to stay within the 500-line-per-file
limit imposed by ADR-0003. All public symbols remain importable from
this module — do **not** import from the sub-package directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Callable

from kompany.core.watchdog_parts.constants import (
    KIND_GLOSSARY_DRIFT_ALERT,
    KIND_RECOVERED,
    KIND_RETRY_EXHAUSTED,
    KIND_RUNWAY_ALERT,
    KIND_SILENT_RUN,
    KIND_STRANDED_IN_PROGRESS,
    KIND_STRANDED_TODO,
    LLMUnavailable,
)
from kompany.core.watchdog_parts.recording import RecordingMixin
from kompany.core.watchdog_parts.scanning import ScanningMixin
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.health_events import HealthEvents
from kompany.state.projects import Projects

log = logging.getLogger(__name__)


class Watchdog(RecordingMixin, ScanningMixin):
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
        runway_provider: Callable[[], dict[str, Any] | None] | None = None,
        agent_status: AgentStatusStore | None = None,
    ):
        self.health_events = health_events
        self.projects = projects
        self.audit = audit
        # ``agent_status`` is optional so legacy tests that construct a
        # watchdog without it keep working; ``reconcile_on_startup`` is
        # the only caller and simply skips the agent-status reset when
        # it is None.
        self.agent_status = agent_status
        # Approvals is optional so legacy tests that construct a watchdog
        # without an approvals store keep working. The snooze scanner
        # silently no-ops when ``approvals`` is None.
        self.approvals = approvals
        # ``runway_provider`` returns a dict with keys ``cash``, ``burn_rate``
        # (USD/hour), ``deadline`` (ISO 8601 string or None), ``targets``
        # (raw model_dump). Returning ``None`` disables the scan for one
        # tick. We use a callable rather than direct ledger/engine refs
        # so this module stays free of upward dependencies.
        self.runway_provider = runway_provider
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


__all__ = [
    "LLMUnavailable",
    "Watchdog",
    "KIND_GLOSSARY_DRIFT_ALERT",
    "KIND_RECOVERED",
    "KIND_RETRY_EXHAUSTED",
    "KIND_RUNWAY_ALERT",
    "KIND_SILENT_RUN",
    "KIND_STRANDED_IN_PROGRESS",
    "KIND_STRANDED_TODO",
]
