"""LEDGER layer of the three-layer cost visibility discipline.

This module exposes a single helper, :func:`record_ai_cost`, which is the
recommended single entry point for callers that need to:

1. Make sure an :class:`~kompany.llm.client.LLMResponse` has been booked
   against the company ledger (``LedgerCategory.AI_COST``).
2. Emit a corresponding ``llm.spend`` SSE envelope on the process-wide
   :class:`~kompany.core.event_hub.EventHub` so browser clients can keep
   dashboard chips / live cost meters in sync.

In practice :class:`~kompany.llm.client.LLMClient` already records every
successful response through :class:`~kompany.llm.cost_tracker.CostTracker`
which writes the ledger + emits the SSE event automatically. That covers
the vast majority of LLM call sites without any caller bookkeeping.

The explicit ``record_ai_cost`` helper exists for two reasons:

* **Discipline**: it gives callers that care about the per-action_type
  story (``"target_feasibility"``, ``"debate_round_1"``, ...) a single
  place to tag the spend so the SSE payload carries a meaningful label
  rather than ``"other"``.
* **Idempotence**: response objects passed back from
  :class:`LLMClient.call` carry an internal ``_cost_recorded`` flag.
  ``record_ai_cost`` honours it and skips the ledger write when the cost
  is already booked, so this helper is safe to call after the LLM client
  has done its work. In that case it ONLY emits the SSE event (with the
  caller-supplied ``action_type``).

The double-record path matters because we don't want two ``AI_COST``
ledger rows per real LLM call — the response cost is reality, the SSE
event is just a UI signal.
"""

from __future__ import annotations

import logging
from typing import Any

from kompany.llm.client import LLMResponse

log = logging.getLogger(__name__)


def record_ai_cost(
    ledger: Any,
    resp: LLMResponse,
    *,
    action_type: str,
    run_id: str | None = None,
    event_hub: Any = None,
    description: str | None = None,
) -> None:
    """Book an LLM response on the ledger and emit a ``llm.spend`` event.

    The function is idempotent against ``LLMClient`` — if the response
    already carries ``_cost_recorded = True`` (set by
    :class:`~kompany.llm.cost_tracker.CostTracker`) we skip the ledger
    write and only fire the SSE so the per-action_type label reaches
    subscribers.

    Parameters
    ----------
    ledger:
        A :class:`~kompany.state.ledger.Ledger`-like object exposing
        ``record_ai_cost(amount_usd, description, ...)`` and
        ``get_balance() -> float``. May be ``None`` for tests; in that
        case only the SSE emit runs (and only if ``event_hub`` is wired).
    resp:
        The :class:`LLMResponse` returned by the LLM client. Provides
        ``model``, ``input_tokens``, ``output_tokens``, ``cost_usd``.
    action_type:
        Stable label for the call site. PRD-defined vocabulary:
        ``"target_feasibility"``, ``"feasibility_revise"``,
        ``"distillation"``, ``"debate_round_1"``, ``"debate_round_2"``,
        ``"debate_synthesis"``, ``"debate_decision"``,
        ``"agent_classify"``, ``"agent_task_execute"``,
        ``"glossary_scan"``, ``"ping"``, ``"other"``. Free-form strings
        are allowed; UI components treat unknowns as "other".
    run_id:
        Optional run identifier to thread through the ledger row + SSE
        payload. When ``None`` the helper does not touch
        :func:`current_run_id` (the underlying ``ledger.record_ai_cost``
        does that itself).
    event_hub:
        Optional :class:`~kompany.core.event_hub.EventHub`. When
        omitted, no SSE event is emitted.
    description:
        Human-readable line for the ledger row. Defaults to
        ``"AI: <action_type>: <model>"`` if omitted.
    """
    already_recorded = bool(getattr(resp, "_cost_recorded", False))

    if not already_recorded and ledger is not None:
        desc = description or f"{action_type}: {resp.model}"
        ledger.record_ai_cost(
            amount_usd=float(resp.cost_usd or 0.0),
            description=desc,
            run_id=run_id,
        )
        # Mark so a second call to record_ai_cost with the same response
        # cannot double-book — this matches CostTracker.record's contract.
        try:
            setattr(resp, "_cost_recorded", True)
        except Exception:  # pragma: no cover — frozen dataclasses
            pass

    if event_hub is None:
        return

    balance_after: float | None = None
    if ledger is not None:
        try:
            balance_after = float(ledger.get_balance())
        except Exception:  # pragma: no cover — defensive
            balance_after = None

    payload = {
        "action_type": action_type,
        "model": resp.model,
        "input_tokens": int(getattr(resp, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(resp, "output_tokens", 0) or 0),
        "cost_usd": float(getattr(resp, "cost_usd", 0.0) or 0.0),
        "run_id": run_id,
        "ledger_balance_after": balance_after,
    }
    try:
        event_hub.publish("llm.spend", payload)
    except Exception:
        # Best-effort — the SSE event is a UI signal, not source of truth.
        log.debug("record_ai_cost: event_hub.publish failed", exc_info=True)


__all__ = ["record_ai_cost"]
