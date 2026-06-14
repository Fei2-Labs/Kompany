"""SQLite migration steps applied at startup against existing databases."""

from __future__ import annotations

import sqlite3

from .migrations_extra import run_migrations_part2
from .schema import _RUN_ID_TABLES


def run_migrations(conn: sqlite3.Connection) -> None:
    """Add columns / tables to existing databases if missing. Idempotent."""
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
        # Utility-weighted recall (Keel mechanism #6 port): access stats
        # feed the log1p(access_count) ranking term. Bumped only by the
        # prompt-injection path (AgentMemory.recall track_access=True).
        ("access_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_accessed_at", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE agent_memories ADD COLUMN {col} {defn}")
        except sqlite3.OperationalError:
            pass  # column already exists
    # Unique index lets UPSERT on (agent_role, pattern_key) match the
    # P1 distillation idempotency contract. Partial index so legacy rows
    # without a pattern_key (reflections, observations) are unaffected.
    conn.execute(
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
            conn.execute(f"ALTER TABLE agent_status ADD COLUMN {col} {defn}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute(
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
        conn.execute(
            "ALTER TABLE tool_authorizations ADD COLUMN requires_approval INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass
    # Add run_id columns + per-table indexes for cross-table tracing.
    # Idempotent: ALTER fails on existing column (caught), CREATE INDEX
    # is IF NOT EXISTS.
    for table in _RUN_ID_TABLES:
        try:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN run_id TEXT"
            )
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_run_id "
            f"ON {table}(run_id)"
        )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS remote_command_replays (
               source TEXT NOT NULL,
               replay_key TEXT NOT NULL,
               command TEXT NOT NULL DEFAULT '',
               result TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (source, replay_key)
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS credential_vault (
               name TEXT PRIMARY KEY,
               ciphertext TEXT NOT NULL,
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    # Self-learning P0: debates and project_episodes tables.
    # CREATE TABLE statements are idempotent; ALTER + CREATE INDEX make
    # this safe to re-run against older databases.
    conn.execute(
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
        conn.execute("ALTER TABLE debates ADD COLUMN run_id TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_debates_project_id ON debates(project_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_debates_run_id ON debates(run_id)"
    )
    conn.execute(
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
        conn.execute(
            "ALTER TABLE project_episodes ADD COLUMN run_id TEXT"
        )
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_episodes_run_id "
        "ON project_episodes(run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_episodes_retention "
        "ON project_episodes(retention_tier)"
    )

    # Resilience foundation (05-18-resilience-foundation):
    # 1. tasks.updated_at — required by the stranded-task scanner so it
    #    can detect "in_progress for too long" rows. New rows get
    #    datetime('now') from the inline schema; old rows pre-migration
    #    get a no-default ALTER and we backfill from created_at.
    try:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN updated_at TEXT"
        )
        # Backfill: for rows that existed before the migration, treat
        # created_at as the last-known activity.
        conn.execute(
            "UPDATE tasks SET updated_at = created_at "
            "WHERE updated_at IS NULL"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # 2. health_events — first-class storage for watchdog events. The
    #    inline schema covers fresh databases; this block handles
    #    upgrades on databases that pre-date the column.
    conn.execute(
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_health_events_project_id "
        "ON health_events(project_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_health_events_run_id "
        "ON health_events(run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_health_events_status "
        "ON health_events(status)"
    )
    conn.execute(
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
            conn.execute(
                f"ALTER TABLE approval_requests ADD COLUMN {col} {defn}"
            )
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute(
        """CREATE TABLE IF NOT EXISTS approval_comments (
               id TEXT PRIMARY KEY,
               approval_id TEXT NOT NULL,
               by_type TEXT NOT NULL,
               by_id TEXT,
               body TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_approval_comments_approval_id "
        "ON approval_comments(approval_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_approval_requests_status "
        "ON approval_requests(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_approval_requests_predecessor_id "
        "ON approval_requests(predecessor_id)"
    )

    # CEO channel — founder<->team conversation sessions + turns
    # (06-03-ceo-channel). Structural twin of approval_requests +
    # approval_comments: parent session row + ordered child turns. New
    # tables go in _migrate() (not _SCHEMA) so upgraded DBs get them too.
    # run_id columns are declared inline (brand-new tables, no ALTER
    # needed) with their own indexes — see run_context.py.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS channel_sessions (
               id TEXT PRIMARY KEY,
               state TEXT NOT NULL DEFAULT 'open',
               route TEXT,
               clarify_turns INTEGER NOT NULL DEFAULT 0,
               directive_id TEXT,
               project_id TEXT,
               approval_id TEXT,
               run_id TEXT,
               payload TEXT NOT NULL DEFAULT '{}',
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               closed_at TEXT
           )"""
    )
    # ``payload`` holds the PR2 gated-directive snapshot (raw_input + CEO
    # classification) so a founder GO survives an engine restart. Added
    # via ALTER too, so DBs created before PR2 (table already exists from
    # PR1) pick up the column. Idempotent: ALTER fails on existing column.
    try:
        conn.execute(
            "ALTER TABLE channel_sessions ADD COLUMN payload "
            "TEXT NOT NULL DEFAULT '{}'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_sessions_state "
        "ON channel_sessions(state)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_sessions_run_id "
        "ON channel_sessions(run_id)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS channel_turns (
               id TEXT PRIMARY KEY,
               session_id TEXT NOT NULL,
               turn_index INTEGER NOT NULL DEFAULT 0,
               role TEXT NOT NULL,
               content TEXT NOT NULL,
               kind TEXT NOT NULL DEFAULT 'message',
               cost REAL NOT NULL DEFAULT 0.0,
               run_id TEXT,
               directive_id TEXT,
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_turns_session_id "
        "ON channel_turns(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_turns_run_id "
        "ON channel_turns(run_id)"
    )

    # Continued in migrations_extra (ADR-0003 file-size split).
    run_migrations_part2(conn)
