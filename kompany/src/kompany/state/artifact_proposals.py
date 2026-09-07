"""Artifact-evolution proposal rows (08-29 R2).

One row per propose attempt on the artifact lane. Lifecycle:
``running`` → ``applied`` (commit survived doctor) | ``reverted`` (doctor
failed, commit reverted) | ``rejected`` (validation refused, nothing
written) | ``failed`` (LLM/budget/IO). ``pending`` is reserved for the
cheap fallback of inserting an approval gate later (ADR-lite in the task).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from kompany.state.database import Database

STATUSES: frozenset[str] = frozenset({"running", "applied", "reverted", "rejected", "failed", "pending"})
KINDS: tuple[str, ...] = ("soul", "workflow", "plugin")
_UPDATABLE: frozenset[str] = frozenset({
    "status", "commit_sha", "revert_sha", "diff_stat", "doctor_status", "flags", "cost_usd", "summary",
    "rationale", "error", "target",
})


class ArtifactProposalStore:
    def __init__(self, db: Database):
        self.db = db

    def create(self, kind: str, target: str, instruction: str, run_id: str | None = None) -> str:
        if kind not in KINDS:
            raise ValueError(f"invalid artifact kind {kind!r}; expected one of {KINDS}")
        pid = uuid4().hex[:8]
        self.db.execute(
            """INSERT INTO artifact_proposals (id, kind, target, instruction, status, run_id)
               VALUES (?, ?, ?, ?, 'running', ?)""",
            (pid, kind, target, instruction, run_id),
        )
        self.db.commit()
        return pid

    def update(self, pid: str, **fields: Any) -> dict[str, Any] | None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"invalid status {fields['status']!r}")
        if "flags" in fields and not isinstance(fields["flags"], str):
            fields["flags"] = json.dumps(fields["flags"])
        if not fields:
            return self.get(pid)
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = datetime('now')"
        self.db.execute(f"UPDATE artifact_proposals SET {sets} WHERE id = ?", (*fields.values(), pid))
        self.db.commit()
        return self.get(pid)

    def get(self, pid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM artifact_proposals WHERE id = ?", (pid,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM artifact_proposals"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"; params.append(status)
        # rowid, not the random id, breaks same-second ties → insertion order
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"; params.append(int(limit))
        return [self._row(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    def spent_today_usd(self) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM artifact_proposals WHERE date(created_at) = date('now')"
        ).fetchone()
        return float(row["total"] or 0.0)

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r)
        try:
            d["flags"] = json.loads(d.get("flags") or "[]")
        except (TypeError, ValueError):
            d["flags"] = []
        return d


__all__ = ["KINDS", "STATUSES", "ArtifactProposalStore"]
