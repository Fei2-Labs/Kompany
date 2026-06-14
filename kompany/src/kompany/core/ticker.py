"""Ticker — the engine's autonomous heartbeat (daemon tick loop).

PRD ``06-12-daemon-tick-loop`` D1: the ticker lives **in the engine**,
not in a separate scheduler process — any host that boots the engine's
background workers (sidecar server, daemon server) gets ticking for
free. It follows the :class:`kompany.core.watchdog.Watchdog` pattern
exactly: asyncio task, injectable sleeper/clock for tests, idempotent
``start``, ``contextlib.suppress(CancelledError)`` stop.

Tick actions (PRD D3, strictly bounded, each individually guarded):

1. **Runtime gate** — runtime suspended → record an idle tick, nothing
   else runs (the founder brake is ``kompany runtime suspend``).
2. **Heartbeat** — ``engine.heartbeat_once()`` (pending approvals,
   active projects, monthly subscription fee booking). Reused, never
   duplicated.
3. **Advance work** — at most ONE pending task of ONE active project
   per tick via :meth:`kompany.core.runner.ProjectRunner.run_one_pending`
   (envelope guards, per-task caps, permission gate already enforce
   spend safety). Gated by ``daemon_auto_execute``; a project with a
   pending ``project_envelope_topup`` / ``harness_budget_increase``
   approval is skipped — don't grind against a closed gate.
4. **Housekeeping** — ``daemon_ticks.prune(keep=500)`` plus the EXISTING
   episodes retention hook (``engine.episodes.trim_to_retention_window``
   with the ``episode_retention_full_count`` config, the same call the
   engine's episode materialization path uses). Approval auto-unsnooze
   already runs every Watchdog scanner pass
   (``Watchdog._scan_snoozed_approvals``) — deliberately NOT duplicated
   here (code-reuse guide).
5. **Record** — one ``daemon_ticks`` row + an SSE ``daemon.tick`` event
   with ``activity_kind: "tick"`` (additive to the activity contract).

``self.actions`` is an ordered ``(name, callable)`` list so PRD 3/4
persona intents (emotion, diary, posting) can append their own steps
without touching the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from kompany.core.event_hub import get_event_hub
from kompany.core.harness_execution import (
    ACTION_BUDGET_INCREASE,
    ACTION_ENVELOPE_TOPUP,
)
from kompany.state.daemon_ticks import DaemonTickStore
from kompany.state.models import TaskStatus

log = logging.getLogger(__name__)

# Tick history retention (PRD: keep the last 500 ticks).
TICK_HISTORY_KEEP = 500


def _status_value(status: Any) -> str:
    return status.value if isinstance(status, TaskStatus) else str(status)


class Ticker:
    """Engine-scoped 24/7 tick loop (Watchdog precedent)."""

    def __init__(
        self,
        engine: Any,
        ticks: DaemonTickStore,
        tick_interval_seconds: int = 300,
        auto_execute: bool = True,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
        hub: Any = None,
    ):
        self._engine = engine
        self._ticks = ticks
        self.tick_interval_seconds = max(1, int(tick_interval_seconds))
        self.auto_execute = bool(auto_execute)
        # ``sleeper``/``clock`` are injectable so tests can drive the loop
        # without real waiting (watchdog style). Production keeps the
        # defaults: ``asyncio.sleep`` + ``time.monotonic``.
        self._sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._clock = clock if clock is not None else time.monotonic
        self._hub = hub if hub is not None else get_event_hub()
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self.last_tick_at: str | None = None
        self.tick_count: int = 0
        # Ordered tick steps — PRD 3/4 persona intents extend this list.
        self.actions: list[tuple[str, Callable[[], list[str]]]] = [
            ("heartbeat", self._action_heartbeat),
            ("advance", self._action_advance),
            ("housekeeping", self._action_housekeeping),
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the tick loop if not already running. Idempotent.

        The caller must already be inside a running asyncio loop; the
        engine wires this from its async entry points.
        """
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("ticker.start called outside a running loop; deferring")
            return
        self._stopped.clear()
        self._task = loop.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel and await the tick loop. Idempotent."""
        task = self._task
        self._task = None
        self._stopped.set()
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # One tick
    # ------------------------------------------------------------------

    def tick_once(self) -> dict[str, Any]:
        """Run one full tick pass synchronously. Returns the tick row.

        Public so tests (and later surfaces) can drive a tick without
        the asyncio timer — same contract as ``Watchdog.scan_once``.
        """
        started_at = datetime.now(UTC).isoformat()
        t0 = self._clock()
        actions: list[str] = []
        errors: dict[str, str] = {}
        runtime = self._engine.runtime.get() or {}
        if runtime.get("state") == "suspended":
            outcome = "idle_suspended"
        else:
            for name, action in self.actions:
                try:
                    actions.extend(action() or [])
                except Exception as exc:  # noqa: BLE001 — one step must not kill the tick
                    log.exception("ticker action %r failed", name)
                    actions.append(f"{name}:error")
                    errors[name] = str(exc)
            outcome = "error" if errors else "ok"
        duration_ms = int(max(0.0, self._clock() - t0) * 1000)
        row = self._ticks.record(
            started_at=started_at,
            duration_ms=duration_ms,
            actions=actions,
            outcome=outcome,
            detail={"errors": errors} if errors else None,
        )
        self.last_tick_at = started_at
        self.tick_count += 1
        self._publish(outcome, actions, duration_ms)
        return row

    # ------------------------------------------------------------------
    # Tick actions (D3 order)
    # ------------------------------------------------------------------

    def _action_heartbeat(self) -> list[str]:
        """Existing engine heartbeat: approvals, projects, monthly fee."""
        self._engine.heartbeat_once()
        return ["heartbeat"]

    def _action_advance(self) -> list[str]:
        """Advance AT MOST one pending task of the oldest eligible project.

        ADR-0005: when the engine wires a lane dispatcher, delegate to it
        so several independent lanes can advance per tick. With the single
        default ``main`` lane the dispatcher reproduces this method's
        oldest-eligible-project, one-task-per-tick behaviour exactly — the
        legacy path below stays in place for engines without a dispatcher.
        """
        if not self.auto_execute:
            return []
        dispatcher = getattr(self._engine, "lane_dispatcher", None)
        if dispatcher is not None:
            return dispatcher.dispatch_once()
        actions: list[str] = []
        blocked = self._projects_awaiting_budget_approval()
        candidates = sorted(
            self._engine.projects.list_active(), key=lambda p: p.created_at
        )
        for project in candidates:
            tasks = self._engine.projects.list_tasks(project.id)
            # A project with NO tasks at all stays eligible: the slice
            # decomposes-if-empty (run_one_pending), so the ticker can
            # START new projects, not only continue kicked-off ones. Only
            # skip when tasks exist but none is pending (all done/failed).
            if tasks and not any(
                _status_value(t.status) == TaskStatus.PENDING.value
                for t in tasks
            ):
                continue
            if project.id in blocked:
                actions.append(f"skipped_pending_approval:{project.id}")
                continue
            task_id = self._run_one_pending(project.id)
            if task_id:
                actions.append(f"advanced_task:{task_id}")
                return actions
        if not actions:
            actions.append("no_work")
        return actions

    def _action_housekeeping(self) -> list[str]:
        """Prune tick history + reuse the existing episodes retention hook."""
        actions: list[str] = []
        removed = self._ticks.prune(keep=TICK_HISTORY_KEEP)
        if removed:
            actions.append(f"pruned_ticks:{removed}")
        episodes = getattr(self._engine, "episodes", None)
        trim = getattr(episodes, "trim_to_retention_window", None)
        if callable(trim):
            get_cfg = getattr(self._engine, "_get_int_config", None)
            max_full = (
                get_cfg("episode_retention_full_count", default=50)
                if callable(get_cfg)
                else 50
            )
            trimmed = trim(max_full)
            if trimmed:
                actions.append(f"episodes_trimmed:{len(trimmed)}")
        return actions

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _projects_awaiting_budget_approval(self) -> set[str]:
        """Project ids with an open top-up / budget-increase approval."""
        blocking = {ACTION_ENVELOPE_TOPUP, ACTION_BUDGET_INCREASE}
        return {
            req.project_id
            for req in self._engine.approvals.list_pending()
            if req.action_type in blocking and req.project_id
        }

    def _run_one_pending(self, project_id: str) -> str | None:
        # Lazy import mirrors engine.py's own ProjectRunner imports and
        # keeps core/ticker.py free of an import-time runner dependency.
        from kompany.core.runner import ProjectRunner

        return ProjectRunner(self._engine).run_one_pending(project_id)

    def _publish(
        self, outcome: str, actions: list[str], duration_ms: int
    ) -> None:
        try:
            self._hub.publish(
                "daemon.tick",
                {
                    "activity_kind": "tick",
                    "outcome": outcome,
                    "actions": actions,
                    "duration_ms": duration_ms,
                },
            )
        except Exception:  # noqa: BLE001 — live feed is best-effort
            pass

    async def _loop(self) -> None:
        """Background task: sleep + tick + repeat until cancelled."""
        log.debug(
            "ticker loop started (interval=%ds, auto_execute=%s)",
            self.tick_interval_seconds,
            self.auto_execute,
        )
        try:
            while not self._stopped.is_set():
                await self._sleeper(self.tick_interval_seconds)
                if self._stopped.is_set():
                    break
                try:
                    self.tick_once()
                except Exception as exc:  # noqa: BLE001 — never let a tick kill the loop
                    log.exception("ticker tick_once failed")
                    with contextlib.suppress(Exception):
                        self._ticks.record(
                            started_at=datetime.now(UTC).isoformat(),
                            duration_ms=0,
                            actions=[],
                            outcome="error",
                            detail={"error": str(exc)},
                        )
        except asyncio.CancelledError:
            log.debug("ticker loop cancelled")
            raise


__all__ = ["TICK_HISTORY_KEEP", "Ticker"]
