"""Virtual channel context → conversation session mapping.

The persisted column retains its legacy ``chat_id`` name, but new callers use
the canonical context key produced by ``DirectiveContext.session_key``.
"""

from __future__ import annotations

from kompany.state.database import Database


class ChannelSessionMapStore:
    """SQLite-backed context-key → session-id map."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, context_key: str) -> str | None:
        row = self.db.execute(
            "SELECT session_id FROM channel_session_map WHERE chat_id = ?",
            (str(context_key),),
        ).fetchone()
        return row["session_id"] if row else None

    def set(self, context_key: str, session_id: str) -> None:
        self.db.execute(
            """INSERT INTO channel_session_map (chat_id, session_id, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(chat_id) DO UPDATE SET
                 session_id = excluded.session_id,
                 updated_at = excluded.updated_at""",
            (str(context_key), session_id),
        )
        self.db.commit()

    def clear(self, context_key: str) -> None:
        self.db.execute(
            "DELETE FROM channel_session_map WHERE chat_id = ?",
            (str(context_key),),
        )
        self.db.commit()


__all__ = ["ChannelSessionMapStore"]
