"""Persistent replay results for authenticated remote commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from kompany.state.database import Database


class RemoteReplayStore:
    """SQLite-backed replay cache keyed by remote source and replay key."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, source: str, replay_key: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT result FROM remote_command_replays
               WHERE source = ? AND replay_key = ?""",
            (source, replay_key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["result"])

    def store(
        self,
        source: str,
        replay_key: str,
        command: str,
        result: dict[str, Any],
    ) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO remote_command_replays
               (source, replay_key, command, result, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (source, replay_key, command, json.dumps(result), _utc_now()),
        )
        self.db.commit()

    def cleanup(self, ttl_seconds: int) -> dict[str, Any]:
        cutoff = _cutoff(ttl_seconds)
        deleted = self.db.execute(
            """DELETE FROM remote_command_replays
               WHERE created_at < ?""",
            (cutoff,),
        ).rowcount
        self.db.commit()
        remaining = self.db.execute(
            """SELECT COUNT(*) AS count FROM remote_command_replays"""
        ).fetchone()["count"]
        return {
            "deleted": deleted,
            "remaining": remaining,
            "ttl_seconds": ttl_seconds,
            "cutoff": cutoff,
        }


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _cutoff(ttl_seconds: int) -> str:
    cutoff = datetime.now(UTC) - timedelta(seconds=max(0, ttl_seconds))
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")
