"""SQLite database connection and schema management."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .database_parts.schema import _SCHEMA, _RUN_ID_TABLES
from .database_parts.migrations import run_migrations

__all__ = ["Database", "_SCHEMA", "_RUN_ID_TABLES"]


class Database:
    """SQLite database wrapper."""

    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "kompany.db"
        self._conn: sqlite3.Connection | None = None
        # check_same_thread=False lets FastAPI's threadpool reach the
        # connection, but sqlite3.Connection is NOT internally
        # thread-safe — concurrent execute/commit from two worker
        # threads corrupts the connection state and raises
        # InterfaceError: bad parameter or other API misuse, after
        # which every subsequent call hangs (the settings page would
        # load only the sections whose endpoints happened to win the
        # race). This lock serializes all access.
        self._lock = threading.RLock()
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
        with self._lock:
            return self.conn.execute(sql, params)

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
