"""Post-resolve effects for harness budget approvals (PR5 closure).

PR4 created ``project_envelope_topup`` / ``harness_budget_increase``
approval requests but approving them moved no money — the founder's
"yes" was decorative. This module makes the approval real, mirroring
the ``core/subscription_fee.py`` precedent: the logic lives here, and
``engine.approve_request`` / ``engine.reject_request`` keep only a thin
delegate.

Idempotency: the approval state machine already makes re-approving an
approved row a no-op transition, but the engine hook still re-fires on
replayed calls (remote command replay, double-tap CLI). An
``effect_applied`` stamp in the approval payload guards against
double-funding. The stamp is intentionally NOT written when funding
fails on insufficient treasury — re-approving later (after revenue
lands) retries the funding instead of refusing forever
(mission-integrity: "can't afford it" is never terminal).
"""

from __future__ import annotations

from typing import Any

from kompany.channels.outbox import (
    ACTION_CHANNEL_POST,
    approve_channel_post,
    reject_channel_post,
)
from kompany.core.harness_execution.executor import (
    ACTION_BUDGET_INCREASE,
    ACTION_ENVELOPE_TOPUP,
)
from kompany.core.self_update.effects import (
    ACTION_SELF_UPDATE,
    approve_self_update,
    reject_self_update,
)
from kompany.state.models import ApprovalRequest

# Action types whose approve/reject triggers an engine-side effect here.
HARNESS_EFFECT_ACTIONS: frozenset[str] = frozenset(
    {
        ACTION_ENVELOPE_TOPUP,
        ACTION_BUDGET_INCREASE,
        ACTION_SELF_UPDATE,
        ACTION_CHANNEL_POST,
    }
)


def apply_post_approve_effect(
    engine: Any, request: ApprovalRequest
) -> dict[str, Any]:
    """Execute the approved request's real-world effect. Returns a summary."""
    payload = request.payload or {}
    if payload.get("effect_applied"):
        return {"status": "already_applied"}
    if request.action_type == ACTION_ENVELOPE_TOPUP:
        return _approve_envelope_topup(engine, request)
    if request.action_type == ACTION_BUDGET_INCREASE:
        return _approve_budget_increase(engine, request)
    if request.action_type == ACTION_SELF_UPDATE:
        return approve_self_update(engine, request)
    if request.action_type == ACTION_CHANNEL_POST:
        # Tier 1 (06-12-channels D3): mark approved + manual-post comment.
        # NEVER posts anywhere — constitution publishing gate.
        return approve_channel_post(engine, request)
    return {"status": "no_effect"}


def apply_post_reject_effect(
    engine: Any, request: ApprovalRequest
) -> dict[str, Any]:
    """Rejected: audit it and leave the task PENDING with a real next step."""
    if request.action_type == ACTION_SELF_UPDATE:
        return reject_self_update(engine, request)
    if request.action_type == ACTION_CHANNEL_POST:
        return reject_channel_post(engine, request)
    payload = request.payload or {}
    task_id = payload.get("task_id")
    if request.action_type == ACTION_ENVELOPE_TOPUP:
        founder_action = (
            "Envelope top-up declined — this project's tasks stay parked. "
            "Re-scope the project to fit the remaining envelope, fund it "
            "later (a new top-up request will appear on the next run), or "
            "abandon the project."
        )
    else:
        founder_action = (
            "Budget increase declined — the task stays paused. Re-scope "
            "it to fit its current cap, or abandon the project."
        )
    if task_id:
        _patch_task_founder_action(engine, str(task_id), founder_action)
    engine.audit.record(
        "approval_effect.declined",
        f"Declined {request.action_type} — task stays pending",
        detail={
            "approval_id": request.id,
            "action_type": request.action_type,
            "task_id": task_id,
        },
        project_id=request.project_id,
    )
    return {"status": "declined", "founder_action": founder_action}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _approve_envelope_topup(
    engine: Any, request: ApprovalRequest
) -> dict[str, Any]:
    payload = request.payload or {}
    project_id = payload.get("project_id")
    amount = float(
        payload.get("approved_top_up_usd")
        or payload.get("suggested_top_up_usd")
        or 0.0
    )
    if not project_id or amount <= 0:
        return {"status": "invalid_payload"}
    try:
        budget = engine.fund_project(str(project_id), amount)
    except ValueError as exc:
        # Insufficient unallocated treasury. Mission integrity: explain
        # the shortfall and the funding path — never silently fail, never
        # refuse terminally (no stamp → a later re-approve retries).
        _comment(
            engine,
            request.id,
            f"Could not fund the envelope yet: {exc}. The approval stands "
            "— approve again once treasury frees up, or spin up a revenue "
            "path whose target covers the shortfall so the envelope can "
            "be funded from real income.",
        )
        engine.audit.record(
            "approval_effect.funding_short",
            "Approved top-up could not be funded: insufficient treasury",
            detail={
                "approval_id": request.id,
                "project_id": project_id,
                "amount_usd": amount,
                "error": str(exc),
            },
            project_id=request.project_id,
        )
        return {"status": "insufficient_treasury", "detail": str(exc)}
    engine.approvals.update_payload(
        request.id, {"effect_applied": True, "funded_usd": amount}
    )
    _comment(
        engine,
        request.id,
        f"Funded {amount:.2f} into the project envelope "
        f"(remaining: {float(budget.get('remaining') or 0.0):.2f}). Parked "
        "tasks run on the project's next execution.",
    )
    engine.audit.record(
        "approval_effect.project_funded",
        f"Approved top-up funded the project envelope (+{amount:.2f})",
        detail={
            "approval_id": request.id,
            "project_id": project_id,
            "amount_usd": amount,
        },
        project_id=request.project_id,
    )
    task_id = payload.get("task_id")
    if task_id:
        _patch_task_founder_action(
            engine,
            str(task_id),
            "Envelope topped up — re-run the project to resume its "
            "parked tasks.",
        )
    return {
        "status": "funded",
        "amount_usd": amount,
        "remaining": budget.get("remaining"),
    }


def _approve_budget_increase(
    engine: Any, request: ApprovalRequest
) -> dict[str, Any]:
    payload = request.payload or {}
    task_id = payload.get("task_id")
    increase = float(
        payload.get("approved_increase_usd")
        or payload.get("proposed_increase_usd")
        or 0.0
    )
    task = engine.projects.get_task(str(task_id)) if task_id else None
    if task is None or increase <= 0:
        return {"status": "invalid_payload"}
    new_cap = float(task.budget_cap_usd or 0.0) + increase
    engine.projects.set_task_budget_cap(task.id, new_cap)
    engine.approvals.update_payload(
        request.id, {"effect_applied": True, "new_budget_cap_usd": new_cap}
    )
    _patch_task_founder_action(
        engine,
        task.id,
        "Budget increase approved — re-run the project; the session "
        "continues from where it stopped.",
    )
    engine.audit.record(
        "approval_effect.budget_cap_raised",
        f"Raised task budget cap to ${new_cap:.2f} (+${increase:.2f})",
        detail={
            "approval_id": request.id,
            "task_id": task.id,
            "increase_usd": increase,
            "new_budget_cap_usd": new_cap,
        },
        project_id=request.project_id,
    )
    return {"status": "cap_raised", "new_budget_cap_usd": new_cap}


def _patch_task_founder_action(engine: Any, task_id: str, text: str) -> None:
    """Update only ``founder_action`` in the task result, keep the rest."""
    task = engine.projects.get_task(task_id)
    if task is None:
        return
    merged = {**(task.result or {}), "founder_action": text}
    engine.projects.update_task_status(task_id, task.status, result=merged)


def _comment(engine: Any, approval_id: str, body: str) -> None:
    """Best-effort system comment on the approval thread."""
    try:
        engine.approvals.add_comment(
            approval_id, body=body, by_type="system", by_id=None
        )
    except Exception:  # noqa: BLE001 — the effect outcome is already audited
        pass


__all__ = [
    "HARNESS_EFFECT_ACTIONS",
    "apply_post_approve_effect",
    "apply_post_reject_effect",
]
