"""Migrations for the generic document / artifact stores.

Domain-neutral persistence behind ``state/documents.py`` and
``state/artifacts.py``. New tables live in ``_migrate()`` (shadow_costs
precedent) so databases created before this change pick them up; every
statement is idempotent (``CREATE ... IF NOT EXISTS``).
"""

from __future__ import annotations

import sqlite3


def run_migrations_documents(conn: sqlite3.Connection) -> None:
    # Versioned project documents: one row per (scope, namespace, key,
    # version). Approved rows are immutable by store contract; the unique
    # index makes version collisions impossible even under a race.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS project_documents (
               id TEXT PRIMARY KEY,
               company_id TEXT,
               project_id TEXT,
               namespace TEXT NOT NULL,
               key TEXT NOT NULL,
               version INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'draft',
               content TEXT NOT NULL DEFAULT '{}',
               checksum TEXT NOT NULL DEFAULT '',
               created_by TEXT,
               approval_id TEXT,
               predecessor_version INTEGER,
               note TEXT,
               approved_by TEXT,
               approved_at TEXT,
               rejection_reason TEXT,
               run_id TEXT,
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at TEXT
           )"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_project_documents_version
           ON project_documents(
               IFNULL(company_id, ''), IFNULL(project_id, ''),
               namespace, key, version
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_project_documents_lookup
           ON project_documents(namespace, key, status)"""
    )

    # Artifacts produced outside the DB (files, images, decks) + provenance.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS artifacts (
               id TEXT PRIMARY KEY,
               uri TEXT NOT NULL,
               mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
               checksum TEXT,
               kind TEXT NOT NULL DEFAULT '',
               metadata TEXT NOT NULL DEFAULT '{}',
               status TEXT NOT NULL DEFAULT 'active',
               status_note TEXT,
               company_id TEXT,
               project_id TEXT,
               approval_id TEXT,
               run_id TEXT,
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at TEXT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_artifacts_project_kind
           ON artifacts(IFNULL(project_id, ''), kind, status)"""
    )

    # artifact -> document version -> JSON path. Drives stale marking when a
    # successor document version is approved.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS artifact_dependencies (
               artifact_id TEXT NOT NULL,
               document_id TEXT NOT NULL,
               json_path TEXT NOT NULL DEFAULT '$',
               PRIMARY KEY (artifact_id, document_id, json_path)
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_artifact_dependencies_document
           ON artifact_dependencies(document_id)"""
    )
