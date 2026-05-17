"""Local SQLite snapshot backup and restore."""

from __future__ import annotations

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
        }
