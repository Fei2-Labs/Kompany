"""SQLite DDL: initial schema (CREATE TABLE / CREATE INDEX statements)."""

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
    parent_task_id TEXT,
    budget_cap_usd REAL,
    max_turns INTEGER,
    harness_session_id TEXT,
    harness_vehicle TEXT
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
-- a no-op for existing tables -- the columns only appear after ALTER.
"""

# Tables that gain a ``run_id`` column for cross-table tracing.
# See ``kompany/core/run_context.py``.
_RUN_ID_TABLES = (
    "audit_log",
    "agent_memories",
    "decisions",
    "tasks",
    "ledger",
    "approval_requests",
)
