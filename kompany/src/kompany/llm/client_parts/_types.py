"""Shared types and constants for the LLM client."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from dataclasses import dataclass, field
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
class ToolSpec:
    """Provider-neutral tool definition (06-16-agentic-chat-engine P1).

    A single description of a callable tool that each provider mixin
    translates to its own wire format (Anthropic ``tools=`` / OpenAI
    ``tools=[{"type":"function",...}]``). ``parameters`` is a JSON-schema
    object describing the tool's arguments.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object", "properties": {}
    })

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters or {"type": "object", "properties": {}},
        }

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


@dataclass
class ToolCallRequest:
    """One tool invocation the model asked for, provider-normalized.

    ``call_id`` is the provider's correlation id (Anthropic ``tool_use``
    block id / OpenAI ``tool_call`` id) that the matching result must
    echo back so the next turn is well-formed.
    """

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    parsed: Any = None
    # Native tool_use surface (06-16-agentic-chat-engine P1). Populated by
    # the ``call_with_tools`` path: the tool calls the model requested this
    # turn, ``stop_reason`` ("tool_use"/"end_turn"/"stop"/...), and the
    # provider-native assistant message to replay on the next turn (so the
    # follow-up tool_result/role:"tool" message threads correctly). Empty
    # on the legacy text paths.
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    stop_reason: str | None = None
    raw_assistant_message: Any = None
    # Set to True by :class:`CostTracker.record` (and by
    # ``record_ai_cost``) after the response has been booked against the
    # ledger. Callers can read this to avoid double-recording the same
    # response — see ``llm/cost_ledger.py``. Not part of the wire format.
    _cost_recorded: bool = False
