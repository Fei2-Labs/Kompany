"""Live-stream consumer for one harness run.

Bridges the vehicle's normalized event stream to the rest of the
engine: EventHub fan-out (``activity_kind: "harness"``), watchdog
liveness (throttled task ``updated_at`` touches), and external turn-cap
enforcement (PRD D3) for vehicles without a native turn flag.
"""

from __future__ import annotations

import time
from typing import Any

from kompany.core.event_hub import get_event_hub
from kompany.core.harness import HarnessEvent, RunAbort
from kompany.state.models import Project, Task

# Minimum seconds between task ``updated_at`` touches while events
# stream — keeps the watchdog's stale scan happy without hammering
# SQLite on chatty sessions.
TOUCH_THROTTLE_SECONDS = 10.0


class EventMonitor:
    """``on_event`` consumer for one harness run.

    Republishes every normalized event to the EventHub with
    ``activity_kind: "harness"`` (the SSE activity contract addition),
    touches the task's ``updated_at`` (throttled) so the watchdog treats
    an actively-streaming run as alive, and enforces the external turn
    cap by raising :class:`RunAbort` once ``max_turns`` is exceeded
    (vehicles without a native turn flag — research: opencode/codex).
    """

    def __init__(
        self,
        projects: Any,
        task: Task,
        project: Project,
        max_turns: int,
        hub: Any = None,
        clock: Any = time.monotonic,
        vehicle: str | None = None,
    ) -> None:
        self._projects = projects
        self._task = task
        self._project = project
        self._max_turns = max_turns
        self._hub = hub if hub is not None else get_event_hub()
        self._clock = clock
        self._vehicle = vehicle
        self._last_touch: float | None = None
        self.turns = 0
        self.tool_events = 0
        self.session_id: str | None = None
        self.last_text = ""

    def __call__(self, event: HarnessEvent) -> None:
        if event.kind == "session_started":
            sid = event.payload.get("session_id")
            if sid:
                self.session_id = str(sid)
                self._persist_session_id()
        elif event.kind == "turn":
            self.turns += 1
        elif event.kind == "tool_use":
            self.tool_events += 1
        elif event.kind == "text":
            text = str(event.payload.get("text") or "")
            if text:
                self.last_text = text
        self._publish(event)
        self._touch()
        if self.turns > self._max_turns:
            raise RunAbort(
                f"external turn cap reached: {self.turns} turns > "
                f"max_turns={self._max_turns}"
            )

    def _publish(self, event: HarnessEvent) -> None:
        try:
            self._hub.publish(
                "harness.event",
                {
                    "activity_kind": "harness",
                    "project_id": self._project.id,
                    "task_id": self._task.id,
                    "agent_role": self._task.assigned_agent,
                    "kind": event.kind,
                    "summary": self._summarize(event),
                },
            )
        except Exception:  # noqa: BLE001 — live feed is best-effort
            pass

    def _touch(self) -> None:
        now = self._clock()
        if (
            self._last_touch is not None
            and now - self._last_touch < TOUCH_THROTTLE_SECONDS
        ):
            return
        self._last_touch = now
        try:
            self._projects.touch_task(self._task.id)
        except Exception:  # noqa: BLE001 — a touch miss must not kill the run
            pass

    def _persist_session_id(self) -> None:
        """Write the vehicle session id to SQLite as soon as it is known.

        Stage A deployment plan (session-persistence gap): previously the
        task row was only updated with ``set_task_harness_session`` after
        the *entire* run finished (``execute_harness_task``'s post-run
        book-keeping). A crash, kill, or hard process restart mid-run lost
        the session id entirely — the next attempt fell back to
        ``runner.start()`` instead of ``runner.resume()``, silently
        dropping the in-progress harness conversation/context. Persisting
        immediately on ``session_started`` closes that window; the
        post-run write in executor.py stays as a harmless idempotent
        no-op re-write (covers vehicles/paths where no explicit
        ``session_started`` event is emitted, e.g. some resume flows).
        """
        if not self._vehicle:
            return
        try:
            self._projects.set_task_harness_session(
                self._task.id, self.session_id, self._vehicle
            )
        except Exception:  # noqa: BLE001 — best-effort; post-run write still covers it
            pass

    @staticmethod
    def _summarize(event: HarnessEvent) -> str:
        if event.kind == "text":
            return str(event.payload.get("text") or "")[:120]
        if event.kind == "tool_use":
            return str(event.payload.get("tool_name") or "")[:120]
        if event.kind == "cost_delta":
            return f"${float(event.payload.get('usd') or 0.0):.4f}"
        if event.kind == "permission_denied":
            return str(event.payload.get("tool_name") or "denied")[:120]
        return event.kind


__all__ = ["TOUCH_THROTTLE_SECONDS", "EventMonitor"]
