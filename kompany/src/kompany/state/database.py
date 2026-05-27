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
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    result TEXT,
    parent_task_id TEXT
);

CREATE TABLE IF NOT EXISTS health_events (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    task_id TEXT,
    project_id TEXT,
    run_id TEXT,
    detail_json TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    resolved_by TEXT,
    resolved_at TEXT,
    snoozed_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    project_id TEXT,
    project_type TEXT,
    activity_kind TEXT,
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
    resolved_at TEXT,
    severity TEXT NOT NULL DEFAULT 'medium',
    predecessor_id TEXT,
    snoozed_until TEXT,
    snoozed_by TEXT
);

CREATE TABLE IF NOT EXISTS approval_comments (
    id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL,
    by_type TEXT NOT NULL,
    by_id TEXT,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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

CREATE TABLE IF NOT EXISTS debates (
    id TEXT PRIMARY KEY,
    directive_id TEXT,
    project_id TEXT,
    rounds_json TEXT NOT NULL DEFAULT '[]',
    synthesis_json TEXT,
    decision_json TEXT,
    run_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS project_episodes (
    project_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL DEFAULT '',
    payload_json TEXT,
    retention_tier TEXT NOT NULL DEFAULT 'full',
    run_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memories_agent ON agent_memories(agent_role);
-- NOTE: indexes referencing columns added by _migrate() (run_id,
-- predecessor_id, tasks.updated_at, etc.) live in _migrate() itself.
-- Putting them here would crash executescript() on databases created
-- before those columns existed, because CREATE TABLE IF NOT EXISTS is
-- a no-op for existing tables — the columns only appear after ALTER.
"""


# Tables that gain a ``run_id`` column for cross-table tracing.
# See ``kompany/core/run_context.py`` and ``.trellis/tasks/05-18-run-id-tracing``.
_RUN_ID_TABLES = (
    "audit_log",
    "agent_memories",
    "decisions",
    "tasks",
    "ledger",
    "approval_requests",
)


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
        for col, defn in [
            ("knowledge_type", "TEXT NOT NULL DEFAULT 'experiential'"),
            ("valid_until", "TEXT"),
            # Distillation P1 adds pattern-keyed UPSERT semantics. ``metadata``
            # carries the structured DistilledPattern context (confidence,
            # evidence_episode_ids); ``pattern_key`` is the idempotency key
            # used by ``AgentMemory.upsert_by_pattern_key``; ``updated_at``
            # is bumped on each pattern refresh so callers can tell when a
            # memory was last re-derived.
            ("metadata", "TEXT"),
            ("pattern_key", "TEXT"),
            ("updated_at", "TEXT"),
        ]:
            try:
                self.conn.execute(f"ALTER TABLE agent_memories ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass  # column already exists
        # Unique index lets UPSERT on (agent_role, pattern_key) match the
        # P1 distillation idempotency contract. Partial index so legacy rows
        # without a pattern_key (reflections, observations) are unaffected.
        self.conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
               idx_agent_memories_pattern
               ON agent_memories(agent_role, pattern_key)
               WHERE pattern_key IS NOT NULL"""
        )
        # activity_kind contract (05-27): carry project context + the advisory
        # work-kind on each agent_status row so the dashboard and the future
        # sprite client can render "what each agent is doing".
        for col, defn in [
            ("project_id", "TEXT"),
            ("project_type", "TEXT"),
            ("activity_kind", "TEXT"),
        ]:
            try:
                self.conn.execute(f"ALTER TABLE agent_status ADD COLUMN {col} {defn}")
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
        # Add run_id columns + per-table indexes for cross-table tracing.
        # Idempotent: ALTER fails on existing column (caught), CREATE INDEX
        # is IF NOT EXISTS.
        for table in _RUN_ID_TABLES:
            try:
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN run_id TEXT"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
            self.conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_run_id "
                f"ON {table}(run_id)"
            )
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
        # Self-learning P0: debates and project_episodes tables.
        # CREATE TABLE statements are idempotent; ALTER + CREATE INDEX make
        # this safe to re-run against older databases.
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS debates (
                   id TEXT PRIMARY KEY,
                   directive_id TEXT,
                   project_id TEXT,
                   rounds_json TEXT NOT NULL DEFAULT '[]',
                   synthesis_json TEXT,
                   decision_json TEXT,
                   run_id TEXT,
                   created_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        try:
            self.conn.execute("ALTER TABLE debates ADD COLUMN run_id TEXT")
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_debates_project_id ON debates(project_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_debates_run_id ON debates(run_id)"
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS project_episodes (
                   project_id TEXT PRIMARY KEY,
                   summary TEXT NOT NULL DEFAULT '',
                   payload_json TEXT,
                   retention_tier TEXT NOT NULL DEFAULT 'full',
                   run_id TEXT,
                   created_at TEXT NOT NULL DEFAULT (datetime('now')),
                   updated_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        try:
            self.conn.execute(
                "ALTER TABLE project_episodes ADD COLUMN run_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_episodes_run_id "
            "ON project_episodes(run_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_episodes_retention "
            "ON project_episodes(retention_tier)"
        )

        # Resilience foundation (05-18-resilience-foundation):
        # 1. tasks.updated_at — required by the stranded-task scanner so it
        #    can detect "in_progress for too long" rows. New rows get
        #    datetime('now') from the inline schema; old rows pre-migration
        #    get a no-default ALTER and we backfill from created_at.
        try:
            self.conn.execute(
                "ALTER TABLE tasks ADD COLUMN updated_at TEXT"
            )
            # Backfill: for rows that existed before the migration, treat
            # created_at as the last-known activity.
            self.conn.execute(
                "UPDATE tasks SET updated_at = created_at "
                "WHERE updated_at IS NULL"
            )
        except sqlite3.OperationalError:
            pass  # column already exists

        # 2. health_events — first-class storage for watchdog events. The
        #    inline schema covers fresh databases; this block handles
        #    upgrades on databases that pre-date the column.
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS health_events (
                   id TEXT PRIMARY KEY,
                   kind TEXT NOT NULL,
                   task_id TEXT,
                   project_id TEXT,
                   run_id TEXT,
                   detail_json TEXT,
                   status TEXT NOT NULL DEFAULT 'open',
                   resolved_by TEXT,
                   resolved_at TEXT,
                   snoozed_until TEXT,
                   created_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_health_events_project_id "
            "ON health_events(project_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_health_events_run_id "
            "ON health_events(run_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_health_events_status "
            "ON health_events(status)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status_updated_at "
            "ON tasks(status, updated_at)"
        )

        # Approval thread + RPG inbox (05-18-approval-thread-and-rpg):
        # Additive columns on approval_requests + new approval_comments table.
        for col, defn in [
            ("severity", "TEXT NOT NULL DEFAULT 'medium'"),
            ("predecessor_id", "TEXT"),
            ("snoozed_until", "TEXT"),
            ("snoozed_by", "TEXT"),
        ]:
            try:
                self.conn.execute(
                    f"ALTER TABLE approval_requests ADD COLUMN {col} {defn}"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS approval_comments (
                   id TEXT PRIMARY KEY,
                   approval_id TEXT NOT NULL,
                   by_type TEXT NOT NULL,
                   by_id TEXT,
                   body TEXT NOT NULL,
                   created_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approval_comments_approval_id "
            "ON approval_comments(approval_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approval_requests_status "
            "ON approval_requests(status)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approval_requests_predecessor_id "
            "ON approval_requests(predecessor_id)"
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
