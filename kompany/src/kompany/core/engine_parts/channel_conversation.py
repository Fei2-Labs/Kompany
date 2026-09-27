"""CEO-channel commands and specialist conversation handling."""

from __future__ import annotations

import time

from kompany.core.directive import (
    Directive,
    DirectiveResult,
    DirectiveStatus,
    DirectiveType,
)
from kompany.channels.routing import resolve_project
from kompany.state.models import ConversationSession, Decision, SessionStatus


class ChannelConversationMixin:
    def _handle_channel_command(
        self,
        raw_input: str,
        directive: Directive,
        session,
    ) -> DirectiveResult | None:
        """Apply deterministic conversation controls before LLM routing."""
        parts = raw_input.strip().split()
        if not parts or not parts[0].startswith("/"):
            return None
        command = parts[0].lower()

        if command == "/status":
            project = session.project_id or "General"
            message = (
                f"Active agent: {session.active_agent_id.upper()}\n"
                f"Project: {project}\n"
                f"Session epoch: {session.session_epoch}"
            )
            return self._channel_command_result(
                directive,
                session,
                message,
            )

        if command == "/new":
            replacement = self._replace_channel_session(session)
            return self._channel_command_result(
                directive,
                replacement,
                "Started a new conversation session.",
            )

        if command == "/project":
            query = " ".join(parts[1:]).strip()
            if not query:
                return self._channel_command_result(
                    directive,
                    session,
                    "Usage: /project <name-or-id>",
                    status="failed",
                )
            explicit = self.projects.get(query)
            decision = resolve_project(
                query,
                self.projects.list_active(),
                explicit_project_id=explicit.id if explicit else None,
            )
            if decision.status != "resolved" or not decision.project_id:
                candidates = ", ".join(decision.candidate_project_ids)
                message = (
                    f"Project is ambiguous: {candidates}"
                    if candidates
                    else f"Unknown project: {query}"
                )
                return self._channel_command_result(
                    directive,
                    session,
                    message,
                    status="failed",
                )
            replacement = self._replace_channel_session(
                session,
                project_id=decision.project_id,
            )
            return self._channel_command_result(
                directive,
                replacement,
                f"Switched to project {decision.project_id}.",
            )

        if command in {"/agent", "/ceo"}:
            target = "ceo" if command == "/ceo" else (
                parts[1].strip().lower() if len(parts) > 1 else ""
            )
            if not target:
                return self._channel_command_result(
                    directive,
                    session,
                    "Usage: /agent <role>",
                    status="failed",
                )
            try:
                descriptor = self.registry.descriptor(target)
                if not descriptor.can_own_conversation:
                    raise ValueError("agent cannot own a conversation")
                self.registry.get(target, company_state=self.get_company_state())
            except (ValueError, KeyError):
                return self._channel_command_result(
                    directive,
                    session,
                    f"Unknown or unavailable conversation agent: {target}",
                    status="failed",
                )

            previous = (
                session.active_agent_id
                if session.active_agent_id != target
                else None
            )
            if previous:
                session = self.channel.handoff(
                    session.id,
                    to_agent_id=target,
                    reason="explicit_user_selection",
                    confidence=1.0,
                    directive_id=directive.id,
                )
                self.audit.record(
                    "routing.handoff",
                    "User selected the active conversation owner",
                    detail={
                        "handoff_id": session.handoff_id,
                        "from_agent_id": previous,
                        "to_agent_id": target,
                        "reason": "explicit_user_selection",
                    },
                    agent_role=target,
                    directive_id=directive.id,
                    project_id=session.project_id,
                )
            return self._channel_command_result(
                directive,
                session,
                f"{target.upper()} now owns this conversation.",
                previous_agent_id=previous,
                handoff_id=session.handoff_id if previous else None,
            )

        return None

    def _replace_channel_session(
        self,
        session,
        *,
        project_id: str | None = None,
    ):
        """Close one virtual conversation and open an isolated successor."""
        self.channel.update_session_state(
            session.id,
            SessionStatus.ABANDONED,
        )
        return self.channel.create_session(
            ConversationSession(
                company_id=session.company_id,
                project_id=(
                    project_id
                    if project_id is not None
                    else session.project_id
                ),
                channel=session.channel,
                account_id=session.account_id,
                chat_id=session.chat_id,
                thread_id=session.thread_id,
                sender_id=session.sender_id,
                active_agent_id=session.active_agent_id,
                previous_agent_id=session.previous_agent_id,
                session_epoch=session.session_epoch + 1,
            )
        )

    def _channel_command_result(
        self,
        directive: Directive,
        session,
        message: str,
        *,
        status: str = "completed",
        previous_agent_id: str | None = None,
        handoff_id: str | None = None,
    ) -> DirectiveResult:
        """Persist and return one deterministic channel-control response."""
        self.channel.add_turn(
            session.id,
            role="founder",
            content=directive.raw_input,
            kind="message",
            directive_id=directive.id,
        )
        self.channel.add_turn(
            session.id,
            role="ceo",
            agent_id=session.active_agent_id,
            content=message,
            kind="final",
            directive_id=directive.id,
        )
        return DirectiveResult(
            directive=directive,
            status=status,
            message=message,
            project_id=session.project_id,
            session_id=session.id,
            agents_used=[session.active_agent_id],
            active_agent_id=session.active_agent_id,
            previous_agent_id=previous_agent_id,
            handoff_id=handoff_id,
            conversation_continues=True,
        )

    def _handle_specialist_chat(
        self,
        directive: Directive,
        classification,
        specialist,
        session,
        start_time: float,
        *,
        transition_from: str | None,
        session_context: str | None,
    ) -> DirectiveResult:
        """Let the persisted specialist owner answer while keeping chat open."""
        role = session.active_agent_id
        self.agent_status.set(role, "thinking", "handling channel conversation")
        context = (
            "Operating boundary: You have no tools in this direct channel reply. "
            "Do not claim to have browsed, posted, sent, edited, purchased, or "
            "otherwise performed external actions. Explain when execution must "
            "be delegated or approved.\n"
            f"Project: {session.project_id or 'general'}\n"
            f"User request: {directive.raw_input}"
        )
        if session_context:
            context = f"Conversation so far:\n{session_context}\n\n{context}"
        if transition_from:
            context = (
                f"You are taking over this conversation from "
                f"{transition_from.upper()}.\n{context}"
            )
        try:
            response = specialist.call(
                context,
                directive_id=directive.id,
                action_type=f"{role}.channel_reply",
            )
        finally:
            self.agent_status.set(role, "idle")
        message = (response.text or "").strip() or (
            f"{role.upper()} could not produce a response."
        )
        cost = self.cost_tracker.run_total()
        directive.status = DirectiveStatus.COMPLETED
        result = DirectiveResult(
            directive=directive,
            status="completed",
            message=message,
            project_id=session.project_id,
            session_id=session.id,
            total_ai_cost=cost,
            agents_used=[role],
            active_agent_id=role,
            previous_agent_id=transition_from,
            handoff_id=session.handoff_id if transition_from else None,
            conversation_continues=True,
        )
        self.channel.add_turn(
            session.id,
            role="ceo",
            agent_id=role,
            content=message,
            kind="final",
            cost=cost,
            directive_id=directive.id,
        )
        self.channel.update_session_state(
            session.id,
            SessionStatus.OPEN,
            route=classification.route,
            directive_id=directive.id,
        )
        self.audit.record(
            "directive.specialist_reply",
            f"{role.upper()} handled the active conversation",
            detail={
                "handoff_id": result.handoff_id,
                "conversation_continues": True,
            },
            agent_role=role,
            directive_id=directive.id,
            project_id=session.project_id,
        )
        self.journal.log(Decision(
            directive_id=directive.id,
            directive_type=(
                directive.directive_type.value
                if directive.directive_type
                else "unknown"
            ),
            raw_input=directive.raw_input,
            classification=classification.model_dump(),
            result={"status": result.status, "message": message[:500]},
            agents_involved=[role],
            total_ai_cost=cost,
            duration_seconds=time.time() - start_time,
        ))
        return result
