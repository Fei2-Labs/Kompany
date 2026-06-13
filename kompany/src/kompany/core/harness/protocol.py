"""HarnessRunner protocol — the contract every loop vehicle implements.

Egress harness layer: a harness CLI (claude / codex / opencode) acts as
Kompany's execution backend for agent tasks. Vehicle adapters are an
internal implementation detail — the founder picks a ModelSource, never a
runner (PRD D1). This is distinct from the ingress rule in
the internal design spec (harness as caller of
Kompany interfaces); see the "Scope note: ingress vs egress" there.

The API is synchronous (the engine is sync). Streaming happens through the
``on_event`` callback, invoked from the subprocess stdout reader as
normalized :class:`HarnessEvent` objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

EventKind = Literal[
    "session_started",
    "turn",
    "tool_use",
    "text",
    "cost_delta",
    "permission_denied",
    "completed",
    "failed",
]


class HarnessCaps(BaseModel):
    """Per-run caps assigned at task decomposition time (PRD D3).

    ``extra_cli_args`` carries vehicle-specific flags injected by the
    executor — e.g. the claude vehicle's ``--permission-prompt-tool``
    approval routing (PRD D5). Vehicles that don't understand the args
    simply ignore the field; the default keeps every existing call site
    byte-identical.
    """

    budget_cap_usd: float | None = None
    max_turns: int = 50
    extra_cli_args: list[str] = Field(default_factory=list)


class HarnessEvent(BaseModel):
    """One normalized stream event, vehicle-agnostic.

    ``raw`` optionally carries the vehicle's original event for consumers
    that need passthrough fidelity (debugging, transcripts).
    """

    kind: EventKind
    payload: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] | None = None


class HarnessResult(BaseModel):
    """Final outcome of one harness session run.

    ``cost_usd`` is the vehicle-reported authoritative spend; ``None``
    means non-authoritative (e.g. the run was killed before the final
    result event — see research: kill forfeits ``total_cost_usd``).
    ``cost_is_estimate`` flags token-only vehicles (codex) whose USD is
    computed from the pricing table, never backend-reported — the ledger
    must not book it as authoritative (evidence-traced discipline).
    ``permission_denials`` must surface every denied tool call — a run can
    exit 0 with blocked work (research pitfall 3).
    """

    final_text: str = ""
    session_id: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    cost_is_estimate: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    permission_denials: list[dict[str, Any]] = Field(default_factory=list)
    exit_status: str = "success"
    error: str | None = None


class HandoffBrief(BaseModel):
    """Normalized progress brief for cross-vehicle portability (PRD D4)."""

    done: str
    stuck: str
    next_steps: str
    session_id: str | None = None
    vehicle: str


EventCallback = Callable[[HarnessEvent], None]


@runtime_checkable
class HarnessRunner(Protocol):
    """Contract for loop vehicles (claude_code, codex, opencode, native)."""

    @property
    def vehicle_name(self) -> str:
        """Stable identifier for this vehicle (engine-internal, never UI)."""
        ...

    def start(
        self,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None = None,
    ) -> HarnessResult:
        """Run a new agentic session in ``workspace`` under ``caps``."""
        ...

    def resume(
        self,
        session_id: str,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None = None,
    ) -> HarnessResult:
        """Continue a previous session identified by ``session_id``."""
        ...

    def handoff(self, result: HarnessResult) -> HandoffBrief:
        """Assemble a portability brief from a finished run (non-LLM)."""
        ...
