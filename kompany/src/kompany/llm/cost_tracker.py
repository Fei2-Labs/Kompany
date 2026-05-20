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


class CostTracker:
    """Tracks AI costs and records them to the company ledger."""

    def __init__(self, ledger=None, event_hub: Any = None):
        self.ledger = ledger
        # Optional :class:`EventHub`. When set, every successful ledger
        # write publishes a ``llm.spend`` envelope. None in standalone
        # tests / pure-CLI use; the engine wires it on construction.
        self.event_hub = event_hub
        self.session_total: float = 0.0

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
