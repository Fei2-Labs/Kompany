"""Generic artifact registry with document dependencies (domain-neutral).

An artifact is anything produced by a workflow that lives outside the
database: an image, a PDF, a generated copy deck, a font file. The store
records *where it is* (``uri``), *what it is* (``mime_type``, ``checksum``),
*who made it* (``run_id``, ``approval_id``), free-form ``metadata`` (provider,
prompt hash, cost, dimensions — the engine never interprets it), and which
**document versions** it depends on, down to a JSON path.

Dependency invalidation: when a successor document version is approved, the
caller diffs old vs new content (:func:`changed_json_paths`), asks
:meth:`ArtifactStore.dependents` which artifacts reference any changed path,
and marks them ``stale``. Nothing is deleted or regenerated automatically.

Statuses: ``active`` → ``stale`` (upstream changed) or ``quarantined``
(rejected direction — must never be reused as a positive example).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, Field

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import _short_id


class ArtifactStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    QUARANTINED = "quarantined"


class Artifact(BaseModel):
    id: str = Field(default_factory=_short_id)
    uri: str
    mime_type: str = "application/octet-stream"
    checksum: str | None = None
    kind: str = ""
    """Caller-defined class, e.g. ``branding.logo`` — free string."""
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: ArtifactStatus = ArtifactStatus.ACTIVE
    status_note: str | None = None
    company_id: str | None = None
    project_id: str | None = None
    approval_id: str | None = None
    run_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None


class ArtifactDependency(BaseModel):
    artifact_id: str
    document_id: str
    json_path: str = "$"
    """JSON path inside the document version, ``$`` = whole document."""


class ArtifactStore:
    """SQLite-backed artifact registry (``artifacts`` + ``artifact_dependencies``)."""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def register(
        self,
        uri: str,
        *,
        mime_type: str = "application/octet-stream",
        checksum: str | None = None,
        kind: str = "",
        metadata: dict[str, Any] | None = None,
        company_id: str | None = None,
        project_id: str | None = None,
        approval_id: str | None = None,
        dependencies: Iterable[tuple[str, str]] | None = None,
    ) -> Artifact:
        """Record a produced artifact and its ``(document_id, json_path)`` deps."""
        if not uri:
            raise ValueError("uri is required")
        art = Artifact(
            uri=uri,
            mime_type=mime_type,
            checksum=checksum,
            kind=kind,
            metadata=dict(metadata or {}),
            company_id=company_id,
            project_id=project_id,
            approval_id=approval_id,
            run_id=current_run_id(),
        )
        with self.db.locked():
            self.db.execute(
                """INSERT INTO artifacts
                   (id, uri, mime_type, checksum, kind, metadata, status,
                    status_note, company_id, project_id, approval_id, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    art.id,
                    art.uri,
                    art.mime_type,
                    art.checksum,
                    art.kind,
                    json.dumps(art.metadata),
                    art.status.value,
                    art.status_note,
                    art.company_id,
                    art.project_id,
                    art.approval_id,
                    art.run_id,
                ),
            )
            for document_id, json_path in dependencies or ():
                self._insert_dependency(art.id, document_id, json_path)
            self.db.commit()
        return self.get(art.id)  # type: ignore[return-value]

    def add_dependency(
        self, artifact_id: str, document_id: str, json_path: str = "$"
    ) -> ArtifactDependency:
        self._require(artifact_id)
        self._insert_dependency(artifact_id, document_id, json_path)
        self.db.commit()
        return ArtifactDependency(
            artifact_id=artifact_id, document_id=document_id, json_path=json_path
        )

    def mark_stale(self, artifact_ids: Iterable[str], note: str | None = None) -> list[str]:
        """Flag active artifacts as stale. Quarantined ones are left alone."""
        changed: list[str] = []
        with self.db.locked():
            for aid in artifact_ids:
                cur = self.db.execute(
                    """UPDATE artifacts
                       SET status = ?, status_note = ?, updated_at = datetime('now')
                       WHERE id = ? AND status = ?""",
                    (
                        ArtifactStatus.STALE.value,
                        note,
                        aid,
                        ArtifactStatus.ACTIVE.value,
                    ),
                )
                if cur.rowcount:
                    changed.append(aid)
            self.db.commit()
        return changed

    def quarantine(self, artifact_id: str, reason: str) -> Artifact:
        """Rejected direction: never surfaces as a positive example again."""
        self._require(artifact_id)
        self.db.execute(
            """UPDATE artifacts
               SET status = ?, status_note = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (ArtifactStatus.QUARANTINED.value, reason, artifact_id),
        )
        self.db.commit()
        return self.get(artifact_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, artifact_id: str) -> Artifact | None:
        row = self.db.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        return self._row(row) if row else None

    def list(
        self,
        *,
        project_id: str | None = None,
        kind: str | None = None,
        status: ArtifactStatus | str | None = None,
    ) -> list[Artifact]:
        sql = "SELECT * FROM artifacts WHERE 1 = 1"
        params: list[Any] = []
        if project_id is not None:
            sql += " AND IFNULL(project_id, '') = ?"
            params.append(project_id)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value if isinstance(status, ArtifactStatus) else status)
        sql += " ORDER BY created_at ASC, id ASC"
        rows = self.db.execute(sql, tuple(params)).fetchall()
        return [self._row(r) for r in rows]

    def dependencies(self, artifact_id: str) -> list[ArtifactDependency]:
        rows = self.db.execute(
            """SELECT artifact_id, document_id, json_path
               FROM artifact_dependencies WHERE artifact_id = ?
               ORDER BY document_id, json_path""",
            (artifact_id,),
        ).fetchall()
        return [
            ArtifactDependency(
                artifact_id=r["artifact_id"],
                document_id=r["document_id"],
                json_path=r["json_path"],
            )
            for r in rows
        ]

    def dependents(
        self,
        document_id: str,
        changed_paths: Iterable[str] | None = None,
    ) -> list[Artifact]:
        """Artifacts depending on ``document_id`` (optionally on changed paths).

        A dependency on ``$`` matches any change. A dependency on
        ``$.colors`` matches a changed path ``$.colors.primary`` and vice
        versa (prefix match in either direction), so both coarse and fine
        declarations behave sensibly.
        """
        rows = self.db.execute(
            """SELECT DISTINCT a.*, d.json_path AS dep_path
               FROM artifacts a
               JOIN artifact_dependencies d ON d.artifact_id = a.id
               WHERE d.document_id = ?
               ORDER BY a.created_at ASC, a.id ASC""",
            (document_id,),
        ).fetchall()
        if changed_paths is None:
            seen: dict[str, Artifact] = {}
            for r in rows:
                seen.setdefault(r["id"], self._row(r))
            return list(seen.values())
        changed = [p for p in changed_paths]
        hits: dict[str, Artifact] = {}
        for r in rows:
            dep = r["dep_path"] or "$"
            if dep == "$" or any(_path_overlaps(dep, c) for c in changed):
                hits.setdefault(r["id"], self._row(r))
        return list(hits.values())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _insert_dependency(self, artifact_id: str, document_id: str, json_path: str) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO artifact_dependencies
               (artifact_id, document_id, json_path) VALUES (?, ?, ?)""",
            (artifact_id, document_id, json_path or "$"),
        )

    def _require(self, artifact_id: str) -> Artifact:
        art = self.get(artifact_id)
        if art is None:
            raise LookupError(f"artifact not found: {artifact_id}")
        return art

    @staticmethod
    def _row(row: Any) -> Artifact:
        created = _parse_dt(row["created_at"]) or datetime.now(UTC)
        return Artifact(
            id=row["id"],
            uri=row["uri"],
            mime_type=row["mime_type"] or "application/octet-stream",
            checksum=row["checksum"],
            kind=row["kind"] or "",
            metadata=json.loads(row["metadata"] or "{}"),
            status=ArtifactStatus(row["status"]),
            status_note=row["status_note"],
            company_id=row["company_id"],
            project_id=row["project_id"],
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            created_at=created,
            updated_at=_parse_dt(row["updated_at"]),
        )


# ---------------------------------------------------------------------------
# JSON-path diff helpers (pure functions)
# ---------------------------------------------------------------------------


def changed_json_paths(old: Any, new: Any, prefix: str = "$") -> set[str]:
    """Leaf-level JSON paths whose value differs between ``old`` and ``new``.

    Dict keys recurse as ``$.a.b``; list items as ``$.a[0]``. Added or
    removed keys count as changed. Scalars compare by equality.
    """
    if isinstance(old, dict) and isinstance(new, dict):
        out: set[str] = set()
        for k in set(old) | set(new):
            child = f"{prefix}.{k}"
            if k not in old or k not in new:
                out.add(child)
            else:
                out |= changed_json_paths(old[k], new[k], child)
        return out
    if isinstance(old, list) and isinstance(new, list):
        out = set()
        for i in range(max(len(old), len(new))):
            child = f"{prefix}[{i}]"
            if i >= len(old) or i >= len(new):
                out.add(child)
            else:
                out |= changed_json_paths(old[i], new[i], child)
        return out
    return set() if old == new else {prefix}


def _path_overlaps(a: str, b: str) -> bool:
    """True when one JSON path is a prefix of the other (``$.x`` vs ``$.x.y``)."""
    if a == b:
        return True
    return b.startswith(a + ".") or b.startswith(a + "[") or a.startswith(
        b + "."
    ) or a.startswith(b + "[")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


__all__ = [
    "Artifact",
    "ArtifactDependency",
    "ArtifactStatus",
    "ArtifactStore",
    "changed_json_paths",
]
