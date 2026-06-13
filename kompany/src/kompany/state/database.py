"""SQLite database connection and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .database_parts.schema import _SCHEMA, _RUN_ID_TABLES
from .database_parts.migrations import run_migrations

__all__ = ["Database", "_SCHEMA", "_RUN_ID_TABLES"]


class Database:
    """SQLite database wrapper."""

    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "kompany.db"
        self._conn: sqlite3.Connection | None = None
        self._init_schema()
        self._migrate()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # check_same_thread=False: FastAPI runs sync endpoints AND
            # BackgroundTasks in a worker threadpool, so the connection
            # is created on one thread but used from others (e.g. the
            # post-onboarding kickoff runs execute_project in a separate
            # thread). Without this, SQLite raises "objects created in a
            # thread can only be used in that same thread", which broke
            # the shared connection and made every subsequent /status,
            # /projects, /targets return 500 after onboarding completed.
            # busy_timeout lets concurrent writes wait instead of
            # immediately erroring with "database is locked"; WAL keeps
            # reads non-blocking against a writer.
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns to existing tables if missing."""
        run_migrations(self.conn)
        self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
