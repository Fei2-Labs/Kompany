"""CEO-channel conversation persistence (sessions + turns).

The directive bar becomes the founder's communication channel with the team
(via the CEO conductor). Each founder message opens or continues a *session*;
the CEO auto-routes it into one of three modes — ``execute`` (dispatch the
pipeline) / ``clarify`` (ask back, grill-style) / ``answer`` (reply only) —
and the exchange is recorded as an ordered thread of *turns*.

This store is the structural twin of :class:`ApprovalRequests`
(``approval_requests`` + ``approval_comments``): one parent row
(``channel_sessions``) with ordered child rows (``channel_turns``). The
session lifecycle is enforced here — callers never write a raw state string;
they call :meth:`update_session_state` and let :meth:`_assert_transition`
reject illegal moves.

Lifecycle: ``open -> clarifying -> dispatched | answered | abandoned``.
``gated`` is reserved for PR2's threshold gate (included now so no migration
is needed later).
"""

from __future__ import annotations

import json
from uuid import uuid4

from kompany.core.event_hub import get_event_hub
from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import (
    SESSION_TERMINAL_STATUSES,
    TURN_KINDS,
    TURN_ROLES,
    ConversationSession,
    ConversationTurn,
    SessionStatus,
)


# Max clarify turns per session (PRD edge decision, default 5). At the cap the
# CEO must execute / answer / abandon — no infinite question loops burning
# tokens. Enforced engine-side via :meth:`ConversationStore.at_clarify_cap`.
MAX_CLARIFY_TURNS = 5


class IllegalSessionTransition(ValueError):
    """Raised when a session state transition is not legal.

    Subclass of ``ValueError`` so existing callers catching ``ValueError``
    keep working (mirrors ``IllegalApprovalTransition``).
    """


def _publish_channel_updated(
    reason: str,
    session_id: str | None = None,
    state: str | None = None,
) -> None:
    """Best-effort SSE push that a channel session changed.

    The payload carries ``session_id`` + ``state`` so a client can decide what
    to refetch (the session detail / thread) without parsing ``reason``. SSE
    has no replay, so this is a nudge to reconcile via REST, not the source of
    truth.
    """
    try:
        get_event_hub().publish(
            "channel.updated",
            {"reason": reason, "session_id": session_id, "state": state},
        )
    except Exception:  # pragma: no cover — best-effort live feed
        pass


class ConversationStore:
    """Stores CEO-channel sessions and their ordered turns."""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        session: ConversationSession | None = None,
        run_id: str | None = None,
    ) -> ConversationSession:
        """Open a new conversation session."""
        session = session or ConversationSession()
        rid = run_id if run_id is not None else current_run_id()
        if session.run_id is None:
            session.run_id = rid
        self.db.execute(
            """INSERT INTO channel_sessions
               (id, state, route, clarify_turns, directive_id, company_id,
                project_id,
                channel, account_id, chat_id, thread_id, sender_id,
                active_agent_id, previous_agent_id, handoff_id, handoff_reason,
                handoff_confidence, session_epoch, approval_id, run_id, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id,
                session.state.value,
                session.route,
                session.clarify_turns,
                session.directive_id,
                session.company_id,
                session.project_id,
                session.channel,
                session.account_id,
                session.chat_id,
                session.thread_id,
                session.sender_id,
                session.active_agent_id,
                session.previous_agent_id,
                session.handoff_id,
                session.handoff_reason,
                session.handoff_confidence,
                session.session_epoch,
                session.approval_id,
                session.run_id,
                json.dumps(session.payload or {}),
            ),
        )
        self.db.commit()
        _publish_channel_updated(
            "session_created", session.id, session.state.value
        )
        # Re-read to pick up the SQL-side ``created_at`` default.
        return self.get_session(session.id) or session

    def get_session(self, session_id: str) -> ConversationSession | None:
        row = self.db.execute(
            "SELECT * FROM channel_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return self._row_to_session(row) if row else None

    def bind_legacy_context(
        self,
        session_id: str,
        *,
        company_id: str,
        project_id: str | None,
        channel: str,
        account_id: str,
        chat_id: str,
        thread_id: str | None,
        sender_id: str,
    ) -> ConversationSession | None:
        """Bind transport identity once for sessions created before scoping."""
        existing = self.get_session(session_id)
        if existing is None:
            return None
        transport_identity = (
            existing.channel,
            existing.account_id,
            existing.chat_id,
            existing.thread_id,
            existing.sender_id,
        )
        if any(value is not None for value in transport_identity):
            return existing
        if existing.company_id not in {"default", company_id}:
            return existing

        self.db.execute(
            """UPDATE channel_sessions
               SET company_id = ?,
                   project_id = COALESCE(project_id, ?),
                   channel = ?,
                   account_id = ?,
                   chat_id = ?,
                   thread_id = ?,
                   sender_id = ?
               WHERE id = ?""",
            (
                company_id,
                project_id,
                channel,
                account_id,
                chat_id,
                thread_id,
                sender_id,
                session_id,
            ),
        )
        self.db.commit()
        return self.get_session(session_id)

    def list_sessions(
        self,
        state: str | None = None,
        limit: int = 50,
    ) -> list[ConversationSession]:
        """List sessions, newest first, optionally filtered by state."""
        if state is None:
            rows = self.db.execute(
                "SELECT * FROM channel_sessions "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM channel_sessions WHERE state = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (state, limit),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def open_session(self) -> ConversationSession | None:
        """Most-recent non-terminal session, if any (open or clarifying).

        Used by interface callers that want to continue an in-flight
        conversation without holding a session_id; the engine itself always
        passes an explicit session_id.
        """
        terminal = ",".join(f"'{s.value}'" for s in SESSION_TERMINAL_STATUSES)
        row = self.db.execute(
            f"SELECT * FROM channel_sessions WHERE state NOT IN ({terminal}) "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return self._row_to_session(row) if row else None

    def update_session_state(
        self,
        session_id: str,
        state: SessionStatus | str,
        route: str | None = None,
        directive_id: str | None = None,
        project_id: str | None = None,
        approval_id: str | None = None,
    ) -> ConversationSession | None:
        """Transition a session to ``state`` (state-machine enforced).

        ``route`` and the cross-link ids are stamped when provided. Terminal
        states (dispatched / answered / abandoned) also set ``closed_at``.
        """
        target = SessionStatus(state) if isinstance(state, str) else state
        existing = self.get_session(session_id)
        if existing is None:
            return None
        if existing.state != target:
            self._assert_transition(existing.state, target)

        sets = ["state = ?"]
        params: list[object] = [target.value]
        if route is not None:
            sets.append("route = ?")
            params.append(route)
        if directive_id is not None:
            sets.append("directive_id = ?")
            params.append(directive_id)
        if project_id is not None:
            sets.append("project_id = ?")
            params.append(project_id)
        if approval_id is not None:
            sets.append("approval_id = ?")
            params.append(approval_id)
        if target in SESSION_TERMINAL_STATUSES:
            sets.append("closed_at = datetime('now')")
        params.append(session_id)
        self.db.execute(
            f"UPDATE channel_sessions SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        self.db.commit()
        _publish_channel_updated(
            f"state.{target.value}", session_id, target.value
        )
        return self.get_session(session_id)

    def handoff(
        self,
        session_id: str,
        *,
        to_agent_id: str,
        reason: str,
        confidence: float,
        directive_id: str | None = None,
    ) -> ConversationSession:
        """Atomically transfer a live conversation to another agent."""
        existing = self.get_session(session_id)
        if existing is None:
            raise KeyError(f"Unknown channel session {session_id!r}.")
        if existing.state in SESSION_TERMINAL_STATUSES:
            raise IllegalSessionTransition(
                f"cannot hand off terminal session {session_id!r}"
            )
        if existing.active_agent_id == to_agent_id:
            return existing

        handoff_id = uuid4().hex[:12]
        next_epoch = existing.session_epoch + 1
        with self.db.conn:
            self.db.execute(
                """INSERT INTO channel_handoffs
                   (id, session_id, from_agent_id, to_agent_id, reason,
                    confidence, directive_id, session_epoch)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    handoff_id,
                    session_id,
                    existing.active_agent_id,
                    to_agent_id,
                    reason,
                    confidence,
                    directive_id,
                    next_epoch,
                ),
            )
            self.db.execute(
                """UPDATE channel_sessions
                   SET previous_agent_id = ?,
                       active_agent_id = ?,
                       handoff_id = ?,
                       handoff_reason = ?,
                       handoff_confidence = ?,
                       session_epoch = ?
                   WHERE id = ?""",
                (
                    existing.active_agent_id,
                    to_agent_id,
                    handoff_id,
                    reason,
                    confidence,
                    next_epoch,
                    session_id,
                ),
            )
        updated = self.get_session(session_id)
        if updated is None:
            raise RuntimeError(f"handoff lost session {session_id!r}")
        _publish_channel_updated("handoff", session_id, updated.state.value)
        return updated

    def rollback_handoff(
        self,
        session_id: str,
        *,
        handoff_id: str,
        restore: ConversationSession,
    ) -> ConversationSession:
        """Restore the exact pre-handoff owner after recipient startup fails."""
        current = self.get_session(session_id)
        if current is None:
            raise KeyError(f"Unknown channel session {session_id!r}.")
        if current.handoff_id != handoff_id:
            raise ValueError(
                f"handoff {handoff_id!r} is not active for session {session_id!r}"
            )

        with self.db.conn:
            self.db.execute(
                """UPDATE channel_handoffs
                   SET status = 'failed'
                   WHERE id = ? AND session_id = ?""",
                (handoff_id, session_id),
            )
            self.db.execute(
                """UPDATE channel_sessions
                   SET active_agent_id = ?,
                       previous_agent_id = ?,
                       handoff_id = ?,
                       handoff_reason = ?,
                       handoff_confidence = ?,
                       session_epoch = ?
                   WHERE id = ?""",
                (
                    restore.active_agent_id,
                    restore.previous_agent_id,
                    restore.handoff_id,
                    restore.handoff_reason,
                    restore.handoff_confidence,
                    restore.session_epoch,
                    session_id,
                ),
            )
        updated = self.get_session(session_id)
        if updated is None:
            raise RuntimeError(f"handoff rollback lost session {session_id!r}")
        _publish_channel_updated(
            "handoff_rolled_back",
            session_id,
            updated.state.value,
        )
        return updated

    def set_session_payload(
        self,
        session_id: str,
        payload: dict,
    ) -> ConversationSession | None:
        """Persist a server-side scratch payload on a session.

        PR2 stores the gated-directive snapshot here (raw_input + CEO
        classification) so a founder GO survives an engine restart. Replaces
        any existing payload (pass ``{}`` to clear it).
        """
        existing = self.get_session(session_id)
        if existing is None:
            return None
        self.db.execute(
            "UPDATE channel_sessions SET payload = ? WHERE id = ?",
            (json.dumps(payload or {}), session_id),
        )
        self.db.commit()
        return self.get_session(session_id)

    def at_clarify_cap(self, session_id: str) -> bool:
        """True if the session has reached the clarify-turn cap.

        At the cap the CEO must execute / answer / abandon; the engine rejects
        a further ``clarify`` route. Engine-side enforcement (not prompt-only).
        """
        session = self.get_session(session_id)
        return bool(session and session.clarify_turns >= MAX_CLARIFY_TURNS)

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    def add_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        agent_id: str | None = None,
        kind: str = "message",
        cost: float = 0.0,
        directive_id: str | None = None,
        run_id: str | None = None,
    ) -> ConversationTurn:
        """Append a turn to a session.

        ``turn_index`` is computed as the next ordinal within the session. A
        CEO ``clarify_question`` turn also bumps the parent session's
        ``clarify_turns`` counter (the cap is read from that column).
        """
        if role not in TURN_ROLES:
            raise ValueError(
                f"invalid role {role!r}; expected one of {sorted(TURN_ROLES)}"
            )
        if kind not in TURN_KINDS:
            raise ValueError(
                f"invalid kind {kind!r}; expected one of {sorted(TURN_KINDS)}"
            )
        rid = run_id if run_id is not None else current_run_id()
        turn_index = self._next_turn_index(session_id)
        turn = ConversationTurn(
            session_id=session_id,
            turn_index=turn_index,
            role=role,
            agent_id=agent_id or ("ceo" if role == "ceo" else None),
            content=content,
            kind=kind,
            cost=cost,
            run_id=rid,
            directive_id=directive_id,
        )
        self.db.execute(
            """INSERT INTO channel_turns
               (id, session_id, turn_index, role, agent_id, content, kind, cost,
                run_id, directive_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                turn.id,
                turn.session_id,
                turn.turn_index,
                turn.role,
                turn.agent_id,
                turn.content,
                turn.kind,
                turn.cost,
                turn.run_id,
                turn.directive_id,
            ),
        )
        if kind == "clarify_question":
            self.db.execute(
                "UPDATE channel_sessions SET clarify_turns = clarify_turns + 1 "
                "WHERE id = ?",
                (session_id,),
            )
        self.db.commit()
        parent = self.get_session(session_id)
        _publish_channel_updated(
            "turn_added",
            session_id,
            parent.state.value if parent else None,
        )
        # Re-read to pick up the SQL-side ``created_at`` default.
        return self.get_turn(turn.id) or turn

    def session_turns(self, session_id: str) -> list[ConversationTurn]:
        """All turns in a session, oldest-first.

        Ordered by ``turn_index`` (explicit ordinal), tie-broken on ``rowid``
        for any rows that share an index.
        """
        rows = self.db.execute(
            "SELECT * FROM channel_turns WHERE session_id = ? "
            "ORDER BY turn_index ASC, rowid ASC",
            (session_id,),
        ).fetchall()
        return [self._row_to_turn(r) for r in rows]

    def recent_turns(
        self,
        limit: int = 6,
        *,
        scope: ConversationSession | None = None,
    ) -> list[ConversationTurn]:
        """Return recent turns in one virtual conversation scope, oldest-first.

        Used to inject cross-session context into classify and answer prompts
        so short follow-ups ("批准" / "继续" / "第二个") are understood even
        when a new session is opened.  Only ``message`` and ``final`` kinds are
        included (skips progress_summary / clarify_question / preview noise).
        """
        clauses = ["t.kind IN ('message', 'final')"]
        params: list[Any] = []
        if scope is not None:
            scope_fields = (
                "company_id",
                "project_id",
                "channel",
                "account_id",
                "chat_id",
                "thread_id",
                "sender_id",
                "active_agent_id",
                "session_epoch",
            )
            for field in scope_fields:
                value = getattr(scope, field)
                if value is None:
                    clauses.append(f"s.{field} IS NULL")
                else:
                    clauses.append(f"s.{field} = ?")
                    params.append(value)
        params.append(limit)
        rows = self.db.execute(
            "SELECT t.* FROM channel_turns t "
            "JOIN channel_sessions s ON s.id = t.session_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY t.created_at DESC, t.rowid DESC "
            "LIMIT ?",
            tuple(params),
        ).fetchall()
        # Reverse so the result is oldest-first (natural reading order).
        return [self._row_to_turn(r) for r in reversed(rows)]

    def get_turn(self, turn_id: str) -> ConversationTurn | None:
        row = self.db.execute(
            "SELECT * FROM channel_turns WHERE id = ?",
            (turn_id,),
        ).fetchone()
        return self._row_to_turn(row) if row else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _next_turn_index(self, session_id: str) -> int:
        row = self.db.execute(
            "SELECT MAX(turn_index) AS mx FROM channel_turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        mx = row["mx"] if row and row["mx"] is not None else None
        return 0 if mx is None else int(mx) + 1

    @staticmethod
    def _assert_transition(
        current: SessionStatus,
        target: SessionStatus,
    ) -> None:
        """Validate ``current -> target`` against the session state machine.

        Legal moves:
        * ``open``       -> clarifying, dispatched, answered, abandoned, gated, proposed
        * ``clarifying`` -> clarifying, dispatched, answered, abandoned, gated, proposed
        * ``gated``      -> dispatched, abandoned  (spend-gate GO / give-up)
        * ``proposed``   -> dispatched, abandoned  (proposal GO / give-up)
        * terminal (dispatched / answered / abandoned) -> nothing
        """
        if current in SESSION_TERMINAL_STATUSES:
            raise IllegalSessionTransition(
                f"session is in terminal state {current.value!r}; "
                f"cannot transition to {target.value!r}"
            )
        if current == SessionStatus.GATED and target not in (
            SessionStatus.DISPATCHED,
            SessionStatus.ABANDONED,
        ):
            raise IllegalSessionTransition(
                f"gated session can only resolve to dispatched/abandoned, "
                f"not {target.value!r}"
            )
        if current == SessionStatus.PROPOSED and target not in (
            SessionStatus.DISPATCHED,
            SessionStatus.ABANDONED,
        ):
            raise IllegalSessionTransition(
                f"proposed session can only resolve to dispatched/abandoned, "
                f"not {target.value!r}"
            )

    @staticmethod
    def _row_to_session(row) -> ConversationSession:
        keys = set(row.keys())
        payload: dict = {}
        if "payload" in keys and row["payload"]:
            try:
                parsed = json.loads(row["payload"])
                if isinstance(parsed, dict):
                    payload = parsed
            except (TypeError, ValueError):
                payload = {}
        return ConversationSession(
            id=row["id"],
            state=row["state"],
            route=row["route"] if "route" in keys else None,
            clarify_turns=row["clarify_turns"] if row["clarify_turns"] else 0,
            directive_id=row["directive_id"],
            company_id=(
                row["company_id"]
                if "company_id" in keys and row["company_id"]
                else "default"
            ),
            project_id=row["project_id"],
            channel=row["channel"] if "channel" in keys else None,
            account_id=row["account_id"] if "account_id" in keys else None,
            chat_id=row["chat_id"] if "chat_id" in keys else None,
            thread_id=row["thread_id"] if "thread_id" in keys else None,
            sender_id=row["sender_id"] if "sender_id" in keys else None,
            active_agent_id=(
                row["active_agent_id"]
                if "active_agent_id" in keys and row["active_agent_id"]
                else "ceo"
            ),
            previous_agent_id=(
                row["previous_agent_id"]
                if "previous_agent_id" in keys
                else None
            ),
            handoff_id=row["handoff_id"] if "handoff_id" in keys else None,
            handoff_reason=(
                row["handoff_reason"] if "handoff_reason" in keys else None
            ),
            handoff_confidence=(
                row["handoff_confidence"]
                if "handoff_confidence" in keys
                else None
            ),
            session_epoch=(
                int(row["session_epoch"] or 0)
                if "session_epoch" in keys
                else 0
            ),
            approval_id=row["approval_id"] if "approval_id" in keys else None,
            run_id=row["run_id"] if "run_id" in keys else None,
            payload=payload,
            created_at=row["created_at"],
            closed_at=row["closed_at"] if "closed_at" in keys else None,
        )

    @staticmethod
    def _row_to_turn(row) -> ConversationTurn:
        keys = set(row.keys())
        return ConversationTurn(
            id=row["id"],
            session_id=row["session_id"],
            turn_index=row["turn_index"] if row["turn_index"] is not None else 0,
            role=row["role"],
            agent_id=(
                row["agent_id"]
                if "agent_id" in keys
                else ("ceo" if row["role"] == "ceo" else None)
            ),
            content=row["content"],
            kind=row["kind"] if "kind" in keys and row["kind"] else "message",
            cost=row["cost"] if row["cost"] is not None else 0.0,
            run_id=row["run_id"] if "run_id" in keys else None,
            directive_id=row["directive_id"] if "directive_id" in keys else None,
            created_at=row["created_at"],
        )


__all__ = [
    "ConversationStore",
    "IllegalSessionTransition",
    "MAX_CLARIFY_TURNS",
]
