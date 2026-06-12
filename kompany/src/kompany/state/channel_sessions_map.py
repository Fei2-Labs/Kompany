"""Chat-thread → CEO-channel session mapping (06-12-channels PRD D2).

One ConversationSession per transport chat thread: a Telegram chat_id
maps to the session its messages continue (clarify / gated round-trips
keep context). The map row is cleared when the session reaches a
terminal state so the next message opens a fresh session.
"""

from __future__ import annotations

from kompany.state.database import Database


class ChannelSessionMapStore:
    """SQLite-backed chat_id → session_id map."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, chat_id: str) -> str | None:
        row = self.db.execute(
            "SELECT session_id FROM channel_session_map WHERE chat_id = ?",
            (str(chat_id),),
        ).fetchone()
        return row["session_id"] if row else None

    def set(self, chat_id: str, session_id: str) -> None:
        self.db.execute(
            """INSERT INTO channel_session_map (chat_id, session_id, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(chat_id) DO UPDATE SET
                 session_id = excluded.session_id,
                 updated_at = excluded.updated_at""",
            (str(chat_id), session_id),
        )
        self.db.commit()

    def clear(self, chat_id: str) -> None:
        self.db.execute(
            "DELETE FROM channel_session_map WHERE chat_id = ?",
            (str(chat_id),),
        )
        self.db.commit()


__all__ = ["ChannelSessionMapStore"]
