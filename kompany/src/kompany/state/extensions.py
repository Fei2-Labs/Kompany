"""Customer extension registry (layer 3 of the four-layer model).

Rows describe installed extension packages and their runs. Statuses:
``installed`` (awaiting founder approval) → ``active`` → ``disabled`` /
``removed``; ``blocked`` is orthogonal and set by the Core compatibility
check (``status_before_block`` remembers where to return). Nothing here is
ever deleted by a vendor update; ``remove`` is a status.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from kompany.state.database import Database

STATUSES: frozenset[str] = frozenset({"installed", "active", "disabled", "blocked", "removed"})


class ExtensionStore:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, manifest: dict[str, Any], *, artifact_hash: str, pkg_path: str,
               status: str = "installed", previous_version: str | None = None) -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError(f"invalid extension status {status!r}")
        with self.db.locked():
            self.db.execute(
                """INSERT INTO extensions
                       (id, name, version, owner, origin, manifest_json, artifact_hash, pkg_path,
                        status, previous_version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET
                       name = excluded.name, version = excluded.version, owner = excluded.owner,
                       origin = excluded.origin, manifest_json = excluded.manifest_json,
                       artifact_hash = excluded.artifact_hash, pkg_path = excluded.pkg_path,
                       status = excluded.status, status_before_block = NULL, block_reason = NULL,
                       approval_id = NULL, previous_version = excluded.previous_version,
                       updated_at = datetime('now')""",
                (manifest["id"], manifest["name"], manifest["version"], manifest.get("owner", "customer"),
                 manifest.get("origin", ""), json.dumps(manifest), artifact_hash, pkg_path, status, previous_version),
            )
            self.db.commit()
        return self.get(manifest["id"])  # type: ignore[return-value]

    def set_status(self, extension_id: str, status: str, *, reason: str | None = None,
                   approval_id: str | None = None) -> dict[str, Any] | None:
        if status not in STATUSES:
            raise ValueError(f"invalid extension status {status!r}")
        row = self.get(extension_id)
        if row is None:
            return None
        before = row["status"] if status == "blocked" and row["status"] != "blocked" else row.get("status_before_block")
        if status == "blocked" and reason is None:
            reason = row.get("block_reason")  # re-stamping a blocked row keeps its reason
        with self.db.locked():
            self.db.execute(
                """UPDATE extensions SET status = ?, block_reason = ?, status_before_block = ?,
                       approval_id = COALESCE(?, approval_id), updated_at = datetime('now')
                   WHERE id = ?""",
                (status, reason if status == "blocked" else None, before if status == "blocked" else None,
                 approval_id, extension_id),
            )
            self.db.commit()
        return self.get(extension_id)

    def unblock(self, extension_id: str) -> dict[str, Any] | None:
        row = self.get(extension_id)
        if row is None or row["status"] != "blocked":
            return row
        return self.set_status(extension_id, row.get("status_before_block") or "installed")

    def get(self, extension_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM extensions WHERE id = ?", (extension_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, *, include_removed: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM extensions" + ("" if include_removed else " WHERE status != 'removed'") + " ORDER BY id"
        return [self._row(r) for r in self.db.execute(sql).fetchall()]

    def record_run(self, extension_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
        run_id = outcome.get("run_id") or uuid4().hex[:12]
        with self.db.locked():
            self.db.execute(
                """INSERT INTO extension_runs
                       (id, extension_id, status, exit_code, denied_json, result_json, error, requests,
                        proposals_json, finished_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (run_id, extension_id, "ok" if outcome.get("ok") else "failed", outcome.get("exit_code"),
                 json.dumps(outcome.get("denied") or []), json.dumps(outcome.get("result")),
                 outcome.get("error"), int(outcome.get("requests") or 0), json.dumps(outcome.get("proposals") or [])),
            )
            self.db.commit()
        return {"run_id": run_id, "extension_id": extension_id, **outcome}

    def runs(self, extension_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM extension_runs WHERE extension_id = ? ORDER BY started_at DESC, id DESC LIMIT ?",
            (extension_id, int(limit)),
        ).fetchall()
        return [{"id": r["id"], "extension_id": r["extension_id"], "status": r["status"], "exit_code": r["exit_code"],
                 "denied": json.loads(r["denied_json"] or "[]"), "result": json.loads(r["result_json"]) if r["result_json"] else None,
                 "error": r["error"], "requests": r["requests"], "proposals": json.loads(r["proposals_json"] or "[]"),
                 "started_at": r["started_at"], "finished_at": r["finished_at"]} for r in rows]

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        return {"id": r["id"], "name": r["name"], "version": r["version"], "owner": r["owner"], "origin": r["origin"],
                "manifest": json.loads(r["manifest_json"] or "{}"), "artifact_hash": r["artifact_hash"],
                "pkg_path": r["pkg_path"], "status": r["status"], "status_before_block": r["status_before_block"],
                "block_reason": r["block_reason"], "approval_id": r["approval_id"],
                "previous_version": r["previous_version"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


__all__ = ["STATUSES", "ExtensionStore"]
