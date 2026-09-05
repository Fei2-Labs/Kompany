"""kompany merge (#46): union two forks of one company; refuse different companies."""

from __future__ import annotations

import pytest

from kompany.state.backup import BackupManager
from kompany.state.database import Database
from kompany.state.export_bundle import create_bundle
from kompany.state.merge_company import MergeRefused, merge_company


def _company(data_dir, name="Acme"):
    db = Database(data_dir)
    db.execute("INSERT OR REPLACE INTO company_config(key, value) VALUES ('company_name', ?)", (name,))
    db.execute("INSERT OR REPLACE INTO company_config(key, value) VALUES ('company_goal', ?)", (f"goal-{data_dir.name}",))
    db.commit()
    return db


def _project(db, pid, name, updated="2026-09-01 10:00:00"):
    db.execute("INSERT INTO projects(id, name, type, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
               (pid, name, "revenue", "active", "2026-08-01 00:00:00", updated))


def _task(db, tid, pid, title, status, updated):
    db.execute("INSERT INTO tasks(id, project_id, title, status, assigned_agent, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
               (tid, pid, title, status, "cro", "2026-08-01 00:00:00", updated))


def _memory(db, mid, role, key, content):
    db.execute("INSERT INTO agent_memories(id, agent_role, pattern_key, content, created_at) VALUES (?,?,?,?,?)",
               (mid, role, key, content, "2026-08-01 00:00:00"))


def _ledger(db, ts, amount, desc):
    db.execute("INSERT INTO ledger(timestamp, amount, balance_after, description, category) VALUES (?,?,?,?,?)",
               (ts, amount, 0.0, desc, "operational"))


def _fixture(tmp_path):
    local = _company(tmp_path / "local"); other = _company(tmp_path / "other")
    # shared history
    for db in (local, other):
        _project(db, "p1", "Launch")
        _task(db, "t1", "p1", "Email leads", "active", "2026-09-01 10:00:00")
        _ledger(db, "2026-09-01 09:00:00", 100.0, "seed capital")
    # the same learned pattern exists on both machines under different ids
    _memory(local, 1, "cro", "warm-intro-works", "shared")
    _memory(other, 7, "cro", "warm-intro-works", "same pattern, server copy")
    # local-only
    _project(local, "p2", "Local project"); _ledger(local, "2026-09-02 09:00:00", -5.0, "local spend")
    # other-only + divergence
    _project(other, "p3", "Server project")
    other.execute("UPDATE tasks SET status='completed', updated_at='2026-09-03 12:00:00' WHERE id='t1'")
    _memory(other, 2, "cmo", None, "server-only")  # unkeyed; id 2 also used locally
    _memory(local, 2, "cmo", None, "local memory sharing the id 2")
    _ledger(other, "2026-09-02 10:00:00", -7.0, "server spend")
    other.execute("INSERT INTO audit_log(timestamp, event_type, action) VALUES ('2026-09-02 10:00:01','x.y','server event')")
    local.commit(); other.commit(); other.close()
    return local, tmp_path / "other" / "kompany.db"


def test_merge_unions_and_newer_wins(tmp_path):
    local, other_db = _fixture(tmp_path)
    report = merge_company(local, other_db, backups=BackupManager(tmp_path / "local"))
    assert report.company == "Acme" and report.backup_id
    assert report.inserted["projects"] == 1 and report.collisions["projects"] == 1
    assert local.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 3
    assert local.execute("SELECT status FROM tasks WHERE id='t1'").fetchone()[0] == "completed"  # newer won
    assert report.updated["tasks"] == 1
    mems = local.execute("SELECT agent_role, content FROM agent_memories ORDER BY id").fetchall()
    # server's unkeyed 'server-only' joins; its duplicate pattern_key row does not; local id-2 memory untouched
    assert sorted(m[1] for m in mems) == ["local memory sharing the id 2", "server-only", "shared"]
    assert report.collisions["agent_memories(pattern_key)"] == 1
    led = local.execute("SELECT timestamp, amount, balance_after FROM ledger ORDER BY timestamp").fetchall()
    assert [round(r[1], 2) for r in led] == [100.0, -5.0, -7.0]
    assert [round(r[2], 2) for r in led] == [100.0, 95.0, 88.0]  # chain recomputed
    assert report.inserted["ledger"] == 1 and report.inserted["audit_log"] == 1
    assert "credential_vault" in report.skipped_tables
    assert "company_goal" in report.config_diffs
    assert local.execute("SELECT value FROM company_config WHERE key='company_goal'").fetchone()[0] == "goal-local"


def test_dry_run_writes_nothing(tmp_path):
    local, other_db = _fixture(tmp_path)
    report = merge_company(local, other_db, dry_run=True)
    assert report.inserted["projects"] == 1 and report.dry_run and report.backup_id is None
    assert local.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2
    assert local.execute("SELECT status FROM tasks WHERE id='t1'").fetchone()[0] == "active"


def test_refuses_different_company_and_unnamed_without_force(tmp_path):
    local, other_db = _fixture(tmp_path)
    o = Database(tmp_path / "other"); o.execute("UPDATE company_config SET value='Other Inc' WHERE key='company_name'"); o.commit(); o.close()
    with pytest.raises(MergeRefused, match="different companies"):
        merge_company(local, other_db)
    o = Database(tmp_path / "other"); o.execute("UPDATE company_config SET value='' WHERE key='company_name'"); o.commit(); o.close()
    with pytest.raises(MergeRefused, match="--force"):
        merge_company(local, other_db)
    report = merge_company(local, other_db, force=True, dry_run=True)
    assert report.company == "Acme"


def test_merge_from_encrypted_bundle(tmp_path):
    local, other_db = _fixture(tmp_path)
    bundle = tmp_path / "server.kmp"
    create_bundle(tmp_path / "other", "pw", bundle)
    with pytest.raises(MergeRefused, match="passphrase"):
        merge_company(local, bundle)
    report = merge_company(local, bundle, passphrase="pw")
    assert report.inserted["projects"] == 1
    assert local.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 3


def test_merge_is_idempotent(tmp_path):
    local, other_db = _fixture(tmp_path)
    merge_company(local, other_db)
    again = merge_company(local, other_db)
    assert sum(again.inserted.values()) == 0 and sum(again.updated.values()) == 0
    assert local.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 3
