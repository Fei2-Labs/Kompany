"""Runtime state store: tracks running/suspended state in company_config."""

from __future__ import annotations

from datetime import UTC, datetime

from kompany.state.database import Database

_STATE_KEY = "runtime.state"
_REASON_KEY = "runtime.reason"
_SINCE_KEY = "runtime.since"
_DEFAULT_STATE = "running"


class RuntimeStateStore:
    """Persistent engine runtime state (running | suspended)."""

    def __init__(self, db: Database):
        self.db = db

    def _read(self, key: str) -> str | None:
        row = self.db.execute(
            "SELECT value FROM company_config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _write(self, key: str, value: str | None) -> None:
        if value is None:
            self.db.execute("DELETE FROM company_config WHERE key = ?", (key,))
            return
        self.db.execute(
            """INSERT INTO company_config (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = datetime('now')""",
            (key, value),
        )

    def get(self) -> dict:
        state = self._read(_STATE_KEY) or _DEFAULT_STATE
        reason = self._read(_REASON_KEY)
        since = self._read(_SINCE_KEY)
        return {"state": state, "reason": reason, "since": since}

    def set(self, state: str, reason: str | None = None) -> dict:
        current = self.get()
        transitioned = current["state"] != state
        self._write(_STATE_KEY, state)
        self._write(_REASON_KEY, reason)
        if transitioned or current["since"] is None:
            self._write(_SINCE_KEY, datetime.now(UTC).isoformat())
        self.db.commit()
        return self.get()
