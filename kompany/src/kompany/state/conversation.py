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


def _publish_channel_updated(reason: str, session_id: str | None = None) -> None:
    """Best-effort SSE push that a channel session changed."""
    try:
        get_event_hub().publish(
            "channel.updated",
            {"reason": reason, "session_id": session_id},
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
               (id, state, route, clarify_turns, directive_id, project_id,
                approval_id, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id,
                session.state.value,
                session.route,
                session.clarify_turns,
                session.directive_id,
                session.project_id,
                session.approval_id,
                session.run_id,
            ),
        )
        self.db.commit()
        _publish_channel_updated("session_created", session.id)
        # Re-read to pick up the SQL-side ``created_at`` default.
        return self.get_session(session.id) or session

    def get_session(self, session_id: str) -> ConversationSession | None:
        row = self.db.execute(
            "SELECT * FROM channel_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return self._row_to_session(row) if row else None

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
        _publish_channel_updated(f"state.{target.value}", session_id)
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
            content=content,
            kind=kind,
            cost=cost,
            run_id=rid,
            directive_id=directive_id,
        )
        self.db.execute(
            """INSERT INTO channel_turns
               (id, session_id, turn_index, role, content, kind, cost,
                run_id, directive_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                turn.id,
                turn.session_id,
                turn.turn_index,
                turn.role,
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
        _publish_channel_updated("turn_added", session_id)
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
        * ``open``       -> clarifying, dispatched, answered, abandoned, gated
        * ``clarifying`` -> clarifying, dispatched, answered, abandoned, gated
        * ``gated``      -> dispatched, abandoned (PR2 resume / give-up)
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

    @staticmethod
    def _row_to_session(row) -> ConversationSession:
        keys = set(row.keys())
        return ConversationSession(
            id=row["id"],
            state=row["state"],
            route=row["route"] if "route" in keys else None,
            clarify_turns=row["clarify_turns"] if row["clarify_turns"] else 0,
            directive_id=row["directive_id"],
            project_id=row["project_id"],
            approval_id=row["approval_id"] if "approval_id" in keys else None,
            run_id=row["run_id"] if "run_id" in keys else None,
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
