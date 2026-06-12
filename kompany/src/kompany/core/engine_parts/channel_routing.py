"""CEO-channel routing, clarify/answer, spend gate.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations

import time
from typing import Any

from kompany.core.directive import (
    Directive,
    DirectiveResult,
    DirectiveStatus,
    DirectiveType,
)
from kompany.state.models import Decision, SessionStatus



class ChannelRoutingMixin:
    # ------------------------------------------------------------------
    # CEO-channel routing (06-03-ceo-channel)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_channel_route(classification, clarify_capped: bool) -> str:
        """Map a classification onto execute | clarify | answer.

        ``informational`` always maps to ``answer`` (a pure status query
        needs a reply, not dispatch) regardless of the emitted route. At the
        clarify cap a ``clarify`` route is rejected and forced to a
        deterministic resolution: ``answer`` for informational directives,
        otherwise ``execute`` — so the conversation always terminates rather
        than looping (PRD clarify-runaway guard).
        """
        route = (classification.route or "execute").strip().lower()
        is_question = classification.directive_type == "informational"
        if is_question:
            return "answer"
        if route == "clarify":
            if clarify_capped or not (classification.clarify_question or "").strip():
                # Cap reached, or model asked to clarify without a question:
                # commit deterministically. Non-question intent → execute.
                return "execute"
            return "clarify"
        if route == "answer":
            return "answer"
        return "execute"

    def _handle_clarify(
        self,
        directive: Directive,
        classification,
        session_id: str,
        start_time: float,
    ) -> DirectiveResult:
        """Record the CEO's clarify question and pause the session.

        The session moves to ``clarifying`` and waits for the founder's reply
        (which re-enters via ``process_directive(reply, session_id)``).
        """
        question = (classification.clarify_question or "").strip() or (
            "Could you clarify what outcome you want here?"
        )
        cost = self.cost_tracker.run_total()
        self.channel.add_turn(
            session_id,
            role="ceo",
            content=question,
            kind="clarify_question",
            cost=cost,
            directive_id=directive.id,
        )
        self.channel.update_session_state(
            session_id,
            SessionStatus.CLARIFYING,
            route="clarify",
            directive_id=directive.id,
        )
        self.audit.record(
            "directive.clarify",
            "CEO asked a clarifying question",
            detail={"question": question[:500]},
            agent_role="ceo",
            directive_id=directive.id,
        )
        self.journal.log(Decision(
            directive_id=directive.id,
            directive_type=directive.directive_type.value if directive.directive_type else "unknown",
            raw_input=directive.raw_input,
            classification=classification.model_dump() if classification else {},
            result={"status": "clarify", "message": question[:500]},
            agents_involved=["ceo"],
            total_ai_cost=cost,
            duration_seconds=time.time() - start_time,
        ))
        return DirectiveResult(
            directive=directive,
            status="clarify",
            message=question,
            session_id=session_id,
            total_ai_cost=cost,
            agents_used=["ceo"],
        )

    def _handle_answer(
        self,
        directive: Directive,
        classification,
        ceo,
        session_id: str,
        start_time: float,
        session_context: str | None = None,
        recent_context: str | None = None,
    ) -> DirectiveResult:
        """Answer a pure question — a real CEO reply, no project/dispatch.

        Returns ``status="proposed"`` when the CEO's reply contains an
        actionable proposal (``has_proposal=true``) — session enters
        ``PROPOSED`` (non-terminal) and the founder can click GO to execute.
        Returns ``status="completed"`` for pure informational answers —
        session closes ``ANSWERED`` (terminal, existing behaviour).

        ``recent_context`` — last N turns across ALL sessions injected for
        cross-session coherence (Decision 3, PR2).
        """
        directive.directive_type = DirectiveType.INFORMATIONAL
        self.audit.record(
            "directive.routed",
            "Routed directive to answer (CEO reply)",
            detail={"route": "answer"},
            directive_id=directive.id,
        )
        company_context, used_cfo = self._compose_answer_context()
        resp = ceo.answer(
            directive.raw_input,
            company_context,
            session_context=session_context,
            recent_context=recent_context,
            directive_id=directive.id,
        )
        parsed = resp.parsed  # AnswerResponse
        answer_text = (parsed.text or "").strip() or (
            "I don't have enough information to answer that right now."
        )
        total_cost = self.cost_tracker.run_total()
        agents_used = ["ceo"]
        if used_cfo:
            agents_used.append("cfo")

        directive.status = DirectiveStatus.COMPLETED
        has_proposal = bool(parsed.has_proposal and parsed.proposal_directive)

        if has_proposal:
            # CEO returned a concrete proposal — park the session so the
            # founder can approve by clicking GO.
            self._stash_proposal_directive(session_id, parsed.proposal_directive)
            result = DirectiveResult(
                directive=directive,
                status="proposed",
                message=answer_text,
                session_id=session_id,
                total_ai_cost=total_cost,
                agents_used=agents_used,
            )
            self.channel.add_turn(
                session_id,
                role="ceo",
                content=result.message,
                kind="final",
                cost=result.total_ai_cost,
                directive_id=directive.id,
            )
            self.channel.update_session_state(
                session_id,
                SessionStatus.PROPOSED,
                route="answer",
                directive_id=directive.id,
            )
        else:
            # Pure informational answer — close session as ANSWERED (terminal).
            result = DirectiveResult(
                directive=directive,
                status="completed",
                message=answer_text,
                session_id=session_id,
                total_ai_cost=total_cost,
                agents_used=agents_used,
            )
            self.channel.add_turn(
                session_id,
                role="ceo",
                content=result.message,
                kind="final",
                cost=result.total_ai_cost,
                directive_id=directive.id,
            )
            self.channel.update_session_state(
                session_id,
                SessionStatus.ANSWERED,
                route="answer",
                directive_id=directive.id,
            )

        self.journal.log(Decision(
            directive_id=directive.id,
            directive_type="informational",
            raw_input=directive.raw_input,
            classification=classification.model_dump() if classification else {},
            result={"status": result.status, "message": result.message[:500]},
            agents_involved=result.agents_used,
            total_ai_cost=result.total_ai_cost,
            duration_seconds=time.time() - start_time,
        ))
        self.audit.record(
            "journal.recorded",
            "Recorded directive decision journal entry",
            detail={"status": result.status},
            directive_id=directive.id,
        )
        self.audit.record(
            "directive.completed",
            "Completed directive processing (answer)",
            detail={"status": result.status, "has_proposal": has_proposal},
            directive_id=directive.id,
        )
        return result

    # ------------------------------------------------------------------
    # CEO-channel threshold spend gate (PR2, Decision 7)
    # ------------------------------------------------------------------

    # Founder-configurable spend threshold (EUR). The ``company_config`` key
    # the founder rules / settings UI writes to; default 1.0. A directive whose
    # estimated cost exceeds this (strictly greater — at exactly the threshold
    # the directive runs) is gated for explicit founder GO. ``master``-tier
    # directives are always gated regardless of cost.
    CHANNEL_SPEND_THRESHOLD_KEY = "channel_spend_threshold_eur"
    CHANNEL_SPEND_THRESHOLD_DEFAULT = 1.0

    def _channel_spend_threshold(self) -> float:
        """Founder-set spend threshold (EUR) for the channel gate."""
        return self._get_float_config(
            self.CHANNEL_SPEND_THRESHOLD_KEY,
            self.CHANNEL_SPEND_THRESHOLD_DEFAULT,
        )

    def _should_gate(self, classification) -> bool:
        """Whether this directive needs a founder GO before executing.

        Gate triggers when the CEO marks it ``master`` tier OR when the
        estimated cost is **strictly greater** than the founder threshold.
        Boundary: a directive whose estimate equals the threshold runs
        without a gate (``> threshold`` gates; ``== threshold`` does not).
        """
        if (classification.approval_tier or "").strip().lower() == "master":
            return True
        estimated = classification.estimated_cost_eur or 0.0
        return estimated > self._channel_spend_threshold()

    def _gate_preview_content(self, classification) -> str:
        """Render the preview turn body: plan + estimated cost + tier.

        The cost is explicitly labelled an ESTIMATE (the CEO's guess, PREVIEW
        in the cost-visibility discipline); the actual run cost is recorded on
        the post-GO final turn (LEDGER).
        """
        plan = (classification.execution_plan or "").strip() or (
            classification.reasoning or ""
        ).strip() or "Execute the requested directive."
        estimated = classification.estimated_cost_eur or 0.0
        tier = (classification.approval_tier or "unknown").strip().lower()
        return (
            f"{plan}\n\n"
            f"Estimated cost (CEO estimate): €{estimated:.2f}\n"
            f"Approval tier: {tier}\n\n"
            "Reply GO to execute, or abandon to drop it. Nothing has run yet."
        )

    def _handle_gate(
        self,
        directive: Directive,
        classification,
        session_id: str,
        start_time: float,
    ) -> DirectiveResult:
        """Pause a directive at the spend gate — nothing executes.

        Records a CEO ``preview`` turn (plan + estimated cost + tier), moves
        the session to ``gated``, and persists a classification snapshot on
        the session so a later GO re-runs the held plan WITHOUT a second
        classify LLM call. No squad dispatch, no project, no approval.
        """
        preview = self._gate_preview_content(classification)
        cost = self.cost_tracker.run_total()
        self.channel.add_turn(
            session_id,
            role="ceo",
            content=preview,
            kind="preview",
            cost=cost,
            directive_id=directive.id,
        )
        # Persist the directive text + classification snapshot so GO can replay
        # the exact held plan without re-classifying (avoids double LLM spend).
        # Written to the session row (survives an engine restart) AND cached
        # in memory for the common same-process GO.
        self._stash_gated_directive(session_id, directive, classification)
        self.channel.update_session_state(
            session_id,
            SessionStatus.GATED,
            route="execute",
            directive_id=directive.id,
        )
        self.audit.record(
            "directive.gated",
            "Directive gated awaiting founder GO",
            detail={
                "approval_tier": classification.approval_tier,
                "estimated_cost_eur": classification.estimated_cost_eur,
                "threshold_eur": self._channel_spend_threshold(),
            },
            agent_role="ceo",
            directive_id=directive.id,
        )
        self.journal.log(Decision(
            directive_id=directive.id,
            directive_type=directive.directive_type.value if directive.directive_type else "unknown",
            raw_input=directive.raw_input,
            classification=classification.model_dump() if classification else {},
            result={"status": "gated", "message": preview[:500]},
            agents_involved=["ceo"],
            total_ai_cost=cost,
            duration_seconds=time.time() - start_time,
        ))
        return DirectiveResult(
            directive=directive,
            status="gated",
            message=preview,
            session_id=session_id,
            total_ai_cost=cost,
            agents_used=["ceo"],
        )

    # Key under which the gated-directive snapshot lives in the session
    # payload (raw founder text + the CEO classification). The payload is the
    # DURABLE source of truth — a gated session is a parked founder decision
    # that may sit for hours/days, and the desktop app restarts the engine
    # routinely; a GO must still work against a brand-new engine instance.
    _GATED_PAYLOAD_KEY = "gated_directive"
    _PROPOSED_PAYLOAD_KEY = "proposed_directive"

    # In-memory cache of held (gated) directives keyed by session_id, used for
    # the common same-process GO. The session-row payload mirrors it so GO
    # survives a restart (rehydrate path in :meth:`_pop_gated_directive`).
    def _stash_gated_directive(
        self,
        session_id: str,
        directive: Directive,
        classification,
    ) -> None:
        if not hasattr(self, "_gated_directives"):
            self._gated_directives: dict[str, tuple[str, Any]] = {}
        self._gated_directives[session_id] = (
            directive.raw_input,
            classification,
        )
        # Durable copy on the session row so a GO survives an engine restart.
        try:
            session = self.channel.get_session(session_id)
            payload = dict(session.payload) if session else {}
            payload[self._GATED_PAYLOAD_KEY] = {
                "raw_input": directive.raw_input,
                "classification": classification.model_dump(),
            }
            self.channel.set_session_payload(session_id, payload)
        except Exception:  # pragma: no cover — persistence is best-effort
            # The in-memory cache still allows a same-process GO; only the
            # restart-survival guarantee is lost on a serialization failure.
            pass

    def _pop_gated_directive(self, session_id: str):
        """Return ``(raw_input, classification)`` for a gated session.

        Tries the in-memory cache first; on a miss (e.g. the engine was
        restarted since the gate), rehydrates from the persisted session
        payload and reconstructs the CEO classification — NO re-classify, so
        no double LLM spend. Returns ``None`` only when neither source has a
        snapshot. Always clears the in-memory entry; the durable payload is
        cleaned up by the caller once the GO/abandon resolves.
        """
        store = getattr(self, "_gated_directives", None)
        if store and session_id in store:
            return store.pop(session_id, None)
        return self._rehydrate_gated_directive(session_id)

    def _rehydrate_gated_directive(self, session_id: str):
        """Rebuild a gated snapshot from the persisted session payload."""
        session = self.channel.get_session(session_id)
        if session is None:
            return None
        snapshot = (session.payload or {}).get(self._GATED_PAYLOAD_KEY)
        if not snapshot:
            return None
        from kompany.agents.ceo import DirectiveClassification
        try:
            classification = DirectiveClassification.model_validate(
                snapshot["classification"]
            )
        except Exception:
            return None
        return snapshot.get("raw_input", ""), classification

    def _clear_gated_payload(self, session_id: str) -> None:
        """Drop the durable gated snapshot once a GO/abandon resolves."""
        try:
            session = self.channel.get_session(session_id)
            if session is None or self._GATED_PAYLOAD_KEY not in (
                session.payload or {}
            ):
                return
            payload = dict(session.payload)
            payload.pop(self._GATED_PAYLOAD_KEY, None)
            self.channel.set_session_payload(session_id, payload)
        except Exception:  # pragma: no cover — best-effort cleanup
            pass

