"""EventHub — in-process pub/sub for live UI streaming.

The :class:`EventHub` is a thin asyncio fan-out used by the FastAPI SSE
endpoint to push real-time events to browser clients. It is deliberately
process-local (no Redis, no fan-in from worker processes) because the
current Kompany deployment is single-process / single-player.

Design notes
------------
* ``publish(event_type, payload)`` is **non-blocking** and **safe to call
  from sync code**. When the asyncio event loop exists, it copies the event
  into every active subscriber queue; if no loop is running it silently
  drops (e.g. unit tests that never start a server).
* ``subscribe()`` returns an async iterator that yields dicts of the form
  ``{"type": str, "data": dict, "id": int}``. Each subscriber owns its own
  bounded queue so a slow client cannot stall the publisher.
* Backpressure: queues use a fixed capacity (default 256). When full we
  drop the oldest event and log a warning rather than block. This matches
  the "live feed, not source of truth" contract of SSE.
* Heartbeats are emitted by the SSE endpoint itself (every 15s) and are
  not routed through the hub.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Per-subscriber queue capacity. Bursts beyond this drop the oldest event.
DEFAULT_CAPACITY = 256


class EventHub:
    """Process-local fan-out for browser SSE clients."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = max(1, int(capacity))
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._next_id = 0
        # The asyncio loop the SSE endpoint runs on. Captured when a
        # subscriber connects (on the loop thread) so that publishes
        # coming from OTHER threads — e.g. the post-onboarding kickoff
        # which runs execute_project in a FastAPI BackgroundTask / anyio
        # worker thread — can hop back onto the loop via
        # call_soon_threadsafe instead of being dropped.
        self._loop: asyncio.AbstractEventLoop | None = None

    def register_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Record the event loop SSE subscribers run on. Called from the
        SSE endpoint (which executes on that loop)."""
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # ------------------------------------------------------------------
    # Publisher side
    # ------------------------------------------------------------------

    def publish(self, event_type: str, payload: dict | None = None) -> None:
        """Push an event to every active subscriber.

        Safe to call from synchronous code. If no asyncio loop is running
        (e.g. CLI/test path with no live server), this is a no-op.
        """
        if not self._subscribers:
            return

        self._next_id += 1
        envelope = {
            "id": self._next_id,
            "type": str(event_type),
            "data": dict(payload or {}),
        }

        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False

        if on_loop:
            # Publisher is on the event loop thread — enqueue directly.
            for queue in list(self._subscribers):
                self._enqueue(queue, envelope)
            return

        # Publisher is on a background/worker thread (e.g. the kickoff
        # task). asyncio.Queue is NOT thread-safe, so we must hop back
        # onto the SSE loop. Without this, every event a background task
        # emits (task.started / task.completed / virtual_day.advanced)
        # was dropped and the live timeline stayed empty.
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(self._fanout, envelope)

    def _fanout(self, envelope: dict) -> None:
        """Enqueue an envelope to every subscriber. Runs on the loop
        thread (called directly, or via call_soon_threadsafe)."""
        for queue in list(self._subscribers):
            self._enqueue(queue, envelope)

    def _enqueue(self, queue: asyncio.Queue[dict], envelope: dict) -> None:
        # Drop oldest on overflow so slow clients can't wedge publishers.
        if queue.full():
            try:
                queue.get_nowait()
                logger.warning(
                    "event_hub: subscriber queue full (cap=%s); dropped oldest",
                    self._capacity,
                )
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(envelope)
        except asyncio.QueueFull:
            # Race with another publisher; nothing we can do without blocking.
            logger.warning("event_hub: queue still full after eviction; dropping event")

    # ------------------------------------------------------------------
    # Subscriber side
    # ------------------------------------------------------------------

    async def subscribe(self) -> AsyncIterator[dict]:
        """Async generator yielding event envelopes for one client.

        Use with ``async for evt in hub.subscribe(): ...``. Cleanup is
        handled via ``finally`` so the queue is removed even if the
        consumer cancels (e.g. browser disconnects).
        """
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._capacity)
        self._subscribers.add(queue)
        try:
            while True:
                envelope = await queue.get()
                yield envelope
        finally:
            self._subscribers.discard(queue)

    # ------------------------------------------------------------------
    # Introspection (mainly for tests)
    # ------------------------------------------------------------------

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


_HUB: EventHub | None = None


def get_event_hub() -> EventHub:
    """Return the process-wide :class:`EventHub` singleton."""
    global _HUB
    if _HUB is None:
        _HUB = EventHub()
    return _HUB


def reset_event_hub() -> None:
    """Reset the singleton (for tests)."""
    global _HUB
    _HUB = None
