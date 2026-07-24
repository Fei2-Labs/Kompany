"""Active-operation registry for safe deployment draining.

Stage A deployment plan (Stage A step 6): a persisted ``suspended`` runtime
state already rejects *new* work at every dispatch entry point (see
``engine_parts/ops.py:suspend`` and its three call sites in
``lane_dispatcher.py``, ``ticker.py``, ``outward_lane.py`` — all gate on
``runtime.get()["state"] == "suspended"``). What that alone cannot answer is
the harder question a deployment actually needs: **is anything still
running that a hard process restart would corrupt or silently drop?**

This module is the missing piece: a process-wide, in-memory counter of
in-flight operations across the four categories the deployment plan names
— task attempts, channel handlers, harness children, and connector calls.
It is deliberately NOT persisted to SQLite: counts only mean something for
the currently-running process (a restart always resets them to zero, which
is the correct state for a *new* process that hasn't started anything yet).
The persisted piece is the *runtime state itself* (``suspended`` / reason),
already handled by :class:`kompany.state.runtime.RuntimeStateStore`.

Usage: wrap the actual unit of work (not the suspend-check branch) in
``with get_drain_registry().track("task_attempt"): ...`` at each of the
four call sites. ``ready_for_restart()`` combines the persisted suspended
state with the live in-memory counts, matching the deployment plan's
"reports ready_for_restart only when task attempts, channel handlers,
harness children, and connector calls are all zero" contract.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

# The four categories named explicitly in the Stage A deployment plan.
CATEGORIES = ("task_attempt", "channel_handler", "harness_child", "connector_call")


class ActiveOperationRegistry:
    """Thread-safe in-flight operation counters, one per category."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {c: 0 for c in CATEGORIES}

    def _require_category(self, category: str) -> None:
        if category not in CATEGORIES:
            raise ValueError(f"unknown drain category: {category!r} (expected one of {CATEGORIES})")

    @contextmanager
    def track(self, category: str) -> Iterator[None]:
        """Wrap one unit of in-flight work. Always decrements, even on
        exception, so a failed task attempt / connector call never leaves
        the registry stuck non-zero and blocks a drain forever."""
        self._require_category(category)
        with self._lock:
            self._counts[category] += 1
        try:
            yield
        finally:
            with self._lock:
                self._counts[category] -= 1

    def counts(self) -> dict[str, int]:
        """Snapshot of current in-flight counts per category."""
        with self._lock:
            return dict(self._counts)

    def total(self) -> int:
        with self._lock:
            return sum(self._counts.values())

    def reset(self) -> None:
        """Test-only: force all counters back to zero."""
        with self._lock:
            self._counts = {c: 0 for c in CATEGORIES}


_REGISTRY: ActiveOperationRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_drain_registry() -> ActiveOperationRegistry:
    """Process-wide singleton, lazily created (mirrors ``get_event_hub``)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = ActiveOperationRegistry()
    return _REGISTRY
