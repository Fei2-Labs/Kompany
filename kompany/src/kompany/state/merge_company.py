"""``kompany merge`` — fold a diverged copy of the SAME company into this one (#46).

Scenario: the company ran on a laptop and on a server; both kept working.
``import`` replaces; ``merge`` unions. Rules (all additive — nothing local is
deleted or rewritten):

* **Identity gate.** Both databases must carry the same ``company_name`` in
  ``company_config``; otherwise refuse. ``--force`` only overrides an
  *empty* name on either side, never a different one (mission integrity).
* **Keyed tables** (projects, tasks, approvals, documents, artifacts, …):
  rows whose primary key is absent locally are inserted. When both sides
  have the row and the table carries ``updated_at``, the newer row wins;
  otherwise the local row stays. Every such collision is reported.
* **Memories**: union by ``(agent_role, pattern_key)``; unkeyed memories by
  id.
* **Ledger / audit**: append-only logs with autoincrement ids — rows are
  matched on their content tuple, not the id; after the union the ledger's
  ``balance_after`` chain is recomputed in timestamp order.
* **Never merged**: ``credential_vault`` (different vault keys), lane
  leases, replay caches, agent status, per-machine caches. Listed in the
  report as skipped.
* **Safety**: a verified backup is taken first; the whole merge runs in one
  transaction; ``dry_run`` rolls back and only reports.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kompany.state.database import Database


class MergeRefused(RuntimeError):
    """Preflight failed (different company, unreadable source)."""


# Tables merged by primary key. ``newer`` = column deciding a collision.
KEYED_TABLES: dict[str, str | None] = {
    "projects": "updated_at",
    "tasks": "updated_at",
    "approval_requests": None,
    "approval_comments": None,
    "decisions": None,
    "debates": None,
    "delegations": "updated_at",
    "channel_sessions": "updated_at",
    "channel_turns": None,
    "channel_handoffs": None,
    "channel_outbox": None,
    "health_events": None,
    "project_documents": None,
    "artifacts": None,
    "artifact_dependencies": None,
    "self_update_proposals": None,
    "project_episodes": "updated_at",
    "credential_approval_consumptions": None,
    "tool_authorizations": None,
    "anima_diary": None,
}
# Append-only logs with autoincrement ids: dedupe on these columns.
LOG_TABLES: dict[str, tuple[str, ...]] = {
    "ledger": ("timestamp", "amount", "description", "category", "run_id"),
    "audit_log": ("timestamp", "event_type", "action", "run_id", "agent_role"),
    "daemon_ticks": ("created_at", "run_id"),
    # INTEGER PRIMARY KEY tables: ids are per-machine counters, so match on
    # content and let autoincrement assign new ids.
    "agent_skills": ("agent_role", "name"),
    "checkpoints": ("project_id", "task_id", "step_index", "created_at"),
    "intake_work_items": ("project_id", "task_id", "enqueued_at"),
    "shadow_costs": ("run_id", "model", "created_at", "shadow_value_usd"),
}
SKIPPED_TABLES: tuple[str, ...] = (
    "credential_vault", "lane_leases", "lanes", "remote_command_replays", "agent_status",
    "channel_email_seen", "channel_session_map", "channel_progress_messages", "anima_state",
    "outward_action_policies", "company_config", "sqlite_sequence",
)


@dataclass
class MergeReport:
    source: str
    company: str
    dry_run: bool
    backup_id: str | None = None
    inserted: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    collisions: dict[str, int] = field(default_factory=dict)
    skipped_tables: list[str] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)
    config_diffs: dict[str, dict[str, Any]] = field(default_factory=dict)
    ledger_recomputed_rows: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["total_inserted"] = sum(self.inserted.values())
        d["total_updated"] = sum(self.updated.values())
        return d


def _cols(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()]


def _pk(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    rows = conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()
    return [r[1] for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]


def _tables(conn: sqlite3.Connection, schema: str) -> set[str]:
    return {r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")}


def prepare_source(path: Path, passphrase: str | None, workdir: Path) -> Path:
    """Materialise the other side as a migrated ``kompany.db`` inside ``workdir``."""
    path = Path(path).expanduser()
    if not path.exists():
        raise MergeRefused(f"source not found: {path}")
    other_dir = workdir / "other"
    other_dir.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".kmp" or path.name.endswith(".kmp"):
        if not passphrase:
            raise MergeRefused("a .kmp bundle needs --passphrase")
        from kompany.state.export_bundle import import_bundle

        import_bundle(path, passphrase, other_dir, force=True)
        db_path = other_dir / "kompany.db"
    else:
        db_path = other_dir / "kompany.db"
        shutil.copy2(path, db_path)
    # Opening through Database applies this build's migrations so both sides
    # share one schema before ATTACH.
    Database(other_dir).close()
    return db_path


def merge_company(
    local_db: Database,
    source: Path,
    *,
    passphrase: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    backups: Any = None,
) -> MergeReport:
    workdir = Path(tempfile.mkdtemp(prefix="kompany-merge-"))
    try:
        other_path = prepare_source(source, passphrase, workdir)
        report = MergeReport(source=str(source), company="", dry_run=dry_run)
        conn = local_db.conn
        with local_db.locked():
            conn.execute("ATTACH DATABASE ? AS other", (str(other_path),))
            try:
                _preflight(conn, report, force)
                if not dry_run and backups is not None:
                    report.backup_id = backups.create_backup(label="pre-merge", kind="manual")["id"]
                conn.execute("BEGIN")
                try:
                    _merge_keyed(conn, report)
                    _merge_memories(conn, report)
                    _merge_logs(conn, report)
                    report.ledger_recomputed_rows = _recompute_ledger(conn)
                    if dry_run:
                        conn.execute("ROLLBACK")
                        report.notes.append("dry run — nothing written")
                    else:
                        conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                conn.execute("DETACH DATABASE other")
        return report
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _config(conn: sqlite3.Connection, schema: str) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute(f"SELECT key, value FROM {schema}.company_config")}


def _preflight(conn: sqlite3.Connection, report: MergeReport, force: bool) -> None:
    mine, theirs = _config(conn, "main"), _config(conn, "other")
    my_name, their_name = (mine.get("company_name") or "").strip(), (theirs.get("company_name") or "").strip()
    if my_name and their_name and my_name != their_name:
        raise MergeRefused(f"different companies: local '{my_name}' vs source '{their_name}' — merge only unites forks of one company")
    if (not my_name or not their_name) and not force:
        raise MergeRefused("one side has no company_name; pass --force if you are sure both are the same company")
    report.company = my_name or their_name
    for k in sorted(set(mine) | set(theirs)):
        if mine.get(k) != theirs.get(k):
            report.config_diffs[k] = {"local": mine.get(k), "source": theirs.get(k)}
    report.skipped_tables = [t for t in SKIPPED_TABLES if t in _tables(conn, "other")]
    if report.config_diffs:
        report.notes.append("company_config differs on: " + ", ".join(report.config_diffs) + " (local kept)")


def _merge_keyed(conn: sqlite3.Connection, report: MergeReport) -> None:
    other_tables = _tables(conn, "other")
    for table, newer in KEYED_TABLES.items():
        if table not in other_tables or table not in _tables(conn, "main"):
            if table in KEYED_TABLES:
                report.missing_tables.append(table)
            continue
        cols = [c for c in _cols(conn, table, "other") if c in _cols(conn, table)]
        pk = _pk(conn, table)
        if not pk or not cols:
            continue
        collist = ", ".join(cols)
        join = " AND ".join(f"m.{k} = o.{k}" for k in pk)
        # collisions = rows both sides already have (count BEFORE inserting)
        n_coll = conn.execute(f"SELECT COUNT(*) FROM other.{table} o JOIN main.{table} m ON {join}").fetchone()[0]
        if n_coll:
            report.collisions[table] = n_coll
        # rows absent locally
        cur = conn.execute(
            f"INSERT INTO main.{table} ({collist}) SELECT {', '.join('o.' + c for c in cols)} "
            f"FROM other.{table} o WHERE NOT EXISTS (SELECT 1 FROM main.{table} m WHERE {join})"
        )
        if cur.rowcount:
            report.inserted[table] = cur.rowcount
        if newer and newer in cols and n_coll:
            sets = ", ".join(f"{c} = (SELECT o.{c} FROM other.{table} o WHERE {join})" for c in cols if c not in pk)
            where_newer = (f"EXISTS (SELECT 1 FROM other.{table} o WHERE {join} AND o.{newer} > m.{newer})")
            cur = conn.execute(f"UPDATE main.{table} AS m SET {sets} WHERE {where_newer}")
            if cur.rowcount:
                report.updated[table] = cur.rowcount


def _merge_memories(conn: sqlite3.Connection, report: MergeReport) -> None:
    """agent_memories has an autoincrement id: match keyed rows on
    (agent_role, pattern_key), unkeyed rows on (agent_role, content, created_at)."""
    if "agent_memories" not in _tables(conn, "other"):
        report.missing_tables.append("agent_memories"); return
    cols = [c for c in _cols(conn, "agent_memories", "other") if c in _cols(conn, "agent_memories") and c != "id"]
    collist = ", ".join(cols); sel = ", ".join("o." + c for c in cols)
    dup = conn.execute(
        "SELECT COUNT(*) FROM other.agent_memories o WHERE o.pattern_key IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM main.agent_memories m WHERE m.agent_role = o.agent_role AND m.pattern_key = o.pattern_key)"
    ).fetchone()[0]
    cur = conn.execute(
        f"INSERT INTO main.agent_memories ({collist}) SELECT {sel} FROM other.agent_memories o WHERE "
        "(o.pattern_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM main.agent_memories m "
        "  WHERE m.agent_role = o.agent_role AND m.pattern_key = o.pattern_key)) "
        "OR (o.pattern_key IS NULL AND NOT EXISTS (SELECT 1 FROM main.agent_memories m "
        "  WHERE m.agent_role = o.agent_role AND m.content = o.content AND m.created_at IS o.created_at))"
    )
    if cur.rowcount:
        report.inserted["agent_memories"] = cur.rowcount
    if dup:
        report.collisions["agent_memories(pattern_key)"] = dup


def _merge_logs(conn: sqlite3.Connection, report: MergeReport) -> None:
    for table, key in LOG_TABLES.items():
        if table not in _tables(conn, "other") or table not in _tables(conn, "main"):
            continue
        cols = [c for c in _cols(conn, table, "other") if c in _cols(conn, table) and c != "id"]
        keycols = [k for k in key if k in cols]
        if not keycols:
            continue
        match = " AND ".join(f"m.{k} IS o.{k}" for k in keycols)
        cur = conn.execute(
            f"INSERT INTO main.{table} ({', '.join(cols)}) SELECT {', '.join('o.' + c for c in cols)} "
            f"FROM other.{table} o WHERE NOT EXISTS (SELECT 1 FROM main.{table} m WHERE {match})"
        )
        if cur.rowcount:
            report.inserted[table] = cur.rowcount


def _recompute_ledger(conn: sqlite3.Connection) -> int:
    """Rebuild balance_after in chronological order (ids from both sides interleave)."""
    rows = conn.execute("SELECT id, amount FROM main.ledger ORDER BY timestamp, id").fetchall()
    balance = 0.0
    for rid, amount in rows:
        balance += float(amount or 0.0)
        conn.execute("UPDATE main.ledger SET balance_after = ? WHERE id = ?", (round(balance, 6), rid))
    return len(rows)


__all__ = ["KEYED_TABLES", "LOG_TABLES", "MergeRefused", "MergeReport", "SKIPPED_TABLES", "merge_company", "prepare_source"]
