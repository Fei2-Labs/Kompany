"""Post-resolve effects for ``self_update_proposal`` approvals (PRD D3).

Approve → promote according to the installation role (07-24): a trusted
``maintainer`` pushes the proposal branch to an allowlisted origin with a
scoped credential and opens a PR (the MERGE stays human, on GitHub); any
other role gets a local patch file. Reject → record + keep the branch
local for autopsy. Wired into the same dispatch as the harness
budget effects (``core/approval_effects.py``), sharing its
``effect_applied`` idempotency stamp.
"""

from __future__ import annotations

from typing import Any

from kompany.core.installation_role import resolve_installation_role
from kompany.core.self_update.promotion import (
    DEFAULT_ALLOWED_REPOS,
    destination_allowed,
    export_patch,
    load_credential,
    open_pull_request,
    push_with_credential,
)
from kompany.core.self_update.workspace import ensure_clone
from kompany.state.models import ApprovalRequest

ACTION_SELF_UPDATE = "self_update_proposal"


def approve_self_update(engine: Any, request: ApprovalRequest) -> dict[str, Any]:
    """Promote the approved proposal according to the installation role.

    ``maintainer`` (trusted role file) → push the ``self-update/*`` branch to
    an allowlisted Core/Pro origin with a scoped credential and open a PR.
    Any other role → export a patch file; nothing leaves the machine. Every
    decision is audited with role, repo, branch, credential kind, outcome.
    """
    payload = request.payload or {}
    proposal_id = payload.get("proposal_id")
    branch = payload.get("branch")
    store = getattr(engine, "self_update_proposals", None)
    if not proposal_id or not branch or store is None:
        return {"status": "invalid_payload"}

    role = resolve_installation_role()
    clone = ensure_clone(engine.settings.data_dir)
    base = {
        "approval_id": request.id,
        "proposal_id": proposal_id,
        "branch": branch,
        "installation_role": role.role,
        "role_trusted": role.trusted,
        "role_reason": role.reason,
    }

    if not role.can_promote:
        return _export_patch_effect(engine, request, store, clone, base)

    allowed = tuple(getattr(engine.settings, "self_update_allowed_repos", None) or DEFAULT_ALLOWED_REPOS)
    ok, reason, slug = destination_allowed(clone, str(branch), allowed)
    if not ok:
        return _refuse(engine, request, base, reason)
    cred, cred_detail = load_credential(
        ambient_ok=bool(getattr(engine.settings, "self_update_ambient_credentials", True))
    )
    if cred is None:
        return _refuse(engine, request, {**base, "repo": slug}, cred_detail)
    base.update({"repo": slug, "credential": cred.kind, "credential_fingerprint": cred.fingerprint})
    engine.audit.record(
        "approval_effect.self_update_credential",
        f"Self-update {proposal_id}: using {cred.kind} credential for {slug}",
        detail={**base, "credential_detail": cred_detail},
    )

    pushed, push_detail = push_with_credential(clone, str(branch), cred)
    if not pushed:
        # No stamp: a later re-approve retries the push (auth fixed,
        # network back). Honest state on the thread + the proposal row.
        store.update(proposal_id, test_summary=f"push failed: {push_detail}")
        _comment(
            engine,
            request.id,
            f"Could not push {branch}: {push_detail}. Fix the promotion credential/network "
            "and approve again — the branch is intact in the local clone.",
        )
        engine.audit.record(
            "approval_effect.self_update_push_failed",
            f"Approved self-update {proposal_id} but push failed",
            detail={**base, "error": push_detail, "outcome": "push_failed"},
        )
        return {"status": "push_failed", "detail": push_detail}

    instruction = str(payload.get("instruction") or "")[:72]
    pr_url, pr_detail = open_pull_request(
        clone,
        str(slug),
        str(branch),
        title=f"Self-update: {instruction}",
        body=(
            "Founder-approved self-update proposal "
            f"`{proposal_id}` (tier {payload.get('tier')}).\n\n"
            f"Instruction: {payload.get('instruction')}\n\n"
            f"Test summary:\n```\n{payload.get('test_summary')}\n```\n"
        ),
        cred=cred,
    )
    store.update(proposal_id, status="approved", approval_id=request.id)
    engine.approvals.update_payload(
        request.id,
        {
            "effect_applied": True,
            "pushed_branch": branch,
            "pr_url": pr_url,
            "installation_role": role.role,
            "repo": slug,
            "credential": cred.kind,
        },
    )
    _comment(
        engine,
        request.id,
        f"Branch {branch} pushed to {slug}. "
        + (f"PR ready: {pr_url}" if pr_url else f"Open the PR manually ({pr_detail}).")
        + " Merging on GitHub remains your call; after merge, rebuild and "
        "reinstall via the existing scripts.",
    )
    engine.audit.record(
        "approval_effect.self_update_pushed",
        f"Self-update {proposal_id} approved — branch pushed",
        detail={**base, "pr_url": pr_url, "pr_detail": pr_detail, "outcome": "pushed"},
    )
    return {"status": "pushed", "branch": branch, "pr_url": pr_url, "installation_role": role.role}


def _export_patch_effect(engine: Any, request: ApprovalRequest, store: Any, clone: Any,
                         base: dict[str, Any]) -> dict[str, Any]:
    proposal_id, branch = base["proposal_id"], str(base["branch"])
    path, detail = export_patch(clone, branch, engine.settings.data_dir, proposal_id)
    if path is None:
        _comment(engine, request.id, f"Could not export the patch: {detail}. Approve again to retry.")
        engine.audit.record(
            "approval_effect.self_update_patch_failed",
            f"Self-update {proposal_id}: patch export failed",
            detail={**base, "error": detail, "outcome": "patch_failed"},
        )
        return {"status": "patch_failed", "detail": detail}
    store.update(proposal_id, status="approved", approval_id=request.id)
    engine.approvals.update_payload(
        request.id,
        {"effect_applied": True, "patch_path": str(path), "installation_role": base["installation_role"]},
    )
    _comment(
        engine,
        request.id,
        f"Installation role is '{base['installation_role']}' ({base['role_reason']}) — this instance "
        f"cannot push branches or open pull requests. {detail}. Review it and submit it upstream "
        "yourself; the branch stays in the local clone.",
    )
    engine.audit.record(
        "approval_effect.self_update_patch_exported",
        f"Self-update {proposal_id} approved — patch exported (role {base['installation_role']})",
        detail={**base, "patch_path": str(path), "outcome": "patch_exported"},
    )
    return {"status": "patch_exported", "patch_path": str(path), "installation_role": base["installation_role"]}


def _refuse(engine: Any, request: ApprovalRequest, base: dict[str, Any], reason: str) -> dict[str, Any]:
    """Maintainer role but the destination/credential gate failed. No stamp
    (fix + approve again); loud on the thread and in the audit log."""
    _comment(
        engine,
        request.id,
        f"Promotion refused: {reason}. Nothing was pushed. Fix it and approve again.",
    )
    engine.audit.record(
        "approval_effect.self_update_promotion_refused",
        f"Self-update {base['proposal_id']}: promotion refused — {reason}",
        detail={**base, "reason": reason, "outcome": "refused"},
    )
    return {"status": "promotion_refused", "detail": reason}


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
