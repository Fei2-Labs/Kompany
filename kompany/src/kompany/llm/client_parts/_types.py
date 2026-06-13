"""Shared types and constants for the LLM client."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

# Default silent-run timeout. Engine overrides via the ``llm_silent_timeout_seconds``
# field from ``company_config``. Kept module-level so unit tests can monkeypatch.
DEFAULT_LLM_SILENT_TIMEOUT_SECONDS = 90

T = TypeVar("T", bound=BaseModel)
ProviderErrorHandler = Callable[[dict[str, Any]], None]


class _SilentTimeoutMarker(BaseException):
    """Sentinel raised inside the LLM client when a soft timeout fires.

    Subclasses ``BaseException`` (not ``Exception``) so the broad
    ``except Exception`` blocks in :meth:`LLMClient.call` won't swallow
    it; the wrapper logic handles it explicitly. Not part of the public
    API — never raised outside ``client.py``.
    """

    def __init__(
        self,
        future: "concurrent.futures.Future[LLMResponse]",
        event_id: str | None,
    ):
        super().__init__("silent_timeout")
        self.future = future
        self.event_id = event_id


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    parsed: Any = None
    # Set to True by :class:`CostTracker.record` (and by
    # ``record_ai_cost``) after the response has been booked against the
    # ledger. Callers can read this to avoid double-recording the same
    # response — see ``llm/cost_ledger.py``. Not part of the wire format.
    _cost_recorded: bool = False
