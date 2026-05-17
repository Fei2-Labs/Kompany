"""Tests for BackupManager and engine backup/restore methods."""

from __future__ import annotations

import pytest

from kompany.state.backup import BackupManager
from kompany.state.database import Database


def _seed_db(data_dir):
    db = Database(data_dir)
    db.execute("INSERT INTO ledger (amount, balance_after, description, category) VALUES (?, ?, ?, ?)",
               (10.0, 10.0, "seed", "operational"))
    db.commit()
    db.close()


def test_create_and_list_backup_round_trip(tmp_path):
    _seed_db(tmp_path)
    mgr = BackupManager(tmp_path)

    meta = mgr.create_backup(label="snapshot one")

    assert meta["id"].endswith("-snapshot-one")
    assert meta["label"] == "snapshot one"
    assert meta["kind"] == "manual"
    assert meta["size_bytes"] > 0

    listed = mgr.list_backups()
    assert len(listed) == 1
    assert listed[0]["id"] == meta["id"]


def test_list_backups_newest_first(tmp_path):
    _seed_db(tmp_path)
    mgr = BackupManager(tmp_path)
    first = mgr.create_backup(label="a")
    second = mgr.create_backup(label="b")

    listed = mgr.list_backups()

    # Newest first: second has same or later timestamp
    assert listed[0]["id"] >= listed[1]["id"]
    assert {b["id"] for b in listed} == {first["id"], second["id"]}


def test_restore_backup_unknown_raises(tmp_path):
    _seed_db(tmp_path)
    mgr = BackupManager(tmp_path)

    with pytest.raises(FileNotFoundError):
        mgr.restore_backup("does-not-exist")


def test_restore_backup_overwrites_live_db(tmp_path):
    """Snapshot at state A, mutate to B, restore A -> live db reflects A."""
    db = Database(tmp_path)
    db.execute("INSERT INTO ledger (amount, balance_after, description, category) VALUES (?, ?, ?, ?)",
               (10.0, 10.0, "A", "operational"))
    db.commit()
    db.close()

    mgr = BackupManager(tmp_path)
    snap = mgr.create_backup(label="A")

    db = Database(tmp_path)
    db.execute("INSERT INTO ledger (amount, balance_after, description, category) VALUES (?, ?, ?, ?)",
               (5.0, 15.0, "B", "operational"))
    db.commit()
    rows_before = db.execute("SELECT description FROM ledger ORDER BY id").fetchall()
    db.close()

    assert {r["description"] for r in rows_before} == {"A", "B"}

    mgr.restore_backup(snap["id"])

    db = Database(tmp_path)
    rows_after = db.execute("SELECT description FROM ledger ORDER BY id").fetchall()
    db.close()
    assert {r["description"] for r in rows_after} == {"A"}
