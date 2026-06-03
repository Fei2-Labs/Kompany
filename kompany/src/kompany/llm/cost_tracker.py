"""Cost tracker — records every LLM call as a real expense in the ledger.

Also emits the STREAM layer of the three-layer cost visibility discipline
(see ``05-19-cost-visibility-discipline``) by publishing a ``llm.spend``
event on the process-wide :class:`~kompany.core.event_hub.EventHub` after
each successful ledger write. Subscribers (browser SSE clients via
``/events``) use these events to keep dashboard chips and live cost
meters in sync without polling.
"""

from __future__ import annotations

import logging
from typing import Any

from kompany.core.run_context import current_run_id
from kompany.llm.models import estimate_cost

log = logging.getLogger(__name__)

# Upper bound on how many per-run cost entries we keep in memory. The engine
# is a long-lived process: without a cap, ``_run_totals`` would grow one entry
# per directive forever (a slow leak). Once the cap is hit we evict the
# oldest entries (insertion order). This is purely an in-memory convenience
# cache — the authoritative per-run cost is always reconstructable from the
# ledger (every AI_COST row carries ``run_id``), so evicting a stale run only
# means ``run_total()`` for a long-finished run falls back to 0.0, never that
# real cost is lost. The cap comfortably exceeds any plausible set of
# concurrently in-flight directives.
_MAX_RUN_TOTALS = 1024


class CostTracker:
    """Tracks AI costs and records them to the company ledger."""

    def __init__(self, ledger=None, event_hub: Any = None):
        self.ledger = ledger
        # Optional :class:`EventHub`. When set, every successful ledger
        # write publishes a ``llm.spend`` envelope. None in standalone
        # tests / pure-CLI use; the engine wires it on construction.
        self.event_hub = event_hub
        # Process-lifetime running total. Kept for any consumer that wants a
        # coarse "how much has this engine instance spent" number; it is NOT
        # safe to attribute to a single directive under concurrency.
        self.session_total: float = 0.0
        # Per-run accumulation keyed by ``run_id``. Each concurrent
        # ``process_directive`` opens its own ``run_scope`` (a contextvar, so
        # already isolated per thread / async task), and every ``record()``
        # books its cost against the active run id. This lets each
        # ``DirectiveResult.total_ai_cost`` reflect ONLY its own run, even
        # when two directives run at the same time on the shared engine.
        self._run_totals: dict[str, float] = {}

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        description: str,
        directive_id: str | None = None,
        run_id: str | None = None,
        action_type: str | None = None,
    ) -> float:
        """Record an LLM call cost. Returns the USD cost.

        ``action_type`` is the call-site label used in the SSE
        ``llm.spend`` payload (e.g. ``"target_feasibility"``,
        ``"debate_round_1"``). When the caller omits it we fall back to
        ``"other"`` so the payload shape stays uniform.
        """
        cost = estimate_cost(model, input_tokens, output_tokens)
        self.session_total += cost
        rid = run_id if run_id is not None else current_run_id()
        if rid is not None:
            self._run_totals[rid] = self._run_totals.get(rid, 0.0) + cost
            self._prune_run_totals()
        balance_after: float | None = None
        if self.ledger:
            self.ledger.record_ai_cost(
                amount_usd=cost,
                description=description,
                directive_id=directive_id,
                run_id=rid,
            )
            try:
                balance_after = float(self.ledger.get_balance())
            except Exception:  # pragma: no cover — defensive
                balance_after = None

        # STREAM layer: fire-and-forget SSE event. Never let an event_hub
        # failure break the cost-recording path — the ledger write above
        # is the source of truth.
        self._publish_spend(
            action_type=action_type or "other",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            run_id=rid,
            balance_after=balance_after,
        )

        return cost

    def run_total(self, run_id: str | None = None) -> float:
        """Return the AI cost accumulated for a single run.

        Defaults to the run active in the current context
        (:func:`current_run_id`), so handlers inside ``process_directive``
        can call ``cost_tracker.run_total()`` to get *their own* directive's
        cost rather than the process-wide :attr:`session_total`, which would
        cross-contaminate under concurrent directives.

        Returns ``0.0`` outside any run scope or for an unknown run id.
        """
        rid = run_id if run_id is not None else current_run_id()
        if rid is None:
            return 0.0
        return self._run_totals.get(rid, 0.0)

    def _prune_run_totals(self) -> None:
        """Bound :attr:`_run_totals` so a long-lived engine can't leak.

        Evicts the oldest entries (dicts preserve insertion order in
        CPython 3.7+) once the cap is exceeded. A currently-accumulating
        run keeps growing in place (``dict.__setitem__`` on an existing key
        does not change its order), so the run being booked right now is
        never the one evicted unless ``_MAX_RUN_TOTALS`` simultaneous runs
        are in flight — far beyond any real concurrency.
        """
        overflow = len(self._run_totals) - _MAX_RUN_TOTALS
        if overflow <= 0:
            return
        for stale in list(self._run_totals)[:overflow]:
            del self._run_totals[stale]

    # ------------------------------------------------------------------
    # SSE helpers
    # ------------------------------------------------------------------

    def _publish_spend(
        self,
        *,
        action_type: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        run_id: str | None,
        balance_after: float | None,
    ) -> None:
        """Publish a ``llm.spend`` envelope on the wired event hub.

        Silent no-op when no hub is wired (CLI / unit-test paths).
        """
        if self.event_hub is None:
            return
        payload = {
            "action_type": action_type,
            "model": model,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cost_usd": float(cost_usd or 0.0),
            "run_id": run_id,
            "ledger_balance_after": balance_after,
        }
        try:
            self.event_hub.publish("llm.spend", payload)
        except Exception:
            # The STREAM layer is best-effort; never let a publisher
            # bug propagate into the LLM call path.
            log.debug("cost_tracker: event_hub.publish failed", exc_info=True)
