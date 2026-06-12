"""Channel proposal dispatch, GO/abandon, autonomy results.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations

import time

from kompany.core.directive import Directive, DirectiveResult, DirectiveType
from kompany.core.run_context import run_scope
from kompany.state.models import Decision, ApprovalRequest, SESSION_TERMINAL_STATUSES, SessionStatus



class ChannelActionsMixin:
    def _stash_proposal_directive(
        self,
        session_id: str,
        proposal_text: str,
    ) -> None:
        """Persist the CEO's proposal directive text so GO survives a restart."""
        try:
            session = self.channel.get_session(session_id)
            payload = dict(session.payload) if session else {}
            payload[self._PROPOSED_PAYLOAD_KEY] = {"proposal_directive": proposal_text}
            self.channel.set_session_payload(session_id, payload)
        except Exception:  # pragma: no cover — best-effort persistence
            pass

    def _pop_proposal_directive(self, session_id: str) -> str | None:
        """Return and clear the stashed proposal_directive text, or None."""
        session = self.channel.get_session(session_id)
        if session is None:
            return None
        snapshot = (session.payload or {}).get(self._PROPOSED_PAYLOAD_KEY)
        if not snapshot:
            return None
        # Clear from payload immediately — can't be replayed after one GO.
        try:
            payload = dict(session.payload)
            payload.pop(self._PROPOSED_PAYLOAD_KEY, None)
            self.channel.set_session_payload(session_id, payload)
        except Exception:  # pragma: no cover
            pass
        return snapshot.get("proposal_directive")

    def _dispatch_proposed(
        self,
        session_id: str,
        proposal_directive: str,
    ) -> DirectiveResult:
        """Classify + execute a proposal directive after founder GO.

        Unlike :meth:`_dispatch_gated` (which replays a persisted
        classification), PROPOSED re-classifies the proposal text with 1
        fresh LLM call — the proposal wording may be more specific than the
        original question, so a fresh classify is intentional (PRD Decision 1).
        """
        state = self.get_company_state()
        ceo = self.registry.get("ceo", company_state=state)
        directive = Directive(raw_input=proposal_directive)
        start_time = time.time()
        try:
            self.agent_status.set("ceo", "thinking", "classifying proposal on GO")
            classification = ceo.classify(
                proposal_directive,
                directive_id=directive.id,
                targets_summary=self._compose_targets_summary(),
                glossary_summary=self._compose_glossary_summary(),
            )
            self.audit.record(
                "directive.classified",
                "CEO classified proposal directive on GO",
                detail=classification.model_dump(),
                agent_role="ceo",
                directive_id=directive.id,
            )
            directive.directive_type = DirectiveType(classification.directive_type)
            directive.assigned_squad = classification.primary_squad
            directive.assigned_agents = classification.agents_needed
            directive.requires_approval = classification.approval_tier
            directive.budget_required = classification.estimated_cost_eur
            directive.budget_available = self.ledger.get_balance()

            handler = {
                DirectiveType.ACQUISITION: self._handle_acquisition,
                DirectiveType.STRATEGIC: self._handle_strategic,
                DirectiveType.OPERATIONAL: self._handle_operational,
                DirectiveType.INFORMATIONAL: self._handle_informational,
            }.get(directive.directive_type, self._handle_operational)
            self.audit.record(
                "directive.routed",
                "Routed proposed directive to handler after GO",
                detail={"directive_type": directive.directive_type.value},
                directive_id=directive.id,
            )
            result = handler(directive, classification, ceo)
            result.session_id = session_id
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
                SessionStatus.DISPATCHED,
                route="execute",
                directive_id=directive.id,
                project_id=result.project_id,
                approval_id=result.approval_id,
            )
            self.journal.log(Decision(
                directive_id=directive.id,
                directive_type=directive.directive_type.value if directive.directive_type else "unknown",
                raw_input=directive.raw_input,
                classification=classification.model_dump() if classification else {},
                result={"status": result.status, "message": result.message[:500]},
                agents_involved=result.agents_used,
                total_ai_cost=result.total_ai_cost,
                duration_seconds=time.time() - start_time,
            ))
            self.audit.record(
                "directive.completed",
                "Completed proposed directive after GO",
                detail={"status": result.status},
                directive_id=directive.id,
            )
            return result
        except Exception as exc:
            self.audit.record(
                "directive.failed",
                "Proposed directive execution failed after GO",
                detail={"error": str(exc)},
                directive_id=directive.id,
            )
            return self._channel_action_error(
                session_id,
                f"Execution failed after GO: {exc}. Reply GO to retry.",
            )
        finally:
            self.agent_status.set("ceo", "idle")

    def channel_go(self, session_id: str) -> DirectiveResult:
        """Execute a gated or proposed session's held directive on founder GO.

        GATED: replays the persisted classification snapshot — no re-classify.
        PROPOSED: re-classifies the stashed proposal directive text (1 LLM
        call per PRD Decision 1), then executes.

        A GO on a non-gated/proposed / closed / unknown session returns a clear
        error result (no exception leaks — per error-handling spec).
        """
        with run_scope():
            session = self.channel.get_session(session_id)
            if session is None:
                return self._channel_action_error(
                    session_id, f"Unknown channel session {session_id!r}."
                )
            if session.state == SessionStatus.PROPOSED:
                proposal_text = self._pop_proposal_directive(session_id)
                if not proposal_text:
                    return self._channel_action_error(
                        session_id,
                        "No held proposal for this session; cannot GO.",
                    )
                return self._dispatch_proposed(session_id, proposal_text)
            if session.state != SessionStatus.GATED:
                return self._channel_action_error(
                    session_id,
                    f"Session is not awaiting GO (state={session.state.value}); "
                    "nothing to execute.",
                )
            stashed = self._pop_gated_directive(session_id)
            if stashed is None:
                return self._channel_action_error(
                    session_id,
                    "No held directive for this gated session; cannot GO.",
                )
            raw_input, classification = stashed
            return self._dispatch_gated(session_id, raw_input, classification)

    def _dispatch_gated(
        self,
        session_id: str,
        raw_input: str,
        classification,
    ) -> DirectiveResult:
        """Run the held plan through the execute handler map (no re-classify)."""
        directive = Directive(raw_input=raw_input)
        directive.directive_type = DirectiveType(classification.directive_type)
        directive.assigned_squad = classification.primary_squad
        directive.assigned_agents = classification.agents_needed
        directive.requires_approval = classification.approval_tier
        directive.budget_required = classification.estimated_cost_eur
        directive.budget_available = self.ledger.get_balance()
        start_time = time.time()
        try:
            ceo = self.registry.get(
                "ceo", company_state=self.get_company_state()
            )
            handler = {
                DirectiveType.ACQUISITION: self._handle_acquisition,
                DirectiveType.STRATEGIC: self._handle_strategic,
                DirectiveType.OPERATIONAL: self._handle_operational,
                DirectiveType.INFORMATIONAL: self._handle_informational,
            }.get(directive.directive_type, self._handle_operational)
            self.audit.record(
                "directive.routed",
                "Routed gated directive to handler after GO",
                detail={"directive_type": directive.directive_type.value},
                directive_id=directive.id,
            )
            result = handler(directive, classification, ceo)
            result.session_id = session_id
            # ACTUAL run cost (LEDGER) — the estimate was the PREVIEW guess.
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
                SessionStatus.DISPATCHED,
                route="execute",
                directive_id=directive.id,
                project_id=result.project_id,
                approval_id=result.approval_id,
            )
            self.journal.log(Decision(
                directive_id=directive.id,
                directive_type=directive.directive_type.value if directive.directive_type else "unknown",
                raw_input=directive.raw_input,
                classification=classification.model_dump() if classification else {},
                result={"status": result.status, "message": result.message[:500]},
                agents_involved=result.agents_used,
                total_ai_cost=result.total_ai_cost,
                duration_seconds=time.time() - start_time,
            ))
            self.audit.record(
                "directive.completed",
                "Completed gated directive after GO",
                detail={"status": result.status},
                directive_id=directive.id,
            )
            # Success: the held directive ran, the session is dispatched. Drop
            # the durable snapshot so it can never be replayed.
            self._clear_gated_payload(session_id)
            return result
        except Exception as exc:
            # Dispatch failed mid-GO. Keep the session GATED so the founder can
            # retry, restore the in-memory snapshot (the durable payload is
            # untouched), and return a clean failed result — no exception leak
            # (per error-handling spec) and no stuck dispatched-but-failed
            # session.
            self.audit.record(
                "directive.failed",
                "Gated directive execution failed after GO",
                detail={"error": str(exc)},
                directive_id=directive.id,
            )
            self._stash_gated_directive(session_id, directive, classification)
            return self._channel_action_error(
                session_id,
                f"Execution failed after GO: {exc}. Session remains gated; "
                "reply GO to retry or abandon to drop it.",
            )
        finally:
            self.agent_status.set("ceo", "idle")

    def channel_abandon(self, session_id: str) -> DirectiveResult:
        """Founder gives up on a gated / clarifying / open session.

        Records a CEO abandonment turn and closes the session as
        ``abandoned``. A non-abandonable (closed / unknown) session returns a
        clear error result.
        """
        session = self.channel.get_session(session_id)
        if session is None:
            return self._channel_action_error(
                session_id, f"Unknown channel session {session_id!r}."
            )
        if session.state in SESSION_TERMINAL_STATUSES:
            return self._channel_action_error(
                session_id,
                f"Session is already closed ({session.state.value}); "
                "nothing to abandon.",
            )
        # Drop any held gated directive (in-memory cache + durable payload) so
        # it can never be replayed later.
        self._pop_gated_directive(session_id)
        self._clear_gated_payload(session_id)
        directive = Directive(raw_input="")
        turn = self.channel.add_turn(
            session_id,
            role="ceo",
            content="Abandoned by founder. Nothing was executed.",
            kind="final",
        )
        self.channel.update_session_state(
            session_id,
            SessionStatus.ABANDONED,
            directive_id=session.directive_id,
        )
        self.audit.record(
            "directive.abandoned",
            "Channel session abandoned by founder",
            detail={"from_state": session.state.value},
            agent_role="ceo",
            directive_id=session.directive_id,
        )
        return DirectiveResult(
            directive=directive,
            status="abandoned",
            message=turn.content,
            session_id=session_id,
            agents_used=["ceo"],
        )

    def _channel_action_error(
        self,
        session_id: str,
        message: str,
    ) -> DirectiveResult:
        """A clear failed result for an illegal GO/abandon (no exception)."""
        return DirectiveResult(
            directive=Directive(raw_input=""),
            status="failed",
            message=message,
            session_id=session_id,
            agents_used=[],
            total_ai_cost=0.0,
        )

    def _record_autonomy_result(
        self,
        directive: Directive,
        can_auto_proceed: bool,
        estimated_cost: float | None,
    ) -> str | None:
        event_type = (
            "autonomy.auto_approved"
            if can_auto_proceed
            else "autonomy.approval_required"
        )
        approval_id = None
        if not can_auto_proceed:
            request = self.approvals.create(ApprovalRequest(
                action_type="directive_execution",
                summary=f"Approve directive: {directive.raw_input[:120]}",
                payload={
                    "directive_type": directive.directive_type.value if directive.directive_type else None,
                    "approval_tier": directive.requires_approval,
                    "estimated_cost": estimated_cost,
                },
                directive_id=directive.id,
                requested_by="AutonomyGate",
                severity="medium",
            ))
            approval_id = request.id
        self.audit.record(
            event_type,
            "AutonomyGate evaluated directive",
            detail={
                "approval_tier": directive.requires_approval,
                "estimated_cost": estimated_cost,
                "approval_id": approval_id,
            },
            directive_id=directive.id,
        )
        return approval_id

