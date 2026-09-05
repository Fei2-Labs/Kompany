"""Customer-evolution tables (07-24 four-layer). Separate from every Core /
Pro table so a vendor release's migrations never touch a customer's
extensions. Idempotent (``CREATE ... IF NOT EXISTS``)."""

from __future__ import annotations

import sqlite3


def run_migrations_extensions(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS extensions (
               id TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               version TEXT NOT NULL,
               owner TEXT NOT NULL DEFAULT 'customer',
               origin TEXT NOT NULL DEFAULT '',
               manifest_json TEXT NOT NULL DEFAULT '{}',
               artifact_hash TEXT NOT NULL DEFAULT '',
               pkg_path TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'installed',
               status_before_block TEXT,
               block_reason TEXT,
               approval_id TEXT,
               previous_version TEXT,
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at TEXT
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS extension_runs (
               id TEXT PRIMARY KEY,
               extension_id TEXT NOT NULL,
               status TEXT NOT NULL,
               exit_code INTEGER,
               denied_json TEXT NOT NULL DEFAULT '[]',
               result_json TEXT,
               error TEXT,
               requests INTEGER NOT NULL DEFAULT 0,
               proposals_json TEXT NOT NULL DEFAULT '[]',
               started_at TEXT NOT NULL DEFAULT (datetime('now')),
               finished_at TEXT
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_extension_runs_ext ON extension_runs(extension_id, started_at)"
    )
