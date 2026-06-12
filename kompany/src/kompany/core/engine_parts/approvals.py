"""Approvals inbox, approve/reject, revision threads, targets surface.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations

from typing import Callable

from kompany.state.models import ApprovalRequest
from kompany.state.targets import CompanyTargets, TargetsBundle, get_bundle as get_targets_bundle, get_targets as get_company_targets, set_targets as set_company_targets



class ApprovalsMixin:
    def list_approvals(self) -> list[dict]:
        """List pending approval requests."""
        return [request.model_dump(mode="json") for request in self.approvals.list_pending()]

    def inbox(
        self,
        statuses: tuple[str, ...] = ("pending", "snoozed"),
    ) -> list[dict]:
        """Return the player's RPG inbox: actionable approvals + counts.

        ``pending`` and ``snoozed`` rows are surfaced by default — terminal
        states (``approved``/``rejected``/``revision_requested``/``cancelled``)
        are read via ``approval show <id>`` or the episode retrospective.

        Each row carries ``comment_count`` so the inbox renderer can show
        "3 comments" without a second per-row query.
        """
        rows: list[dict] = []
        for status in statuses:
            for request in self.approvals.list_by_status(status=status):
                payload = request.model_dump(mode="json")
                payload["comment_count"] = len(
                    self.approvals.list_comments(request.id)
                )
                rows.append(payload)
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows

    def get_approval(self, request_id: str) -> dict | None:
        """Return one approval + its full thread (predecessors + successors)
        + comments. Used by ``approval show`` across all four surfaces."""
        request = self.approvals.get(request_id)
        if request is None:
            return None
        thread = [r.model_dump(mode="json") for r in self.approvals.list_thread(request_id)]
        comments = [
            c.model_dump(mode="json")
            for c in self.approvals.list_comments(request_id)
        ]
        result = request.model_dump(mode="json")
        result["thread"] = thread
        result["comments"] = comments
        return result

    def agent_work_summary(self) -> dict[str, dict]:
        """Per-agent task-history summary (06-12-panel-truthfulness #22).

        Cheap GROUP BY over ``tasks.assigned_agent``: lets the UI tell
        "worked, but no project has closed yet" apart from "never
        worked" when ``project_episodes`` is still empty. Keys are
        lowercase roles; values carry stable keys ``delivered`` /
        ``completed`` / ``failed`` / ``total`` / ``last_active``
        (additive-only — Output Stability Rule).
        """
        rows = self.db.execute(
            """SELECT lower(assigned_agent) AS role,
                      SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
                      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                      COUNT(*) AS total,
                      MAX(updated_at) AS last_active
               FROM tasks
               GROUP BY lower(assigned_agent)"""
        ).fetchall()
        return {
            r["role"]: {
                "delivered": int(r["delivered"] or 0),
                "completed": int(r["completed"] or 0),
                "failed": int(r["failed"] or 0),
                "total": int(r["total"] or 0),
                "last_active": r["last_active"],
            }
            for r in rows
            if r["role"]
        }

    # ------------------------------------------------------------------
    # Self-update pipeline (06-12-self-update-pipeline) — governed code
    # self-modification. Logic lives in core/self_update/; thin delegates.
    # ------------------------------------------------------------------

    def self_update_propose(self, instruction: str) -> dict:
        """Run the governed propose flow; returns the proposal row."""
        from kompany.core.self_update.pipeline import propose_self_update

        text = (instruction or "").strip()
        if not text:
            raise ValueError("instruction must be a non-empty string")
        return propose_self_update(self, text)

    def self_update_list(self, limit: int = 20) -> list[dict]:
        return self.self_update_proposals.list(limit=limit)

    def self_update_show(self, proposal_id: str) -> dict | None:
        return self.self_update_proposals.get(proposal_id)

    def approve_request(
        self,
        request_id: str,
        approved_by: str = "master",
        comment_body: str | None = None,
    ) -> dict | None:
        """Approve a pending request."""
        request = self.approvals.approve(
            request_id,
            approved_by=approved_by,
            comment_body=comment_body,
        )
        if request:
            self.audit.record(
                "approval.approved",
                "Approved pending request",
                detail={"approval_id": request.id, "action_type": request.action_type},
                directive_id=request.directive_id,
                project_id=request.project_id,
            )
            # Action-type-specific post-resolve hook. Keep this list short
            # and inline so new action_types are easy to spot.
            if request.action_type == "target_feasibility":
                self._finalize_target_feasibility(request, outcome="approved")
            if request.action_type == "glossary_review":
                self._finalize_glossary_review(request, outcome="approved")
            # Deferred tool action (#4/#5): the founder approved a
            # proposed external action (e.g. send email). Execute it for
            # real now — agent proposes → founder approves → it actually
            # happens, with a real-result audit. Logic lives in
            # core/tool_actions.py (routed via approval_effects below);
            # ``tool_result`` kept as the surface-facing result key.
            if request.action_type == "tool_action":
                from kompany.core import approval_effects

                effect = approval_effects.apply_post_approve_effect(self, request)
                payload = request.model_dump(mode="json")
                payload["effect"] = effect
                payload["tool_result"] = effect.get("result", effect)
                return payload
            # Harness budget approvals (PR5): approving must actually
            # fund the envelope / raise the task cap. Logic lives in
            # core/approval_effects.py (subscription_fee precedent);
            # the effect itself guards idempotency on replayed approves.
            from kompany.core import approval_effects

            if request.action_type in approval_effects.HARNESS_EFFECT_ACTIONS:
                effect = approval_effects.apply_post_approve_effect(self, request)
                payload = request.model_dump(mode="json")
                payload["effect"] = effect
                return payload
            return request.model_dump(mode="json")
        return None

    def reject_request(
        self,
        request_id: str,
        rejected_by: str = "master",
        reason: str | None = None,
        comment_body: str | None = None,
    ) -> dict | None:
        """Reject a pending request."""
        request = self.approvals.reject(
            request_id,
            rejected_by=rejected_by,
            reason=reason,
            comment_body=comment_body,
        )
        if request:
            self.audit.record(
                "approval.rejected",
                "Rejected pending request",
                detail={
                    "approval_id": request.id,
                    "action_type": request.action_type,
                    "reason": reason,
                },
                directive_id=request.directive_id,
                project_id=request.project_id,
            )
            if request.action_type == "target_feasibility":
                self._finalize_target_feasibility(request, outcome="rejected")
            if request.action_type == "glossary_review":
                self._finalize_glossary_review(request, outcome="rejected")
            # Harness budget approvals (PR5): a "no" still needs a real
            # next step on the task (re-scope / abandon), never a dead end.
            from kompany.core import approval_effects

            if request.action_type in approval_effects.HARNESS_EFFECT_ACTIONS:
                effect = approval_effects.apply_post_reject_effect(self, request)
                payload = request.model_dump(mode="json")
                payload["effect"] = effect
                return payload
            return request.model_dump(mode="json")
        return None

    # ------------------------------------------------------------------
    # Approval thread + RPG inbox (05-18-approval-thread-and-rpg)
    # ------------------------------------------------------------------

    def register_revision_handler(
        self,
        action_type: str,
        handler: Callable[[ApprovalRequest, str], ApprovalRequest],
    ) -> None:
        """Register a revision handler for one ``action_type``.

        The handler is invoked when a player ``request_revision`` lands
        with a counter-proposal hint. Signature:

            handler(original_approval: ApprovalRequest, hint_text: str)
                -> ApprovalRequest

        The handler must **persist** the returned ``ApprovalRequest`` (via
        ``self.approvals.create`` or equivalent) and set its
        ``predecessor_id`` to ``original_approval.id`` so ``list_thread``
        can link the two rows.

        Each ``action_type`` may have at most one handler; re-registering
        replaces the previous one (handy for tests and for swapping in an
        LLM-driven replacement at boot).
        """
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._revision_handlers[action_type] = handler

    def _default_revision_handler(
        self,
        original: ApprovalRequest,
        hint: str,
    ) -> ApprovalRequest:
        """Fallback when no caller-specific handler is registered.

        Copies the original ``payload``, stamps the player's counter
        proposal into ``payload['revision_hint']``, links via
        ``predecessor_id``, and re-submits as ``pending`` for player
        approval. **Critically**, the new approval is created with the
        same ``action_type`` but the handler does *not* trigger another
        revision pathway — that would loop.
        """
        new_payload = {**(original.payload or {}), "revision_hint": hint}
        successor = ApprovalRequest(
            action_type=original.action_type,
            summary=f"[Revised] {original.summary}",
            payload=new_payload,
            directive_id=original.directive_id,
            project_id=original.project_id,
            requested_by=original.requested_by,
            severity=original.severity,
            predecessor_id=original.id,
        )
        return self.approvals.create(successor)

    def request_approval_revision(
        self,
        request_id: str,
        counter: str,
        by_type: str = "user",
        by_id: str | None = None,
        comment_body: str | None = None,
    ) -> dict | None:
        """Player counter-proposal flow.

        1. Original approval -> ``revision_requested`` (terminal).
        2. The counter text lands as a comment on the original.
        3. The action-type's revision handler (or the default fallback)
           creates a fresh ``pending`` approval that links back via
           ``predecessor_id``.

        Returns a dict with ``original`` and ``successor`` payloads, or
        ``None`` if the original was not found.
        """
        original = self.approvals.get(request_id)
        if original is None:
            return None
        # Use comment_body for the "additional context" if provided; the
        # ``counter`` text is always preserved as its own thread comment.
        original_after = self.approvals.request_revision(
            request_id=request_id,
            comment_body=counter,
            by_type=by_type,
            by_id=by_id,
        )
        if original_after is None:
            return None
        if comment_body:
            self.approvals.add_comment(
                approval_id=request_id,
                body=comment_body,
                by_type=by_type,
                by_id=by_id,
            )
        handler = self._revision_handlers.get(
            original.action_type, self._default_revision_handler
        )
        successor = handler(original, counter)
        self.audit.record(
            "approval.revision_requested",
            "Player requested revision",
            detail={
                "approval_id": original.id,
                "successor_id": successor.id,
                "action_type": original.action_type,
            },
            directive_id=original.directive_id,
            project_id=original.project_id,
        )
        return {
            "original": original_after.model_dump(mode="json"),
            "successor": successor.model_dump(mode="json"),
        }

    def snooze_approval(
        self,
        request_id: str,
        minutes: int,
        by_type: str = "user",
        by_id: str | None = None,
        comment_body: str | None = None,
    ) -> dict | None:
        """Snooze an approval; the watchdog auto-unsnoozes when due."""
        request = self.approvals.snooze(
            request_id=request_id,
            minutes=minutes,
            by_type=by_type,
            by_id=by_id,
            comment_body=comment_body,
        )
        if request is None:
            return None
        self.audit.record(
            "approval.snoozed",
            f"Approval snoozed for {minutes}m",
            detail={
                "approval_id": request.id,
                "minutes": minutes,
                "snoozed_until": (
                    request.snoozed_until.isoformat()
                    if request.snoozed_until
                    else None
                ),
            },
            directive_id=request.directive_id,
            project_id=request.project_id,
        )
        return request.model_dump(mode="json")

    def cancel_approval(
        self,
        request_id: str,
        reason: str | None = None,
        by_type: str = "user",
        by_id: str | None = None,
        comment_body: str | None = None,
    ) -> dict | None:
        """Cancel an approval (terminal): the player says 'don't pursue this'."""
        request = self.approvals.cancel(
            request_id=request_id,
            reason=reason,
            by_type=by_type,
            by_id=by_id,
            comment_body=comment_body,
        )
        if request is None:
            return None
        self.audit.record(
            "approval.cancelled",
            "Approval cancelled",
            detail={
                "approval_id": request.id,
                "reason": reason,
            },
            directive_id=request.directive_id,
            project_id=request.project_id,
        )
        return request.model_dump(mode="json")

    def comment_on_approval(
        self,
        request_id: str,
        body: str,
        by_type: str = "user",
        by_id: str | None = None,
    ) -> dict | None:
        """Append a free-form comment to an approval thread.

        Allowed on any state — terminal or not — so the player can leave
        retrospective notes on a closed thread.
        """
        if self.approvals.get(request_id) is None:
            return None
        comment = self.approvals.add_comment(
            approval_id=request_id,
            body=body,
            by_type=by_type,
            by_id=by_id,
        )
        return comment.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Company targets + team feasibility review
    # ------------------------------------------------------------------

    def get_targets(self) -> CompanyTargets:
        """Return the authoritative targets (``agreed`` > founder fallback)."""
        return get_company_targets(self.db)

    def get_targets_bundle(self) -> TargetsBundle:
        """Return all three states + the review approval id."""
        return get_targets_bundle(self.db)

    def set_targets(self, targets: CompanyTargets) -> CompanyTargets:
        """Persist a target snapshot keyed by ``targets.source``."""
        return set_company_targets(self.db, targets)

