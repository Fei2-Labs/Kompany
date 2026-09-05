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


# ---------------------------------------------------------------------------
# Integrity gate (#44): restore refuses tampered or corrupt snapshots.
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from kompany.state.backup import BackupIntegrityError, verify_backup  # noqa: E402


def test_backup_records_sha256_and_restore_reports_verified(tmp_path):
    _seed_db(tmp_path)
    mgr = BackupManager(tmp_path)
    meta = mgr.create_backup(label="hashed")
    assert len(meta["sha256"]) == 64
    out = mgr.restore_backup(meta["id"])
    assert out["verified"] == {"sha256": True, "integrity_check": True}


def test_restore_refuses_modified_snapshot(tmp_path):
    _seed_db(tmp_path)
    mgr = BackupManager(tmp_path)
    meta = mgr.create_backup(label="tamper")
    live_before = (tmp_path / "kompany.db").read_bytes()
    with open(meta["path"], "r+b") as fh:
        fh.seek(200)
        fh.write(b"\xff\xff\xff\xff")
    with pytest.raises(BackupIntegrityError, match="digest mismatch"):
        mgr.restore_backup(meta["id"])
    assert (tmp_path / "kompany.db").read_bytes() == live_before  # live db untouched


def test_restore_refuses_corrupt_legacy_snapshot_without_digest(tmp_path):
    _seed_db(tmp_path)
    mgr = BackupManager(tmp_path)
    meta = mgr.create_backup(label="legacy")
    # Simulate a pre-#44 sidecar (no digest) whose file is garbage.
    Path(meta["path"]).write_bytes(b"not a sqlite file at all" * 100)
    sidecar = tmp_path / "backups" / f"{meta['id']}.json"
    import json as _json
    m = _json.loads(sidecar.read_text()); m.pop("sha256"); sidecar.write_text(_json.dumps(m))
    with pytest.raises(BackupIntegrityError, match="not a valid SQLite"):
        mgr.restore_backup(meta["id"])


def test_legacy_snapshot_without_digest_restores_when_sound(tmp_path):
    _seed_db(tmp_path)
    mgr = BackupManager(tmp_path)
    meta = mgr.create_backup(label="legacy-ok")
    sidecar = tmp_path / "backups" / f"{meta['id']}.json"
    import json as _json
    m = _json.loads(sidecar.read_text()); m.pop("sha256"); sidecar.write_text(_json.dumps(m))
    out = mgr.restore_backup(meta["id"])
    assert out["verified"] == {"sha256": None, "integrity_check": True}


def test_verify_backup_direct(tmp_path):
    db = tmp_path / "x.db"
    import sqlite3 as _sq
    _sq.connect(str(db)).close()
    assert verify_backup(db, None)["integrity_check"] is True
    with pytest.raises(BackupIntegrityError):
        verify_backup(db, "0" * 64)
