"""Vehicle selection + per-task cap defaults (PRD D1/D3).

The founder picks a ModelSource; the engine derives the loop vehicle
here. ``select_runner`` returning ``None`` means "use the legacy
single-call path" — no ModelSource configured, the
``harness_execution_enabled`` flag is off, or the vehicle binary is
missing from PATH (graceful degrade: health event + legacy fallback,
never a crash).
"""

from __future__ import annotations

import shutil
from typing import Any

from kompany.core.harness import (
    ClaudeCodeRunner,
    CodexRunner,
    HarnessRunner,
    OpencodeRunner,
)
from kompany.core.run_context import current_run_id

# PRD D3 defaults applied when the CEO omits per-task caps at
# decomposition time: small/routine tasks default to $0.50; the CEO may
# assign up to $5 for genuinely complex work — never more.
DEFAULT_BUDGET_CAP_USD = 0.50
MAX_BUDGET_CAP_USD = 5.0
DEFAULT_MAX_TURNS = 30

# Vehicle → PATH binary, for the graceful-degrade check (PRD acceptance:
# "Missing CLI for chosen source → health event + degrade, no crash").
VEHICLE_BINARIES: dict[str, str] = {
    "claude_code": "claude",
    "codex": "codex",
    "opencode": "opencode",
}

# (run_id, vehicle) pairs already warned about — one ``harness_vehicle_missing``
# health event per engine run, not one per parked task.
_missing_vehicle_warned: set[tuple[str | None, str]] = set()


def reset_vehicle_missing_dedupe() -> None:
    """Clear the once-per-run dedupe (tests / known PATH change)."""
    _missing_vehicle_warned.clear()


def _warn_vehicle_missing(
    health_events: Any, vehicle: str, binary: str, source_kind: str
) -> None:
    if health_events is None:
        return
    key = (current_run_id(), vehicle)
    if key in _missing_vehicle_warned:
        return
    _missing_vehicle_warned.add(key)
    try:
        health_events.record(
            "harness_vehicle_missing",
            detail={
                "vehicle": vehicle,
                "binary": binary,
                "model_source_kind": source_kind,
                "degraded_to": "single_call_legacy_path",
                "hint": (
                    f"The '{binary}' CLI is not on PATH — install it (or "
                    "switch the ModelSource in Settings) to restore full "
                    "agentic execution; until then tasks run in "
                    "single-shot asset mode."
                ),
            },
        )
    except Exception:  # noqa: BLE001 — degrade must never crash execution
        pass


def execution_caps(
    budget_cap_usd: float | None, max_turns: int | None
) -> tuple[float, int]:
    """Execution-time caps: defaults for missing values, NO ceiling clamp.

    The ``MAX_BUDGET_CAP_USD`` ceiling binds the *CEO* at decomposition
    write time (:func:`resolve_caps`). At execution time the task row is
    authoritative: a cap raised past the ceiling by a founder-approved
    ``harness_budget_increase`` must be honored as-is — founder approval
    overrides the CEO ceiling. Never re-clamp here.
    """
    cap = (
        float(budget_cap_usd)
        if budget_cap_usd is not None and budget_cap_usd > 0
        else DEFAULT_BUDGET_CAP_USD
    )
    turns = (
        int(max_turns)
        if max_turns is not None and max_turns > 0
        else DEFAULT_MAX_TURNS
    )
    return cap, turns


def resolve_caps(
    budget_cap_usd: float | None, max_turns: int | None
) -> tuple[float, int]:
    """Decomposition-time caps: PRD D3 defaults + the CEO ceiling clamp.

    Call this ONLY when writing CEO-assigned caps onto new task rows
    (``ProjectRunner`` decomposition). Reading a stored row back for
    execution must use :func:`execution_caps` instead, or a
    founder-approved cap above the ceiling would be silently re-clamped.
    """
    cap, turns = execution_caps(budget_cap_usd, max_turns)
    return min(cap, MAX_BUDGET_CAP_USD), turns


def harness_model(settings: Any, vehicle: str) -> str:
    """Model passed to the vehicle, from the primary tier mapping.

    ``claude-code:*`` single-call ids are an LLMClient routing prefix,
    not a claude CLI model name — strip the prefix for the claude
    vehicle (the CLI accepts aliases like ``sonnet`` or full ids).
    """
    model = str(getattr(settings, "model_primary", "") or "sonnet")
    if vehicle == "claude_code" and model.lower().startswith("claude-code:"):
        model = model.split(":", 1)[1] or "sonnet"
    return model


def select_runner(
    settings: Any,
    health_events: Any = None,
    permission_mode: str | None = None,
) -> HarnessRunner | None:
    """Derive the loop vehicle from the active ModelSource (PRD D1).

    Returns ``None`` — meaning "use the legacy single-call path" — when
    no ModelSource is configured, the feature flag is off, or the
    derived vehicle's CLI binary is missing from PATH (in which case a
    ``harness_vehicle_missing`` health event is recorded once per engine
    run via ``health_events``, when provided). The vehicle string is
    engine-internal; it must never surface to the founder.
    """
    if settings is None:
        return None
    source = getattr(settings, "model_source", None)
    if source is None:
        return None
    if not getattr(settings, "harness_execution_enabled", True):
        return None
    vehicle = source.vehicle
    binary = VEHICLE_BINARIES.get(vehicle)
    if binary is not None and shutil.which(binary) is None:
        _warn_vehicle_missing(health_events, vehicle, binary, source.kind)
        return None
    model = harness_model(settings, vehicle)
    if vehicle == "claude_code":
        # permission_mode override: self-update sessions run with
        # acceptEdits — file edits in the throwaway clone are the whole
        # point and the D5 inbox gate is for live-project side effects,
        # not clone work (06-12-self-update-pipeline live finding: the
        # default mode denied every Write and sessions produced empty
        # diffs). Other vehicles gate via their own profiles.
        if permission_mode is not None:
            return ClaudeCodeRunner(model=model, permission_mode=permission_mode)
        return ClaudeCodeRunner(model=model)
    if vehicle == "codex":
        return CodexRunner(model=model)
    if vehicle == "opencode":
        return OpencodeRunner(model=model)
    return None


__all__ = [
    "DEFAULT_BUDGET_CAP_USD",
    "DEFAULT_MAX_TURNS",
    "MAX_BUDGET_CAP_USD",
    "VEHICLE_BINARIES",
    "execution_caps",
    "harness_model",
    "reset_vehicle_missing_dedupe",
    "resolve_caps",
    "select_runner",
]
