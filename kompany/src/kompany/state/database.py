"""SQLite database connection and schema management."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    amount REAL NOT NULL,
    balance_after REAL NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    directive_id TEXT,
    project_id TEXT,
    approved_by TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    directive_id TEXT NOT NULL,
    directive_type TEXT NOT NULL,
    raw_input TEXT NOT NULL,
    classification TEXT NOT NULL,
    result TEXT NOT NULL,
    agents_involved TEXT NOT NULL,
    total_ai_cost REAL NOT NULL DEFAULT 0.0,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    target_amount REAL,
    funded_amount REAL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    triggers_directive_id TEXT,
    plan TEXT NOT NULL DEFAULT '',
    assigned_agents TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    assigned_agent TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    result TEXT,
    parent_task_id TEXT
);

CREATE TABLE IF NOT EXISTS company_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_role TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'observation',
    knowledge_type TEXT NOT NULL DEFAULT 'experiential',
    content TEXT NOT NULL,
    context TEXT,
    directive_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    valid_until TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    agent_role TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    directive_id TEXT,
    project_id TEXT
);

CREATE TABLE IF NOT EXISTS agent_status (
    agent_role TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'idle',
    current_task TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id TEXT,
    step_index INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    action_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    directive_id TEXT,
    project_id TEXT,
    requested_by TEXT,
    resolved_by TEXT,
    resolution_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS tool_authorizations (
    agent_role TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 0,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (agent_role, tool_name)
);

CREATE TABLE IF NOT EXISTS remote_command_replays (
    source TEXT NOT NULL,
    replay_key TEXT NOT NULL,
    command TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source, replay_key)
);

CREATE TABLE IF NOT EXISTS credential_vault (
    name TEXT PRIMARY KEY,
    ciphertext TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memories_agent ON agent_memories(agent_role);
"""


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
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns to existing tables if missing."""
        for col, defn in [
            ("knowledge_type", "TEXT NOT NULL DEFAULT 'experiential'"),
            ("valid_until", "TEXT"),
        ]:
            try:
                self.conn.execute(f"ALTER TABLE agent_memories ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass  # column already exists
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS tool_authorizations (
                   agent_role TEXT NOT NULL,
                   tool_name TEXT NOT NULL,
                   allowed INTEGER NOT NULL DEFAULT 0,
                   requires_approval INTEGER NOT NULL DEFAULT 0,
                   reason TEXT NOT NULL DEFAULT '',
                   updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                   PRIMARY KEY (agent_role, tool_name)
               )"""
        )
        try:
            self.conn.execute(
                "ALTER TABLE tool_authorizations ADD COLUMN requires_approval INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS remote_command_replays (
                   source TEXT NOT NULL,
                   replay_key TEXT NOT NULL,
                   command TEXT NOT NULL DEFAULT '',
                   result TEXT NOT NULL,
                   created_at TEXT NOT NULL DEFAULT (datetime('now')),
                   PRIMARY KEY (source, replay_key)
               )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS credential_vault (
                   name TEXT PRIMARY KEY,
                   ciphertext TEXT NOT NULL,
                   updated_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
