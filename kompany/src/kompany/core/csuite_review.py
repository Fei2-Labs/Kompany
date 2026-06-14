"""C-suite review gate (ADR-0007) — adversarial review of outward deliverables.

When a task produces an outward-facing deliverable (see
:mod:`kompany.core.deliverables`), the engine convenes the relevant
executives for an **adversarial** review *before* the outward action (send /
post / launch) is allowed. Each reviewer is asked to find concrete defects
and to vote PASS or HOLD via the agent registry + LLM path (cost booked
normally). Aggregation rules:

* Any reviewer HOLDs, or raises a blocking defect, or the agent/LLM call
  fails → file a ``csuite_review`` approval card (severity high) carrying the
  task/project ids, the deliverable class, and the findings; stamp the task
  result with ``csuite_review_pending=True``. The outward action stays gated
  behind the founder's call. **Defensive default: a review that cannot run
  fails *closed* (files the card) — never silently passes outward.**
* All reviewers pass clean → stamp ``csuite_approved=True``, no card.

This module is NON-blocking for the runner: the task stays delivered/
completed. Only the outward shipping is gated, mirroring how
``channel_post`` / ``email`` already gate the actual send.

Approve / reject / revision handlers stamp the task result, comment on the
thread, and audit. All three are idempotent via the ``effect_applied`` payload
guard, matching the ``core/approval_effects.py`` pattern.
"""

from __future__ import annotations

from typing import Any

from kompany.core.deliverables import DeliverableClass, review_roles_for
from kompany.state.models import ApprovalRequest

# Approval action_type for a held C-suite review. Routed through
# core/approval_effects.py like the other post-resolve effects.
ACTION_CSUITE_REVIEW = "csuite_review"

# Marker a reviewer emits to request a HOLD. Case-insensitive scan.
_HOLD_MARKER = "HOLD"
_PASS_MARKER = "PASS"

# Max chars of deliverable output fed to a reviewer (keeps the prompt cheap).
_OUTPUT_EXCERPT_CHARS = 2000

_REVIEW_SYSTEM = (
    "You are a {role} doing a final, adversarial pre-ship review of an "
    "OUTWARD-FACING deliverable. Your job is to catch what would embarrass "
    "the company, lose a customer, or create legal/financial risk if this "
    "ships as-is. Be specific. Do NOT rubber-stamp."
)

_REVIEW_PROMPT = (
    "Deliverable class: {cls}\n"
    "Task: {title}\n\n"
    "Deliverable content:\n---\n{output}\n---\n\n"
    "Review it adversarially. List concrete defects (one per line, each "
    "starting with '- '); if there are none, write 'No defects.'.\n"
    "Then on the FINAL line write exactly one verdict token: '{ok}' if it is "
    "safe to ship as-is, or '{hold}' if it must NOT ship until fixed."
)


# Task outcomes that count as "shipped a deliverable" and so reach the gate.
_OUTWARD_OUTCOMES = frozenset({"delivered", "completed"})


# ---------------------------------------------------------------------------
# Gate entry point (called from both task-completion paths)
# ---------------------------------------------------------------------------


def gate_completed_task(
    engine: Any, task: Any, project: Any, outcome: str
) -> dict[str, Any] | None:
    """Classify a just-resolved task and run the review if outward-facing.

    NON-blocking: the task status is untouched (the runner already set it).
    Returns the :func:`request_csuite_review` summary, or ``None`` when the
    deliverable is internal / not a ship outcome / the gate could not be
    reached. Never raises — a gate failure must not break task completion.
    """
    from kompany.core.deliverables import (
        classify_deliverable,
        needs_csuite_review,
    )

    try:
        if outcome not in _OUTWARD_OUTCOMES:
            return None
        result = getattr(task, "result", None) or {}
        action_type = result.get("action_type") if isinstance(result, dict) else None
        project_type = getattr(getattr(project, "type", None), "value", None)
        cls = classify_deliverable(
            getattr(task, "title", "") or "",
            action_type=action_type,
            project_type=project_type,
        )
        if not needs_csuite_review(cls):
            return None
        return request_csuite_review(engine, task, project, cls)
    except Exception:  # noqa: BLE001 — the gate must never crash completion
        return None


# ---------------------------------------------------------------------------
# Review request (called from both task-completion paths)
# ---------------------------------------------------------------------------


def request_csuite_review(
    engine: Any,
    task: Any,
    project: Any,
    deliverable_class: DeliverableClass,
    reason: str | None = None,
) -> dict[str, Any]:
    """Run the adversarial C-suite review for one outward deliverable.

    Returns a summary dict. Files a ``csuite_review`` approval (and stamps
    ``csuite_review_pending``) on any HOLD / defect / failure; otherwise
    stamps ``csuite_approved`` with no card. Never raises — a review that
    cannot run fails closed (files the card for the human).
    """
    roles = review_roles_for(deliverable_class)
    output = _deliverable_output(task)
    findings, any_hold, errored = _run_review(
        engine, task, deliverable_class, roles, output
    )

    if any_hold or errored or _has_blocking_defects(findings):
        return _file_review_approval(
            engine, task, project, deliverable_class, findings,
            errored=errored, reason=reason,
        )

    _stamp_task(engine, task, {"csuite_approved": True})
    engine.audit.record(
        "csuite_review.passed",
        f"C-suite review passed clean ({deliverable_class.value})",
        detail={
            "task_id": getattr(task, "id", None),
            "deliverable_class": deliverable_class.value,
            "reviewers": roles,
        },
        project_id=getattr(project, "id", None),
    )
    return {
        "status": "approved",
        "deliverable_class": deliverable_class.value,
        "findings": findings,
    }


def _run_review(
    engine: Any,
    task: Any,
    deliverable_class: DeliverableClass,
    roles: list[str],
    output: str,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Convene each reviewer. Returns (findings, any_hold, errored).

    ``errored`` is True if *any* reviewer could not be run (agent missing /
    LLM failure) — the caller fails closed in that case.
    """
    findings: list[dict[str, Any]] = []
    any_hold = False
    errored = False
    title = getattr(task, "title", "") or ""
    for role in roles:
        try:
            agent = engine.registry.get(role)
            resp = agent.call(
                prompt=_REVIEW_PROMPT.format(
                    cls=deliverable_class.value,
                    title=title,
                    output=output[:_OUTPUT_EXCERPT_CHARS],
                    ok=_PASS_MARKER,
                    hold=_HOLD_MARKER,
                ),
                action_type="csuite_review",
            )
            text = getattr(resp, "text", "") or ""
        except Exception as exc:  # noqa: BLE001 — review must fail closed
            errored = True
            findings.append({
                "role": role,
                "verdict": _HOLD_MARKER,
                "defects": [f"Reviewer unavailable: {exc}"],
                "error": True,
            })
            continue
        verdict, defects = _parse_verdict(text)
        if verdict == _HOLD_MARKER:
            any_hold = True
        findings.append({
            "role": role,
            "verdict": verdict,
            "defects": defects,
        })
    return findings, any_hold, errored


def _parse_verdict(text: str) -> tuple[str, list[str]]:
    """Extract (verdict, defects) from a reviewer's reply.

    Verdict is HOLD if the HOLD token appears anywhere (adversarial bias);
    PASS only when HOLD is absent. Defects are the dashed lines, minus a
    'No defects.' sentinel.
    """
    upper = text.upper()
    verdict = _HOLD_MARKER if _HOLD_MARKER in upper else _PASS_MARKER
    defects: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("- ", "* ", "• ")):
            body = line[2:].strip()
            if body and body.lower().rstrip(".") != "no defects":
                defects.append(body)
    return verdict, defects


def _has_blocking_defects(findings: list[dict[str, Any]]) -> bool:
    """True if any reviewer logged at least one concrete defect."""
    return any(f.get("defects") for f in findings)


def _file_review_approval(
    engine: Any,
    task: Any,
    project: Any,
    deliverable_class: DeliverableClass,
    findings: list[dict[str, Any]],
    errored: bool,
    reason: str | None,
) -> dict[str, Any]:
    """File the ``csuite_review`` approval card and stamp the task pending."""
    task_id = getattr(task, "id", None)
    project_id = getattr(project, "id", None)
    title = getattr(task, "title", "") or ""
    summary = (
        f"C-suite review HOLD on outward deliverable: {title[:80]}"
    )
    request = engine.approvals.create(ApprovalRequest(
        action_type=ACTION_CSUITE_REVIEW,
        summary=summary,
        payload={
            "task_id": task_id,
            "project_id": project_id,
            "deliverable_class": deliverable_class.value,
            "findings": findings,
            "review_failed_closed": errored,
            "reason": reason,
        },
        directive_id=getattr(project, "triggers_directive_id", None),
        project_id=project_id,
        requested_by="csuite_review",
        severity="high",
    ))
    _stamp_task(engine, task, {
        "csuite_review_pending": True,
        "csuite_review_approval_id": request.id,
    })
    engine.audit.record(
        "csuite_review.held",
        f"C-suite review held outward deliverable ({deliverable_class.value})",
        detail={
            "task_id": task_id,
            "approval_id": request.id,
            "deliverable_class": deliverable_class.value,
            "review_failed_closed": errored,
        },
        project_id=project_id,
    )
    return {
        "status": "pending_review",
        "approval_id": request.id,
        "deliverable_class": deliverable_class.value,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Approval effects (wired through core/approval_effects.py)
# ---------------------------------------------------------------------------


def approve_csuite_review(engine: Any, request: Any) -> dict[str, Any]:
    """Founder overrides the HOLD: the deliverable is cleared to ship."""
    payload = request.payload or {}
    if payload.get("effect_applied"):
        return {"status": "already_applied"}
    task_id = payload.get("task_id")
    _stamp_task_by_id(engine, task_id, {
        "csuite_review_pending": False,
        "csuite_approved": True,
    })
    engine.approvals.update_payload(request.id, {"effect_applied": True})
    _comment(
        engine, request.id,
        "Approved over the C-suite HOLD — the deliverable is cleared to "
        "ship. The outward action (send / post / launch) can proceed.",
    )
    engine.audit.record(
        "csuite_review.approved",
        "Founder approved C-suite review — deliverable cleared to ship",
        detail={"approval_id": request.id, "task_id": task_id},
        project_id=request.project_id,
    )
    return {"status": "approved", "task_id": task_id}


def reject_csuite_review(engine: Any, request: Any) -> dict[str, Any]:
    """Founder upholds the HOLD: the deliverable does NOT ship."""
    payload = request.payload or {}
    if payload.get("effect_applied"):
        return {"status": "already_applied"}
    task_id = payload.get("task_id")
    _stamp_task_by_id(engine, task_id, {
        "csuite_review_pending": False,
        "csuite_approved": False,
        "csuite_rejected": True,
        "founder_action": (
            "C-suite HOLD upheld — this outward deliverable will not ship. "
            "Re-scope the task or have the team revise it before retrying."
        ),
    })
    engine.approvals.update_payload(request.id, {"effect_applied": True})
    _comment(
        engine, request.id,
        "C-suite HOLD upheld — the deliverable will not ship as-is.",
    )
    engine.audit.record(
        "csuite_review.rejected",
        "Founder upheld C-suite HOLD — deliverable will not ship",
        detail={"approval_id": request.id, "task_id": task_id},
        project_id=request.project_id,
    )
    return {"status": "rejected", "task_id": task_id}


def revision_requested_csuite_review(
    engine: Any, request: Any, feedback: str
) -> ApprovalRequest:
    """Founder asks for a revision: stamp the task and link a successor.

    Registered as the engine revision handler for ``csuite_review``. Stamps
    the task with the revision feedback and re-files a fresh pending card
    linked via ``predecessor_id`` (matching the engine's revision contract).
    """
    payload = request.payload or {}
    task_id = payload.get("task_id")
    _stamp_task_by_id(engine, task_id, {
        "csuite_review_pending": True,
        "csuite_revision_requested": True,
        "csuite_revision_feedback": feedback,
        "founder_action": (
            "C-suite review revision requested — the team must revise the "
            "deliverable per the feedback before it can ship."
        ),
    })
    successor = engine.approvals.create(ApprovalRequest(
        action_type=ACTION_CSUITE_REVIEW,
        summary=f"[Revised] {request.summary}",
        payload={**payload, "revision_hint": feedback, "effect_applied": False},
        directive_id=request.directive_id,
        project_id=request.project_id,
        requested_by=request.requested_by,
        severity=request.severity,
        predecessor_id=request.id,
    ))
    engine.audit.record(
        "csuite_review.revision_requested",
        "Founder requested revision on C-suite review",
        detail={
            "approval_id": request.id,
            "successor_id": successor.id,
            "task_id": task_id,
        },
        project_id=request.project_id,
    )
    return successor


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _deliverable_output(task: Any) -> str:
    """Best-effort extract the produced output text from the task result."""
    result = getattr(task, "result", None) or {}
    if isinstance(result, dict):
        return str(result.get("output") or "")
    return ""


def _stamp_task(engine: Any, task: Any, fields: dict[str, Any]) -> None:
    """Merge ``fields`` into the task result, preserving the rest."""
    _stamp_task_by_id(engine, getattr(task, "id", None), fields)


def _stamp_task_by_id(
    engine: Any, task_id: str | None, fields: dict[str, Any]
) -> None:
    if not task_id:
        return
    row = engine.projects.get_task(str(task_id))
    if row is None:
        return
    merged = {**(row.result or {}), **fields}
    engine.projects.update_task_status(row.id, row.status, result=merged)


def _comment(engine: Any, approval_id: str, body: str) -> None:
    """Best-effort system comment on the approval thread."""
    try:
        engine.approvals.add_comment(
            approval_id, body=body, by_type="system", by_id=None
        )
    except Exception:  # noqa: BLE001 — the effect outcome is already audited
        pass


__all__ = [
    "ACTION_CSUITE_REVIEW",
    "gate_completed_task",
    "approve_csuite_review",
    "reject_csuite_review",
    "request_csuite_review",
    "revision_requested_csuite_review",
]
