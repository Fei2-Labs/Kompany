"""External budget enforcement for vehicles without a native cap.

Only the claude vehicle has ``--max-budget-usd``; opencode and codex need
the orchestrator to accumulate cost deltas off the event stream and abort
when the cap is hit (research pitfall 9). OpencodeRunner feeds authoritative
per-step USD (``step_finish.part.cost``); CodexRunner feeds USD estimated
from per-turn token usage (flagged via ``HarnessResult.cost_is_estimate``).
"""

from __future__ import annotations

import threading

# Absolute tolerance at the cap boundary: per-step USD deltas are binary
# floats (e.g. 3 × 0.01 sums to 0.030000000000000002), and without a
# tolerance a run landing exactly on its cap would falsely count as a
# breach. Far below any bookable amount (1 nano-dollar).
_CAP_EPSILON = 1e-9


class BudgetMeter:
    """Thread-safe spend accumulator with an optional hard cap.

    ``add`` is called from the subprocess stdout reader, ``spent_usd`` may
    be read from any thread — hence the lock. Spending exactly the cap is
    NOT a breach; only strictly exceeding it is. ``cap_usd=None`` means
    uncapped (``add`` always returns True).
    """

    def __init__(self, cap_usd: float | None) -> None:
        self._cap_usd = cap_usd
        self._spent_usd = 0.0
        self._lock = threading.Lock()

    @property
    def cap_usd(self) -> float | None:
        return self._cap_usd

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent_usd

    def add(self, delta_usd: float) -> bool:
        """Record one cost delta; return False once the cap is exceeded."""
        with self._lock:
            self._spent_usd += delta_usd
            return (
                self._cap_usd is None
                or self._spent_usd <= self._cap_usd + _CAP_EPSILON
            )
