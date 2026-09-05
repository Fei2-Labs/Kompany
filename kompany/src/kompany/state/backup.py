"""Local SQLite snapshot backup and restore."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    text = (text or "").lower().strip()
    text = _SLUG_RE.sub("-", text).strip("-")
    return text or "manual"


class BackupManager:
    """Create, list, and restore SQLite snapshots of the live database."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.db_path = data_dir / "kompany.db"
        self.backups_dir = data_dir / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self, label: str = "manual", kind: str = "manual") -> dict:
        """Create a snapshot of the live database. Returns metadata dict."""
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        slug = _slug(label)
        backup_id = f"{ts}-{slug}"
        target = self.backups_dir / f"{backup_id}.db"
        sidecar = self.backups_dir / f"{backup_id}.json"

        if self.db_path.exists():
            src = sqlite3.connect(str(self.db_path))
            try:
                dest = sqlite3.connect(str(target))
                try:
                    src.backup(dest)
                finally:
                    dest.close()
            finally:
                src.close()
        else:
            target.touch()

        meta = {
            "id": backup_id,
            "label": label,
            "kind": kind,
            "path": str(target),
            "size_bytes": target.stat().st_size,
            # Integrity (#44): restore refuses a snapshot whose bytes changed.
            "sha256": _sha256(target),
            "created_at": datetime.now(UTC).isoformat(),
        }
        sidecar.write_text(json.dumps(meta, indent=2))
        return meta

    def list_backups(self) -> list[dict]:
        """Return all backups newest first."""
        records: list[dict] = []
        for sidecar in self.backups_dir.glob("*.json"):
            try:
                meta = json.loads(sidecar.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            records.append(meta)
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return records

    def get(self, backup_id: str) -> dict | None:
        sidecar = self.backups_dir / f"{backup_id}.json"
        if not sidecar.exists():
            return None
        try:
            return json.loads(sidecar.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def restore_backup(self, backup_id: str) -> dict:
        """Restore a backup over the live db.

        The caller is responsible for closing the live connection BEFORE
        calling this method and re-initializing dependent stores after.
        """
        meta = self.get(backup_id)
        if meta is None:
            raise FileNotFoundError(f"Backup '{backup_id}' not found")
        source = Path(meta["path"])
        if not source.exists():
            raise FileNotFoundError(f"Backup file missing: {source}")
        # Integrity gate (#44): bytes must match the recorded digest (legacy
        # sidecars without one are only structurally checked), and SQLite
        # itself must consider the file sound. Never overwrite a live db
        # with a snapshot that fails either test.
        verified = verify_backup(source, meta.get("sha256"))

        # Remove WAL/SHM sidecar files from previous live db so the restored
        # snapshot is the sole source of truth.
        for suffix in ("-wal", "-shm"):
            sidecar = self.db_path.with_name(self.db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

        shutil.copy2(source, self.db_path)
        return {
            "id": backup_id,
            "restored_from": str(source),
            "restored_at": datetime.now(UTC).isoformat(),
            "verified": verified,
        }


class BackupIntegrityError(RuntimeError):
    """The snapshot is corrupt or was modified after it was written."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_backup(source: Path, expected_sha256: str | None) -> dict:
    """Digest + ``PRAGMA integrity_check``; raises :class:`BackupIntegrityError`.

    Returns ``{"sha256": bool | None, "integrity_check": True}`` — ``sha256`` is
    ``None`` for legacy sidecars that recorded no digest.
    """
    digest_ok: bool | None = None
    if expected_sha256:
        actual = _sha256(source)
        if actual != expected_sha256:
            raise BackupIntegrityError(
                f"backup {source.name} digest mismatch: recorded {expected_sha256[:12]}…, "
                f"file is {actual[:12]}… — refusing to restore a modified snapshot"
            )
        digest_ok = True
    if source.stat().st_size == 0:
        # An empty placeholder from a pre-db backup restores to an empty db.
        return {"sha256": digest_ok, "integrity_check": True}
    try:
        conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise BackupIntegrityError(f"backup {source.name} is not a valid SQLite database: {exc}") from exc
    if not row or str(row[0]).lower() != "ok":
        raise BackupIntegrityError(f"backup {source.name} failed PRAGMA integrity_check: {row[0] if row else 'no result'}")
    return {"sha256": digest_ok, "integrity_check": True}
