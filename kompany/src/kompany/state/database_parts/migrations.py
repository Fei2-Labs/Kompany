"""SQLite migration steps applied at startup against existing databases."""

from __future__ import annotations

import sqlite3

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

    # Shadow costs (06-11-harness-execution-leg PR3): API-equivalent
    # value of LLM calls made under a subscription ModelSource. NEVER
    # joins the ledger / never affects balance — queryable record for
    # quota pacing and worth-it retrospectives (PRD D2). New table →
    # _migrate() per the channel_sessions precedent.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shadow_costs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               run_id TEXT,
               model TEXT NOT NULL,
               tokens_in INTEGER NOT NULL DEFAULT 0,
               tokens_out INTEGER NOT NULL DEFAULT 0,
               shadow_value_usd REAL NOT NULL DEFAULT 0.0,
               description TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_costs_run_id "
        "ON shadow_costs(run_id)"
    )

    # Harness execution leg (06-11-harness-execution-leg PR4):
    # additive task columns — per-task caps assigned at decomposition
    # time (PRD D3) and the vehicle session identity for resume after
    # engine restart (PRD D4). Inline schema covers fresh databases;
    # ALTER upgrades pre-PR4 ones. Idempotent per the approval_requests
    # precedent.
    for col, defn in [
        ("budget_cap_usd", "REAL"),
        ("max_turns", "INTEGER"),
        ("harness_session_id", "TEXT"),
        ("harness_vehicle", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {defn}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Daemon ticks (06-12-daemon-tick-loop PR1): one row per autonomous
    # ticker pass (PRD D3 step 5). Retention = last 500 rows, pruned by
    # the ticker's housekeeping step. New table → _migrate() per the
    # shadow_costs precedent.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS daemon_ticks (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               started_at TEXT NOT NULL,
               duration_ms INTEGER NOT NULL DEFAULT 0,
               actions TEXT NOT NULL DEFAULT '[]',
               outcome TEXT NOT NULL DEFAULT 'ok',
               detail TEXT,
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )

    # Self-update proposals (06-12-self-update-pipeline PR1): one row
    # per governed self-modification attempt — clone branch, post-
    # session tier, diff evidence, test summary, lifecycle status.
    # New table → _migrate() per the shadow_costs precedent.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS self_update_proposals (
               id TEXT PRIMARY KEY,
               instruction TEXT NOT NULL,
               branch TEXT NOT NULL,
               tier TEXT,
               files_changed TEXT,
               diff_stat TEXT,
               test_summary TEXT,
               session_id TEXT,
               vehicle TEXT,
               status TEXT NOT NULL DEFAULT 'running',
               approval_id TEXT,
               cost_usd REAL,
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    # Anima persona layer (06-12-anima-persona PR1): single-row
    # emotion state + date-keyed diary. New tables → _migrate() per
    # the shadow_costs precedent.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS anima_state (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               valence REAL NOT NULL DEFAULT 0.0,
               energy REAL NOT NULL DEFAULT 0.5,
               last_diary_date TEXT,
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS anima_diary (
               date TEXT PRIMARY KEY,
               entry TEXT NOT NULL,
               valence REAL NOT NULL DEFAULT 0.0,
               energy REAL NOT NULL DEFAULT 0.5,
               cost REAL NOT NULL DEFAULT 0.0,
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    # Bidirectional channels (06-12-channels): chat-thread → CEO-channel
    # session mapping (PR1), approval-gated outbound drafts (PR2), and
    # seen-mail UID tracking for the IMAP poller (PR3). New tables →
    # _migrate() per the shadow_costs precedent.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS channel_session_map (
               chat_id TEXT PRIMARY KEY,
               session_id TEXT NOT NULL,
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS channel_outbox (
               id TEXT PRIMARY KEY,
               channel TEXT NOT NULL,
               text TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'draft',
               source TEXT NOT NULL DEFAULT '',
               approval_id TEXT,
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS channel_email_seen (
               uid_key TEXT PRIMARY KEY,
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )

    # Concurrent resilient runtime — lanes on top of the daemon (ADR-0005).
    # Three additive tables, new → _migrate() per the shadow_costs
    # precedent. All idempotent (CREATE TABLE IF NOT EXISTS / CREATE INDEX
    # IF NOT EXISTS). Default single-"main"-lane behaviour stays identical;
    # these only back the additive lane-dispatch capability.
    #
    # intake_work_items: the dev-inbox / "what work is waiting" queue. An
    # append-only producer/consumer log; a worker atomically claims the
    # oldest queued row for its lane (status queued -> assigned).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS intake_work_items (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               project_id TEXT,
               task_id TEXT,
               lane_id TEXT,
               status TEXT NOT NULL DEFAULT 'queued',
               enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
               assigned_at TEXT,
               completed_at TEXT,
               detail_json TEXT
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_intake_work_items_status "
        "ON intake_work_items(status, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_intake_work_items_lane_id "
        "ON intake_work_items(lane_id)"
    )
    # lanes: the registry of independent workstreams. ``main`` (created by
    # LaneRegistry.ensure_default) gives today's single sequential ticker.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS lanes (
               lane_id TEXT PRIMARY KEY,
               name TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'healthy',
               max_concurrent INTEGER NOT NULL DEFAULT 1,
               last_heartbeat TEXT,
               detail_json TEXT,
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    # lane_leases: the own-lock/own-lease guard — one unexpired lease per
    # lane so a lane never re-enters / double-runs a task.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS lane_leases (
               lane_id TEXT PRIMARY KEY,
               task_id TEXT,
               run_id TEXT,
               acquired_at TEXT NOT NULL DEFAULT (datetime('now')),
               heartbeat_at TEXT,
               expires_at TEXT
           )"""
    )
