"""High-level workflow methods for the target feasibility review.

Contains ``TargetReviewOrchestrationMixin`` with:

- ``run_target_feasibility_review`` — entry point CEO + REST hit
- ``_target_feasibility_revision_handler`` — counter-proposal spawn
- ``_collect_prior_rounds`` — history walk for revisions
- ``_finalize_target_feasibility`` — post-approval state write
"""

from __future__ import annotations

from typing import Any

from kompany.state.approvals import ApprovalRequest
from kompany.state.targets import (
    CompanyTargets,
    get_state as get_targets_state,
    set_review_thread_id as set_targets_review_thread_id,
)

from kompany.core.target_review._helpers import _join_claim_texts


class TargetReviewOrchestrationMixin:
    """High-level orchestration methods for the target-review concern."""

    def run_target_feasibility_review(
        self,
        *,
        skip_llm: bool | None = None,
        revision_hint: str | None = None,
        prior_rounds: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Run a CEO+CFO+CoS feasibility pass on the founder's targets.

        Produces one ``approval_request(action_type='target_feasibility',
        severity='high')`` carrying a ``TargetReview`` payload:

            {
                "cfo_view": "...",
                "cos_view": "...",
                "ceo_proposal": "...",
                "rationale": "...",
                "original_targets": {...},
                "recommended_targets": {...},
                "rounds": [
                    {"generation": 1, "cfo_claims": [...], "cos_claims": [...],
                     "ceo_claims": [...], "revision_hint": null},
                    ...
                ]
            }

        The founder then approves (adopt recommended), rejects (keep
        original), or revises (counter-proposal flow) the request.

        ``skip_llm`` short-circuits the three LLM calls and falls back to
        a heuristic recommendation. Defaults to ``True`` when
        ``KOMPANY_TEST_MODE=1`` so tests don't need a live API key.

        ``revision_hint`` and ``prior_rounds`` carry founder counter-
        proposal context for re-reviews; see
        ``_target_feasibility_revision_handler``. When non-empty, the trio
        receives the hint (XML-tagged) and the historical rounds via the
        LLM prompt, and the new round number is computed from
        ``len(prior_rounds) + 1``.

        Returns ``None`` if no founder targets are set yet.
        """
        import os

        founder = get_targets_state(self.db, "founder")
        if founder is None:
            return None

        if skip_llm is None:
            skip_llm = os.environ.get("KOMPANY_TEST_MODE", "") == "1"

        try:
            cash = self.ledger.get_balance()
        except Exception:  # pragma: no cover
            cash = founder.initial_budget

        # Compute the heuristic recommendation up-front — even when LLMs
        # run, we use it as the baseline numeric proposal so the approval
        # payload always has parseable numbers.
        recommended = self._heuristic_recommend(founder, cash=cash)

        cfo_view = ""
        cos_view = ""
        ceo_proposal = ""
        rationale = ""

        from kompany.core.debate_models import Claim, Source, SourceType

        cfo_claims: list[Claim] = []
        cos_claims: list[Claim] = []
        ceo_claims: list[Claim] = []
        prior_rounds = list(prior_rounds or [])
        current_generation = len(prior_rounds) + 1
        # Iteration cap (PRD): rounds 1-3 do full trio + rebuttal; round
        # 4+ collapses to a CEO-only response. ``ceo_only`` flag drives
        # both the LLM dispatch and the payload "ceo_only" marker.
        ceo_only = current_generation >= 4

        if skip_llm:
            cfo_view = (
                f"At current cash ${cash:,.0f} and template defaults, "
                f"initial_budget ${founder.initial_budget:,.0f} is workable."
            )
            cos_view = (
                f"Revenue target ${founder.revenue_target:,.0f} is "
                f"{'ambitious' if founder.revenue_target > 5000 else 'reachable'} "
                f"in the proposed window."
            )
            ceo_proposal = (
                f"Compromise: revenue ${recommended.revenue_target:,.0f}, "
                f"deadline {recommended.deadline or 'unset'}."
            )
            rationale = "test-mode heuristic"
            # Heuristic / test path: emit one inferred-only claim per role so
            # the new payload shape is well-formed even without an LLM. The
            # ``inferred`` source_type makes distillation reject these — by
            # design: the heuristic is not a learning signal.
            cfo_claims = [
                Claim(
                    text=cfo_view,
                    evidence=[
                        Source(
                            source_type=SourceType.USER_INPUT,
                            source_ref="initial_budget",
                            claim_supported="budget-coverage",
                        ),
                        Source(
                            source_type=SourceType.LEDGER_ENTRY,
                            source_ref="balance",
                            claim_supported="cash-on-hand",
                        ),
                    ],
                )
            ]
            cos_claims = [
                Claim(
                    text=cos_view,
                    evidence=[
                        Source(
                            source_type=SourceType.USER_INPUT,
                            source_ref="revenue_target",
                            claim_supported="target-feasibility",
                        )
                    ],
                )
            ]
            ceo_claims = [
                Claim(
                    text=ceo_proposal,
                    evidence=[
                        Source(
                            source_type=SourceType.INFERRED,
                            source_ref="",
                            claim_supported="heuristic-compromise",
                        )
                    ],
                )
            ]
        else:
            try:
                if ceo_only:
                    # Iteration 4+: CFO/CoS claims are frozen from the
                    # last full round (round 3). CEO responds solo to the
                    # founder's latest counter-proposal.
                    frozen = self._frozen_round(prior_rounds)
                    cfo_claims = frozen["cfo_claims"]
                    cos_claims = frozen["cos_claims"]
                    ceo_claims, rationale = self._ceo_only_response(
                        founder,
                        cash=cash,
                        recommended=recommended,
                        revision_hint=revision_hint,
                        prior_rounds=prior_rounds,
                    )
                    # No fresh CFO/CoS calls happened on this iteration —
                    # only CEO. Carry the per-agent cost forward from the
                    # frozen round so the meters show the historical
                    # totals, then we'd attribute the current CEO cost
                    # but that's not surfaced via _ceo_only_response yet;
                    # zero it for now.
                    per_agent_cost = {
                        "cfo": frozen.get("per_agent_cost", {}).get("cfo", {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}),
                        "cos": frozen.get("per_agent_cost", {}).get("cos", {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}),
                        "ceo": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                    }
                else:
                    cfo_claims, cos_claims, ceo_claims, rationale, per_agent_cost = (
                        self._llm_target_review(
                            founder,
                            cash=cash,
                            recommended=recommended,
                            revision_hint=revision_hint,
                            prior_rounds=prior_rounds,
                        )
                    )
                cfo_view = _join_claim_texts(cfo_claims)
                cos_view = _join_claim_texts(cos_claims)
                ceo_proposal = _join_claim_texts(ceo_claims)
            except Exception as exc:  # noqa: BLE001
                # Never let a flaky LLM kill onboarding; fall back to the
                # heuristic with a note so the founder sees a useful
                # approval anyway.
                cfo_view = f"(LLM unavailable: {exc})"
                cos_view = "Falling back to heuristic recommendation."
                ceo_proposal = (
                    f"Heuristic compromise: revenue "
                    f"${recommended.revenue_target:,.0f}."
                )
                rationale = "llm_error_fallback"
                # Synthesize inferred-only claims so downstream code can
                # render even on LLM failure.
                cfo_claims = [Claim(text=cfo_view)]
                cos_claims = [Claim(text=cos_view)]
                ceo_claims = [Claim(text=ceo_proposal)]
                per_agent_cost = self._zero_per_agent_cost()
        # Heuristic / test path doesn't set per_agent_cost; default zeros.
        if "per_agent_cost" not in locals():
            per_agent_cost = self._zero_per_agent_cost()

        this_round_dump = {
            "generation": current_generation,
            "cfo_claims": [c.model_dump(mode="json") for c in cfo_claims],
            "cos_claims": [c.model_dump(mode="json") for c in cos_claims],
            "ceo_claims": [c.model_dump(mode="json") for c in ceo_claims],
            "revision_hint": revision_hint,
            "ceo_only": ceo_only,
            "per_agent_cost": per_agent_cost,
        }
        rounds_history = list(prior_rounds) + [this_round_dump]

        payload: dict[str, Any] = {
            "cfo_view": cfo_view,
            "cos_view": cos_view,
            "ceo_proposal": ceo_proposal,
            "cfo_claims": [c.model_dump(mode="json") for c in cfo_claims],
            "cos_claims": [c.model_dump(mode="json") for c in cos_claims],
            "ceo_claims": [c.model_dump(mode="json") for c in ceo_claims],
            "rationale": rationale,
            "original_targets": founder.model_dump(mode="json"),
            "recommended_targets": recommended.model_dump(mode="json"),
            "rounds": rounds_history,
            "generation": current_generation,
            "ceo_only": ceo_only,
            "revision_hint": revision_hint,
        }
        summary = (
            f"Team feasibility review for ${founder.revenue_target:,.0f} "
            f"revenue / "
            f"{founder.customer_target if founder.customer_target is not None else '—'} "
            f"customers / "
            f"{founder.deadline or 'no deadline'}"
        )
        request = ApprovalRequest(
            action_type="target_feasibility",
            summary=summary,
            payload=payload,
            severity="high",
            requested_by="ceo",
        )
        created = self.approvals.create(request)
        set_targets_review_thread_id(self.db, created.id)
        # Mirror the recommendation as a ``team_proposal`` snapshot so the
        # founder can read ``kompany target show`` and see the trio.
        self.set_targets(
            CompanyTargets(
                initial_budget=recommended.initial_budget,
                revenue_target=recommended.revenue_target,
                customer_target=recommended.customer_target,
                deadline=recommended.deadline,
                source="team_proposal",
            )
        )
        self.audit.record(
            event_type="company.target_feasibility_requested",
            action="Team produced feasibility recommendation",
            detail={
                "approval_id": created.id,
                "original": payload["original_targets"],
                "recommended": payload["recommended_targets"],
            },
        )
        return created.model_dump(mode="json")

    def _target_feasibility_revision_handler(
        self,
        original: ApprovalRequest,
        hint: str,
    ) -> ApprovalRequest:
        """Founder counter-proposal: **actually re-run the trio**.

        Earlier versions of this handler just stamped the hint into the
        successor's ``payload['revision_hint']`` — a UX fake move where
        the founder's counter never reached the agents. Now the handler:

        1. Walks the approval thread (``predecessor_id`` chain) to
           collect every prior round's claims into ``prior_rounds``.
        2. Calls :meth:`run_target_feasibility_review` with
           ``revision_hint=hint`` and ``prior_rounds=...`` so the
           CFO/CoS/CEO actually re-debate with the founder's counter
           injected into their prompts.
        3. The new approval is the result of that re-review — same
           ``action_type`` and ``predecessor_id`` link, but a fresh
           payload with new claims + a ``rounds`` history snapshot.

        Iteration cap: ``run_target_feasibility_review`` itself counts
        ``len(prior_rounds) + 1`` and switches to the CEO-only path at
        iteration 4 — this handler stays agnostic.

        If the re-review returns ``None`` (founder targets missing — a
        degenerate case) we fall back to the metadata-only successor so
        the approval thread still moves forward.
        """
        prior_rounds = self._collect_prior_rounds(original)
        review = None
        try:
            review = self.run_target_feasibility_review(
                revision_hint=hint,
                prior_rounds=prior_rounds,
            )
        except Exception:  # noqa: BLE001 - re-review must never break thread
            review = None

        if review is None:
            # Defensive fallback: keep the thread alive even when the
            # review couldn't run (founder targets missing or the LLM
            # path raised). This matches the legacy behaviour.
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
            created = self.approvals.create(successor)
            set_targets_review_thread_id(self.db, created.id)
            return created

        # The re-review created its own approval with no predecessor; we
        # need to link it back to ``original`` so the approval thread
        # walker sees the chain.
        new_approval_id = review["id"]
        new_summary = f"[Revised] {original.summary}"
        self.approvals.set_predecessor(new_approval_id, original.id)
        self.approvals.update_summary(new_approval_id, new_summary)
        # Refresh in-memory copy after the predecessor link / summary edit.
        refreshed = self.approvals.get(new_approval_id)
        if refreshed is None:  # pragma: no cover - defensive
            return self.approvals.get(new_approval_id)  # type: ignore[return-value]
        set_targets_review_thread_id(self.db, refreshed.id)
        return refreshed

    def _collect_prior_rounds(
        self,
        original: ApprovalRequest,
    ) -> list[dict[str, Any]]:
        """Reconstruct the historical rounds for a re-review.

        We walk the approval thread that contains ``original``, keep
        only ``target_feasibility`` rows in oldest-first order, and
        flatten their ``rounds`` payload arrays. We dedupe by
        ``generation`` so a re-review that already wrote its own
        ``rounds`` array doesn't double-count.
        """
        thread = self.approvals.list_thread(original.id)
        flat: list[dict[str, Any]] = []
        seen_generations: set[int] = set()
        for row in thread:
            if row.action_type != "target_feasibility":
                continue
            payload = row.payload or {}
            rounds = payload.get("rounds")
            if isinstance(rounds, list):
                for r in rounds:
                    if not isinstance(r, dict):
                        continue
                    gen = r.get("generation")
                    if gen in seen_generations:
                        continue
                    if isinstance(gen, int):
                        seen_generations.add(gen)
                    flat.append(r)
                continue
            # Legacy approval (no ``rounds`` array): synthesise one entry
            # from the flat ``cfo_claims/cos_claims/ceo_claims`` keys so
            # the next round has *something* to read.
            if any(k in payload for k in ("cfo_claims", "cos_claims", "ceo_claims")):
                gen = len(flat) + 1
                if gen not in seen_generations:
                    seen_generations.add(gen)
                    flat.append({
                        "generation": gen,
                        "cfo_claims": payload.get("cfo_claims") or [],
                        "cos_claims": payload.get("cos_claims") or [],
                        "ceo_claims": payload.get("ceo_claims") or [],
                        "revision_hint": payload.get("revision_hint"),
                        "ceo_only": payload.get("ceo_only", False),
                    })
        # Sort by generation so out-of-order chains still produce a
        # monotonic history (defensive).
        flat.sort(key=lambda r: r.get("generation") or 0)
        return flat

    def _finalize_target_feasibility(
        self,
        request: ApprovalRequest,
        *,
        outcome: str,
    ) -> None:
        """Hook called after approve_request/reject_request resolves a
        ``target_feasibility`` row.

        On approve → write recommended_targets as ``agreed``.
        On reject → write original_targets (founder's) as ``agreed``.
        """
        payload = request.payload or {}
        try:
            if outcome == "approved":
                src = payload.get("recommended_targets") or {}
            else:
                src = payload.get("original_targets") or {}
            agreed = CompanyTargets(
                initial_budget=float(src.get("initial_budget", 0.0) or 0.0),
                revenue_target=float(src.get("revenue_target", 0.0) or 0.0),
                customer_target=src.get("customer_target"),
                deadline=src.get("deadline"),
                source="agreed",
            )
        except Exception:
            return
        self.set_targets(agreed)
        # Wipe stale first-move drafts: their week_plan / success_metric
        # / cost were proposed against the PREVIOUS agreed_targets. If
        # the founder counter-proposed and the numbers changed, the
        # team's directives must regenerate against the new targets;
        # otherwise step 5 shows directives that don't match the agreed
        # plan. The next call to propose_first_directives produces
        # fresh ones.
        try:
            self.db.execute("DELETE FROM projects WHERE status = 'draft'")
            self.db.commit()
        except Exception:  # noqa: BLE001 — best-effort
            pass
        self.audit.record(
            event_type="company.targets_agreed",
            action="Founder finalized target feasibility review",
            detail={
                "approval_id": request.id,
                "outcome": outcome,
                "agreed": agreed.model_dump(mode="json"),
            },
        )
