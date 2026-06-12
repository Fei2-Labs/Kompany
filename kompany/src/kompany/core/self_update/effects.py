"""Post-resolve effects for ``self_update_proposal`` approvals (PRD D3).

Approve → push the proposal branch to origin + best-effort ``gh pr
create`` (the MERGE stays human, on GitHub); reject → record + keep the
branch local for autopsy. Wired into the same dispatch as the harness
budget effects (``core/approval_effects.py``), sharing its
``effect_applied`` idempotency stamp.
"""

from __future__ import annotations

from typing import Any

from kompany.core.self_update.workspace import create_pr, ensure_clone, push_branch
from kompany.state.models import ApprovalRequest

ACTION_SELF_UPDATE = "self_update_proposal"


def approve_self_update(engine: Any, request: ApprovalRequest) -> dict[str, Any]:
    """Push the approved proposal's branch; record the PR URL when gh works."""
    payload = request.payload or {}
    proposal_id = payload.get("proposal_id")
    branch = payload.get("branch")
    store = getattr(engine, "self_update_proposals", None)
    if not proposal_id or not branch or store is None:
        return {"status": "invalid_payload"}

    clone = ensure_clone(engine.settings.data_dir)
    pushed, push_detail = push_branch(clone, str(branch))
    if not pushed:
        # No stamp: a later re-approve retries the push (auth fixed,
        # network back). Honest state on the thread + the proposal row.
        store.update(proposal_id, test_summary=f"push failed: {push_detail}")
        _comment(
            engine,
            request.id,
            f"Could not push {branch}: {push_detail}. Fix git auth/network "
            "and approve again — the branch is intact in the local clone.",
        )
        engine.audit.record(
            "approval_effect.self_update_push_failed",
            f"Approved self-update {proposal_id} but push failed",
            detail={
                "approval_id": request.id,
                "proposal_id": proposal_id,
                "branch": branch,
                "error": push_detail,
            },
        )
        return {"status": "push_failed", "detail": push_detail}

    instruction = str(payload.get("instruction") or "")[:72]
    pr_url, pr_detail = create_pr(
        clone,
        str(branch),
        title=f"Self-update: {instruction}",
        body=(
            "Founder-approved self-update proposal "
            f"`{proposal_id}` (tier {payload.get('tier')}).\n\n"
            f"Instruction: {payload.get('instruction')}\n\n"
            f"Test summary:\n```\n{payload.get('test_summary')}\n```\n"
        ),
    )
    store.update(proposal_id, status="approved", approval_id=request.id)
    engine.approvals.update_payload(
        request.id,
        {"effect_applied": True, "pushed_branch": branch, "pr_url": pr_url},
    )
    _comment(
        engine,
        request.id,
        f"Branch {branch} pushed. "
        + (f"PR ready: {pr_url}" if pr_url else f"Open the PR manually ({pr_detail}).")
        + " Merging on GitHub remains your call; after merge, rebuild and "
        "reinstall via the existing scripts.",
    )
    engine.audit.record(
        "approval_effect.self_update_pushed",
        f"Self-update {proposal_id} approved — branch pushed",
        detail={
            "approval_id": request.id,
            "proposal_id": proposal_id,
            "branch": branch,
            "pr_url": pr_url,
            "pr_detail": pr_detail,
        },
    )
    return {"status": "pushed", "branch": branch, "pr_url": pr_url}


def reject_self_update(engine: Any, request: ApprovalRequest) -> dict[str, Any]:
    """Record the rejection; the branch stays local for autopsy."""
    payload = request.payload or {}
    proposal_id = payload.get("proposal_id")
    store = getattr(engine, "self_update_proposals", None)
    if proposal_id and store is not None:
        store.update(proposal_id, status="rejected", approval_id=request.id)
    engine.audit.record(
        "approval_effect.self_update_rejected",
        f"Self-update {proposal_id} rejected — branch kept local",
        detail={
            "approval_id": request.id,
            "proposal_id": proposal_id,
            "branch": payload.get("branch"),
        },
    )
    return {"status": "rejected", "proposal_id": proposal_id}


def _comment(engine: Any, approval_id: str, body: str) -> None:
    try:
        engine.approvals.add_comment(
            approval_id, body=body, by_type="system", by_id=None
        )
    except Exception:  # noqa: BLE001 — outcome already audited
        pass


__all__ = ["ACTION_SELF_UPDATE", "approve_self_update", "reject_self_update"]
