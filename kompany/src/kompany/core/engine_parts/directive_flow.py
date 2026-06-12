"""Override + directive processing pipeline.

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
from kompany.core.run_context import current_run_id, run_scope
from kompany.state.models import CLevelReview, Decision, ApprovalRequest, DecisionChainPacket, Project, ProjectType, SESSION_TERMINAL_STATUSES, SessionStatus



class DirectiveProcessingMixin:
    def _materialize_packet_project(
        self,
        packet: DecisionChainPacket,
        request: ApprovalRequest,
    ) -> Project:
        """Create a Project and its Tasks from an approved decision packet."""
        from kompany.state.models import Task, TaskStatus

        project_type = (
            ProjectType.REVENUE
            if packet.revenue_proposal.shortfall > 0
            else ProjectType.OPERATIONAL
        )
        plan_agents = packet.execution_plan.assigned_agents or ["coo"]
        assigned = ["coo"] + [a for a in plan_agents if a != "coo"]

        project = Project(
            name=f"Execute: {packet.raw_input[:50]}",
            type=project_type,
            target_amount=packet.revenue_proposal.target_amount,
            triggers_directive_id=request.directive_id,
            plan={"packet": packet.model_dump(mode="json")},
            assigned_agents=assigned,
        )
        self.projects.create(project)

        steps = packet.execution_plan.steps or ["Execute approved packet"]
        for index, step in enumerate(steps):
            agent = plan_agents[index % len(plan_agents)] if plan_agents else "coo"
            task = Task(
                project_id=project.id,
                title=step,
                assigned_agent=agent,
                status=TaskStatus.PENDING,
            )
            self.projects.create_task(task)
        return project

    def _c_level_review(self, project: Project, run_result) -> list[CLevelReview]:
        """Deterministic C-level review of executed packet outputs."""
        failed_count = run_result.tasks_failed
        completed_count = run_result.tasks_completed
        verdict = "approved" if failed_count == 0 else "needs_revision"

        notes_ok = (
            f"{completed_count} task(s) completed without failures."
        )
        notes_revision = (
            f"{failed_count} task(s) failed; "
            f"review failed task outputs before delivery."
        )
        base_note = notes_ok if verdict == "approved" else notes_revision

        roles = ["cro", "cfo", "cos", "ceo"]
        reviews: list[CLevelReview] = []
        for role in roles:
            review = CLevelReview(owner=role, verdict=verdict, notes=base_note)
            reviews.append(review)
            self.audit.record(
                "governed_execution.reviewed",
                f"{role.upper()} reviewed packet execution",
                detail=review.model_dump(),
                agent_role=role,
                project_id=project.id,
            )
        return reviews

    def process_override(self, text: str) -> dict:
        """Create a risk briefing and approval request for a user override."""
        if current_run_id() is None:
            with run_scope():
                return self._process_override_inner(text)
        return self._process_override_inner(text)

    def _process_override_inner(self, text: str) -> dict:
        directive = Directive(raw_input=text)
        directive.status = DirectiveStatus.AWAITING_APPROVAL
        briefing = {
            "summary": f"Override requested: {text}",
            "risks": [
                "May invalidate the current plan or assumptions.",
                "May affect budget, schedule, or active project priorities.",
                "May require revisiting prior team recommendations.",
            ],
            "required_confirmation": "Approve only after accepting these risks.",
            "will_execute_immediately": False,
        }
        request = self.approvals.create(ApprovalRequest(
            action_type="override",
            summary=f"Approve override: {text[:120]}",
            payload={"override": text, "briefing": briefing},
            directive_id=directive.id,
            requested_by="KompanyEngine",
            severity="high",
        ))
        self.audit.record(
            "override.risk_briefing_created",
            "Created override risk briefing",
            detail={"approval_id": request.id},
            directive_id=directive.id,
        )
        return {
            "status": "awaiting_approval",
            "approval_id": request.id,
            "briefing": briefing,
        }

    def process_directive(
        self,
        raw_input: str,
        session_id: str | None = None,
    ) -> DirectiveResult:
        """Main entry point. Takes natural language, returns result.

        Opens a fresh ``run_scope`` so every state write made during this
        directive (audit_log, decisions, ledger, memories, approvals,
        channel turns) carries the same ``run_id``. A nested call (e.g. CEO
        derives a child directive) records the outer ``run_id`` as
        ``parent_run_id`` automatically — see
        :func:`kompany.core.run_context.run_scope`.

        ``session_id`` is the CEO-channel session this message belongs to
        (06-03-ceo-channel). It is **optional**: internal callers
        (run_context replay, onboarding kickoff) pass only ``raw_input`` and a
        fresh session is opened for them. When provided, the message continues
        that session (a clarify reply); a closed session yields an error
        result.
        """
        with run_scope() as run_id:
            result = self._process_directive_inner(raw_input, session_id)
            # Stamp the run id onto the result so callers can scope per-run
            # SSE events (llm.spend / agent.activity both carry run_id) and
            # reconcile per-run cost. Done here — inside the scope — so even
            # the early "suspended" return path gets tagged.
            result.run_id = run_id
            return result

    def _compose_session_context(self, session_id: str) -> str:
        """Render prior turns of THIS session for the classify prompt.

        Session-scoped per Decision 2 — no cross-session memory. Empty string
        when the session has no turns yet.
        """
        lines: list[str] = []
        for turn in self.channel.session_turns(session_id):
            who = "Founder" if turn.role == "founder" else "CEO"
            lines.append(f"{who}: {turn.content}")
        return "\n".join(lines)

    def _compose_recent_context(self, limit: int = 6) -> str:
        """Render last N turns across ALL sessions for cross-session context.

        Decision 3 (Option B): inject into classify + answer so short
        follow-ups ("批准" / "继续" / "第二个") are understood in new sessions.
        Only ``message`` / ``final`` turns (noise kinds excluded by
        :meth:`ConversationStore.recent_turns`). Empty string when no history.
        """
        lines: list[str] = []
        for turn in self.channel.recent_turns(limit):
            who = "Founder" if turn.role == "founder" else "CEO"
            lines.append(f"{who}: {turn.content}")
        return "\n".join(lines)

    def _process_directive_inner(
        self,
        raw_input: str,
        session_id: str | None = None,
    ) -> DirectiveResult:
        directive = Directive(raw_input=raw_input)

        # ----- Resolve / open the CEO-channel session -----
        # No session_id → open a fresh session. session_id given → continue an
        # existing one (must be open/clarifying); a closed session is an error.
        if session_id is None:
            session = self.channel.create_session()
        else:
            session = self.channel.get_session(session_id)
            if session is None:
                return DirectiveResult(
                    directive=directive,
                    status="failed",
                    message=f"Unknown channel session {session_id!r}.",
                    session_id=session_id,
                    agents_used=[],
                    total_ai_cost=0.0,
                )
            if session.state in SESSION_TERMINAL_STATUSES:
                return DirectiveResult(
                    directive=directive,
                    status="failed",
                    message=(
                        f"Channel session is closed ({session.state.value}); "
                        "start a new message to open a fresh session."
                    ),
                    session_id=session.id,
                    agents_used=[],
                    total_ai_cost=0.0,
                )
        session_id = session.id

        # One-shot GO/abandon replies on a paused session. The gate's CLI
        # hint tells the founder to continue with
        # ``kompany directive "GO" --session <id>``; without this branch the
        # literal text "GO" would be re-classified as a fresh directive and
        # the session would gate again forever. Mirrors the token sets the
        # interactive prompt accepts.
        if session_id is not None and session.state in (
            SessionStatus.GATED,
            SessionStatus.PROPOSED,
        ):
            reply = raw_input.strip().lower()
            if reply in {"go", "g", "yes", "y"}:
                return self.channel_go(session_id)
            if reply in {"abandon", "a", "no", "n"}:
                return self.channel_abandon(session_id)

        rt = self.runtime.get()
        if rt["state"] == "suspended":
            self.audit.record(
                "directive.suspended_skip",
                "Skipped directive: runtime suspended",
                detail={"reason": rt["reason"], "input_length": len(raw_input)},
                directive_id=directive.id,
            )
            return DirectiveResult(
                directive=directive,
                status="suspended",
                message=(
                    f"Engine is suspended ({rt['reason'] or 'manual'}). "
                    "Call resume() to continue."
                ),
                session_id=session_id,
                agents_used=[],
                total_ai_cost=0.0,
            )

        state = self.get_company_state()
        self.audit.record(
            "directive.received",
            "Received user directive",
            detail={"input_length": len(raw_input)},
            directive_id=directive.id,
        )

        # Record the founder's turn before classification so the conversation
        # thread reflects what was sent even if classify fails.
        self.channel.add_turn(
            session_id,
            role="founder",
            content=raw_input,
            kind="message",
            directive_id=directive.id,
        )

        start_time = time.time()
        try:
            self.agent_status.set("ceo", "thinking", "classifying directive")
            ceo = self.registry.get("ceo", company_state=state)
            # Inject the agreed-target summary so CEO classify weighs the
            # ask against the company's explicit revenue/customer/deadline
            # commitments (mission-targets task 05-19). Falls back to an
            # innocuous default when no targets are set. Session context is
            # the prior turns of THIS session only (Decision 2).
            clarify_capped = self.channel.at_clarify_cap(session_id)
            session_context = self._compose_session_context(session_id) or None
            recent_context = self._compose_recent_context() or None
            classification = ceo.classify(
                raw_input,
                directive_id=directive.id,
                targets_summary=self._compose_targets_summary(),
                glossary_summary=self._compose_glossary_summary(),
                session_context=session_context,
                recent_context=recent_context,
                clarify_capped=clarify_capped,
            )
            self.audit.record(
                "directive.classified",
                "CEO classified directive",
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

            # ----- Route detection: execute | clarify | answer -----
            # ``informational`` maps to answer regardless of the emitted route
            # (keeps directive_type compat). At the clarify cap a ``clarify``
            # route is rejected engine-side and forced to a deterministic
            # resolution: answer for questions (informational), else execute.
            route = self._resolve_channel_route(
                classification, clarify_capped
            )
            if route == "clarify":
                return self._handle_clarify(
                    directive, classification, session_id, start_time
                )
            if route == "answer":
                return self._handle_answer(
                    directive,
                    classification,
                    ceo,
                    session_id,
                    start_time,
                    session_context=session_context,
                    recent_context=recent_context,
                )

            # ----- Threshold spend gate (PR2, Decision 7) -----
            # ``auto``/``ceo`` tier under the founder threshold runs
            # immediately (cost streams live). ``master`` tier OR an
            # estimated cost over the founder-set threshold posts a preview
            # turn and pauses — NOTHING executes before a founder GO. The
            # estimate is the CEO's guess; the preview labels it as such and
            # the post-GO final turn records the ACTUAL run cost.
            if self._should_gate(classification):
                return self._handle_gate(
                    directive, classification, session_id, start_time
                )

            handler = {
                DirectiveType.ACQUISITION: self._handle_acquisition,
                DirectiveType.STRATEGIC: self._handle_strategic,
                DirectiveType.OPERATIONAL: self._handle_operational,
                DirectiveType.INFORMATIONAL: self._handle_informational,
            }.get(directive.directive_type, self._handle_operational)
            self.audit.record(
                "directive.routed",
                "Routed directive to handler",
                detail={"directive_type": directive.directive_type.value},
                directive_id=directive.id,
            )

            result = handler(directive, classification, ceo)
            result.session_id = session_id
            # Record the CEO's final (dispatch) turn and close the session.
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

            decision_result_payload: dict[str, Any] = {
                "status": result.status,
                "message": result.message[:500],
            }
            if result.project_id:
                decision_result_payload["project_id"] = result.project_id
            if result.debate_id:
                decision_result_payload["debate_id"] = result.debate_id
            if result.approval_id:
                decision_result_payload["approval_id"] = result.approval_id
            self.journal.log(Decision(
                directive_id=directive.id,
                directive_type=directive.directive_type.value if directive.directive_type else "unknown",
                raw_input=directive.raw_input,
                classification=classification.model_dump() if classification else {},
                result=decision_result_payload,
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
                "Completed directive processing",
                detail={"status": result.status},
                directive_id=directive.id,
            )
            return result
        except Exception as exc:
            self.audit.record(
                "directive.failed",
                "Directive processing failed",
                detail={"error": str(exc)},
                directive_id=directive.id,
            )
            raise
        finally:
            self.agent_status.set("ceo", "idle")

