"""Harness task execution — ProjectRunner's execution organ (PR4/PR5).

Package layout (each concern in a sibling module, ≤500-line rule):

* ``selection``  — ModelSource → vehicle derivation + per-task cap
  defaults (PRD D1/D3) + graceful degrade when the vehicle binary is
  missing (health event + legacy fallback)
* ``monitor``    — live event stream consumer: EventHub republication
  (``activity_kind: "harness"``), watchdog liveness touches, external
  turn-cap enforcement
* ``outcomes``   — evidence-based outcome classification (PRD D6) +
  hard-failure detection (honest error mapping)
* ``permission_gate`` — claude permission routing through the founder
  approval inbox (PRD D5): CLI arg construction + the blocking
  ``kompany_permission_gate`` MCP tool handler
* ``executor``   — the session run itself: envelope guard, cost
  booking via ``CostTracker.record_external``, budget-exceeded pause +
  approval, session-id persistence for resume (PRD D4)
"""

from __future__ import annotations

from kompany.core.harness_execution.executor import (
    ACTION_BUDGET_INCREASE,
    ACTION_ENVELOPE_TOPUP,
    execute_harness_task,
)
from kompany.core.harness_execution.monitor import (
    TOUCH_THROTTLE_SECONDS,
    EventMonitor,
)
from kompany.core.harness_execution.outcomes import (
    classify_harness_outcome,
    is_hard_failure,
)
from kompany.core.harness_execution.permission_gate import (
    ACTION_HARNESS_PERMISSION,
    build_permission_routing_args,
    enrich_gate_arguments,
    handle_permission_gate,
    routing_args_for_task,
)
from kompany.core.harness_execution.selection import (
    DEFAULT_BUDGET_CAP_USD,
    DEFAULT_MAX_TURNS,
    MAX_BUDGET_CAP_USD,
    VEHICLE_BINARIES,
    execution_caps,
    harness_model,
    reset_vehicle_missing_dedupe,
    resolve_caps,
    select_runner,
)

__all__ = [
    "ACTION_BUDGET_INCREASE",
    "ACTION_ENVELOPE_TOPUP",
    "ACTION_HARNESS_PERMISSION",
    "DEFAULT_BUDGET_CAP_USD",
    "DEFAULT_MAX_TURNS",
    "MAX_BUDGET_CAP_USD",
    "TOUCH_THROTTLE_SECONDS",
    "VEHICLE_BINARIES",
    "EventMonitor",
    "build_permission_routing_args",
    "classify_harness_outcome",
    "enrich_gate_arguments",
    "execute_harness_task",
    "execution_caps",
    "handle_permission_gate",
    "harness_model",
    "is_hard_failure",
    "reset_vehicle_missing_dedupe",
    "resolve_caps",
    "routing_args_for_task",
    "select_runner",
]
