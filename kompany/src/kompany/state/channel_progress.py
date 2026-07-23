"""Durable transport coordinates for editable delegation status messages."""

from __future__ import annotations

from typing import Any

from kompany.state.database import Database


class ChannelProgressStore:
    def __init__(self, db: Database):
        self.db = db

    def set(
        self,
        delegation_id: str,
        *,
        channel: str,
        chat_id: str,
        sender_id: str | None = None,
        thread_id: str | None,
        message_id: str,
        project_name: str,
        agents: list[str],
        cost_usd: float = 0.0,
    ) -> None:
        self.db.execute(
            """INSERT INTO channel_progress_messages
               (delegation_id, channel, chat_id, sender_id, thread_id,
                message_id, project_name, agents, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(delegation_id) DO UPDATE SET
                   channel = excluded.channel,
                   chat_id = excluded.chat_id,
                   sender_id = excluded.sender_id,
                   thread_id = excluded.thread_id,
                   message_id = excluded.message_id,
                   project_name = excluded.project_name,
                   agents = excluded.agents,
                   cost_usd = excluded.cost_usd,
                   updated_at = datetime('now')""",
            (
                delegation_id,
                channel,
                chat_id,
                sender_id,
                thread_id,
                message_id,
                project_name,
                ",".join(agents),
                float(cost_usd),
            ),
        )
        self.db.commit()

    def get(self, delegation_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT channel, chat_id, sender_id, thread_id, message_id,
                      project_name, agents, cost_usd, created_at
               FROM channel_progress_messages WHERE delegation_id = ?""",
            (delegation_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def clear(self, delegation_id: str) -> None:
        self.db.execute(
            "DELETE FROM channel_progress_messages WHERE delegation_id = ?",
            (delegation_id,),
        )
        self.db.commit()


__all__ = ["ChannelProgressStore"]
