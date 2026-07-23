"""Serialization helpers shared across SDK namespaces."""

from __future__ import annotations

from typing import Any


def _session_to_dict(session: Any) -> dict[str, Any]:
    """Serialize a ``ConversationSession`` to the channel parity dict.

    Same shape REST's ``_session_to_dict`` emits so SDK/REST stay
    key-identical for the channel surface (interfaces.md equivalence rule).
    """
    return {
        "session_id": session.id,
        "state": session.state.value,
        "route": session.route,
        "clarify_turns": session.clarify_turns,
        "created_at": str(session.created_at) if session.created_at is not None else None,
        "closed_at": str(session.closed_at) if session.closed_at is not None else None,
        "run_id": session.run_id,
        "directive_id": session.directive_id,
        "company_id": session.company_id,
        "project_id": session.project_id,
        "channel": session.channel,
        "account_id": session.account_id,
        "chat_id": session.chat_id,
        "thread_id": session.thread_id,
        "sender_id": session.sender_id,
        "active_agent_id": session.active_agent_id,
        "previous_agent_id": session.previous_agent_id,
        "handoff_id": session.handoff_id,
        "handoff_reason": session.handoff_reason,
        "handoff_confidence": session.handoff_confidence,
        "session_epoch": session.session_epoch,
        "approval_id": session.approval_id,
    }


def _turn_to_dict(turn: Any) -> dict[str, Any]:
    """Serialize a ``ConversationTurn`` to the channel parity dict."""
    return {
        "turn_index": turn.turn_index,
        "role": turn.role,
        "agent_id": turn.agent_id,
        "content": turn.content,
        "kind": turn.kind,
        "cost": turn.cost,
        "run_id": turn.run_id,
        "directive_id": turn.directive_id,
        "created_at": str(turn.created_at) if turn.created_at is not None else None,
    }
