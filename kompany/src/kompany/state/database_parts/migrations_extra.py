"""SQLite migration steps (part 2) — split from migrations.py per ADR-0003."""

from __future__ import annotations

import sqlite3

from .migrations_documents import run_migrations_documents


def run_migrations_part2(conn: sqlite3.Connection) -> None:
    """Continuation of run_migrations; applied in the same startup pass."""
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

    # ADR-0008 Step 4: generalize channel_outbox into the OUTWARD QUEUE that
    # the outward lane drains. Additive columns carry the per-action
    # classification the lane needs to resolve auto/gated and route to a
    # project executor. ALTER upgrades pre-ADR-0008 outbox rows; fresh DBs
    # already have the base table from the CREATE above. Idempotent per the
    # tasks-column precedent.
    for col, defn in [
        ("action_class", "TEXT NOT NULL DEFAULT ''"),
        ("deliverable_class", "TEXT NOT NULL DEFAULT ''"),
        ("side_effect", "TEXT NOT NULL DEFAULT ''"),
        ("estimated_cost_usd", "REAL NOT NULL DEFAULT 0"),
        ("external_ref", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE channel_outbox ADD COLUMN {col} {defn}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_outbox_status "
        "ON channel_outbox(status, created_at)"
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

    # Per-action-CLASS outward pre-authorization policy (ADR-0008 #2).
    # Sibling of tool_authorizations: one row per outward action class set
    # to 'auto' (the lane executes unattended) or 'gated' (parked for
    # `kompany approve`). The operating project sets the values; the engine
    # seeds ALL classes to the safe default 'gated'. Hard floors live in
    # core/outward_policy.py and override this table — they are never
    # persisted here. New table → _migrate() per the shadow_costs
    # precedent. Idempotent (CREATE TABLE IF NOT EXISTS).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS outward_action_policies (
               action_class TEXT PRIMARY KEY,
               mode TEXT NOT NULL DEFAULT 'gated',
               reason TEXT NOT NULL DEFAULT '',
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )

    # Generic versioned documents + artifacts (state/documents.py,
    # state/artifacts.py). Domain-neutral; plugins pick namespaces.
    run_migrations_documents(conn)
