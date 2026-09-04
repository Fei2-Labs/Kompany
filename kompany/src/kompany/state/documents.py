"""Generic versioned project documents — the "structured memory" store.

Domain-neutral. A document is addressed by ``(company_id, project_id,
namespace, key)`` and carries an integer ``version`` that only grows. Plugins
(branding, later others) pick their own ``namespace`` strings — e.g.
``branding.strategy`` — and store JSON ``content``. Core never interprets
the content.

Lifecycle (``DocumentStatus``)::

    draft -> proposed -> approved -> superseded | stale
      |         |
      +---------+--> rejected

Invariants:

* Approved rows are immutable: ``update_draft`` refuses anything that is not
  a draft, and there is no API to rewrite content after approval. A change
  is a NEW version linked through ``predecessor_version``.
* Approving version N marks the previously approved version of the same
  document ``superseded``; exactly one ``approved`` row exists per document.
* ``stale`` is a status flag only (an upstream dependency changed); the
  approved content stays as it was.
* Every transition is asserted; illegal ones raise
  :class:`IllegalDocumentTransition`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from kompany.core.run_context import current_run_id
from kompany.state.database import Database
from kompany.state.models import _short_id


class DocumentStatus(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STALE = "stale"


# Allowed transitions (from -> set(to)).
_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.DRAFT: frozenset(
        {DocumentStatus.PROPOSED, DocumentStatus.APPROVED, DocumentStatus.REJECTED}
    ),
    DocumentStatus.PROPOSED: frozenset(
        {DocumentStatus.APPROVED, DocumentStatus.REJECTED}
    ),
    DocumentStatus.APPROVED: frozenset(
        {DocumentStatus.SUPERSEDED, DocumentStatus.STALE}
    ),
    DocumentStatus.STALE: frozenset({DocumentStatus.SUPERSEDED}),
    DocumentStatus.REJECTED: frozenset(),
    DocumentStatus.SUPERSEDED: frozenset(),
}


class IllegalDocumentTransition(ValueError):
    """Raised on a lifecycle transition the state machine does not allow."""


class DocumentImmutable(ValueError):
    """Raised when content of a non-draft version would be rewritten."""


class ProjectDocument(BaseModel):
    id: str = Field(default_factory=_short_id)
    company_id: str | None = None
    project_id: str | None = None
    namespace: str
    key: str
    version: int = 1
    status: DocumentStatus = DocumentStatus.DRAFT
    content: dict[str, Any] = Field(default_factory=dict)
    checksum: str = ""
    created_by: str | None = None
    approval_id: str | None = None
    predecessor_version: int | None = None
    note: str | None = None
    run_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    rejection_reason: str | None = None


def content_checksum(content: dict[str, Any]) -> str:
    """Stable sha256 over canonical JSON — lets callers detect identical content."""
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ProjectDocumentStore:
    """SQLite-backed versioned document store (table ``project_documents``)."""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Create / mutate drafts
    # ------------------------------------------------------------------

    def draft(
        self,
        namespace: str,
        key: str,
        content: dict[str, Any],
        *,
        project_id: str | None = None,
        company_id: str | None = None,
        created_by: str | None = None,
        predecessor_version: int | None = None,
        note: str | None = None,
    ) -> ProjectDocument:
        """Create the next version of ``(namespace, key)`` as a draft.

        ``predecessor_version`` defaults to the currently approved version
        when one exists so the successor chain is always linked.
        """
        if not namespace or not key:
            raise ValueError("namespace and key are required")
        if not isinstance(content, dict):
            raise ValueError("content must be a JSON object (dict)")
        next_version = self._next_version(namespace, key, project_id, company_id)
        if predecessor_version is None:
            current = self.latest_approved(
                namespace, key, project_id=project_id, company_id=company_id
            )
            predecessor_version = current.version if current else None
        doc = ProjectDocument(
            company_id=company_id,
            project_id=project_id,
            namespace=namespace,
            key=key,
            version=next_version,
            content=content,
            checksum=content_checksum(content),
            created_by=created_by,
            predecessor_version=predecessor_version,
            note=note,
            run_id=current_run_id(),
        )
        self.db.execute(
            """INSERT INTO project_documents
               (id, company_id, project_id, namespace, key, version, status,
                content, checksum, created_by, approval_id,
                predecessor_version, note, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc.id,
                doc.company_id,
                doc.project_id,
                doc.namespace,
                doc.key,
                doc.version,
                doc.status.value,
                json.dumps(doc.content),
                doc.checksum,
                doc.created_by,
                doc.approval_id,
                doc.predecessor_version,
                doc.note,
                doc.run_id,
            ),
        )
        self.db.commit()
        return self.get(doc.id)  # type: ignore[return-value]

    def update_draft(self, document_id: str, content: dict[str, Any]) -> ProjectDocument:
        """Replace the content of a DRAFT. Any other status is immutable."""
        doc = self._require(document_id)
        if doc.status != DocumentStatus.DRAFT:
            raise DocumentImmutable(
                f"document {document_id} is {doc.status.value}; only drafts may change"
            )
        if not isinstance(content, dict):
            raise ValueError("content must be a JSON object (dict)")
        self.db.execute(
            """UPDATE project_documents
               SET content = ?, checksum = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (json.dumps(content), content_checksum(content), document_id),
        )
        self.db.commit()
        return self.get(document_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def propose(self, document_id: str, approval_id: str | None = None) -> ProjectDocument:
        doc = self._require(document_id)
        self._assert(doc.status, DocumentStatus.PROPOSED)
        self.db.execute(
            """UPDATE project_documents
               SET status = ?, approval_id = COALESCE(?, approval_id),
                   updated_at = datetime('now')
               WHERE id = ?""",
            (DocumentStatus.PROPOSED.value, approval_id, document_id),
        )
        self.db.commit()
        return self.get(document_id)  # type: ignore[return-value]

    def set_approval(self, document_id: str, approval_id: str) -> ProjectDocument:
        """Link a draft/proposed version to the approval card that gates it."""
        doc = self._require(document_id)
        if doc.status not in (DocumentStatus.DRAFT, DocumentStatus.PROPOSED):
            raise DocumentImmutable(
                f"document {document_id} is {doc.status.value}; approval link is frozen"
            )
        self.db.execute(
            """UPDATE project_documents
               SET approval_id = ?, updated_at = datetime('now') WHERE id = ?""",
            (approval_id, document_id),
        )
        self.db.commit()
        return self.get(document_id)  # type: ignore[return-value]

    def approve(
        self,
        document_id: str,
        *,
        approval_id: str | None = None,
        approved_by: str | None = None,
    ) -> ProjectDocument:
        """Freeze this version. The previous approved version becomes superseded."""
        doc = self._require(document_id)
        self._assert(doc.status, DocumentStatus.APPROVED)
        previous = self.latest_approved(
            doc.namespace,
            doc.key,
            project_id=doc.project_id,
            company_id=doc.company_id,
            include_stale=True,
        )
        with self.db.locked():
            if previous is not None and previous.id != document_id:
                self.db.execute(
                    """UPDATE project_documents
                       SET status = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (DocumentStatus.SUPERSEDED.value, previous.id),
                )
            self.db.execute(
                """UPDATE project_documents
                   SET status = ?, approval_id = COALESCE(?, approval_id),
                       approved_by = ?, approved_at = datetime('now'),
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (DocumentStatus.APPROVED.value, approval_id, approved_by, document_id),
            )
            self.db.commit()
        return self.get(document_id)  # type: ignore[return-value]

    def reject(self, document_id: str, reason: str | None = None) -> ProjectDocument:
        doc = self._require(document_id)
        self._assert(doc.status, DocumentStatus.REJECTED)
        self.db.execute(
            """UPDATE project_documents
               SET status = ?, rejection_reason = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (DocumentStatus.REJECTED.value, reason, document_id),
        )
        self.db.commit()
        return self.get(document_id)  # type: ignore[return-value]

    def mark_stale(self, document_id: str, note: str | None = None) -> ProjectDocument:
        """Flag an approved version as stale (upstream changed). Content untouched."""
        doc = self._require(document_id)
        self._assert(doc.status, DocumentStatus.STALE)
        self.db.execute(
            """UPDATE project_documents
               SET status = ?, note = COALESCE(?, note), updated_at = datetime('now')
               WHERE id = ?""",
            (DocumentStatus.STALE.value, note, document_id),
        )
        self.db.commit()
        return self.get(document_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, document_id: str) -> ProjectDocument | None:
        row = self.db.execute(
            "SELECT * FROM project_documents WHERE id = ?", (document_id,)
        ).fetchone()
        return self._row(row) if row else None

    def get_version(
        self,
        namespace: str,
        key: str,
        version: int,
        *,
        project_id: str | None = None,
        company_id: str | None = None,
    ) -> ProjectDocument | None:
        row = self.db.execute(
            f"""SELECT * FROM project_documents
                WHERE namespace = ? AND key = ? AND version = ? AND {_SCOPE}""",
            (namespace, key, version, project_id or "", company_id or ""),
        ).fetchone()
        return self._row(row) if row else None

    def latest(
        self,
        namespace: str,
        key: str,
        *,
        project_id: str | None = None,
        company_id: str | None = None,
        status: DocumentStatus | str | None = None,
    ) -> ProjectDocument | None:
        """Highest version, optionally restricted to one status."""
        params: list[Any] = [namespace, key, project_id or "", company_id or ""]
        sql = f"SELECT * FROM project_documents WHERE namespace = ? AND key = ? AND {_SCOPE}"
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value if isinstance(status, DocumentStatus) else status)
        sql += " ORDER BY version DESC LIMIT 1"
        row = self.db.execute(sql, tuple(params)).fetchone()
        return self._row(row) if row else None

    def latest_approved(
        self,
        namespace: str,
        key: str,
        *,
        project_id: str | None = None,
        company_id: str | None = None,
        include_stale: bool = False,
    ) -> ProjectDocument | None:
        """The single approved version (optionally accepting a stale one)."""
        statuses = [DocumentStatus.APPROVED.value]
        if include_stale:
            statuses.append(DocumentStatus.STALE.value)
        placeholders = ",".join("?" for _ in statuses)
        row = self.db.execute(
            f"""SELECT * FROM project_documents
                WHERE namespace = ? AND key = ? AND {_SCOPE}
                  AND status IN ({placeholders})
                ORDER BY version DESC LIMIT 1""",
            (namespace, key, project_id or "", company_id or "", *statuses),
        ).fetchone()
        return self._row(row) if row else None

    def list_versions(
        self,
        namespace: str,
        key: str,
        *,
        project_id: str | None = None,
        company_id: str | None = None,
    ) -> list[ProjectDocument]:
        rows = self.db.execute(
            f"""SELECT * FROM project_documents
                WHERE namespace = ? AND key = ? AND {_SCOPE}
                ORDER BY version ASC""",
            (namespace, key, project_id or "", company_id or ""),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_namespace(
        self,
        namespace: str,
        *,
        project_id: str | None = None,
        company_id: str | None = None,
        status: DocumentStatus | str | None = None,
    ) -> list[ProjectDocument]:
        """All versions in a namespace (or a namespace prefix ending in ``.``)."""
        params: list[Any] = [project_id or "", company_id or ""]
        if namespace.endswith("."):
            where = "namespace LIKE ?"
            params.append(namespace + "%")
        else:
            where = "namespace = ?"
            params.append(namespace)
        sql = f"SELECT * FROM project_documents WHERE {_SCOPE} AND {where}"
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value if isinstance(status, DocumentStatus) else status)
        sql += " ORDER BY namespace, key, version"
        rows = self.db.execute(sql, tuple(params)).fetchall()
        return [self._row(r) for r in rows]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _next_version(
        self,
        namespace: str,
        key: str,
        project_id: str | None,
        company_id: str | None,
    ) -> int:
        row = self.db.execute(
            f"""SELECT MAX(version) AS v FROM project_documents
                WHERE namespace = ? AND key = ? AND {_SCOPE}""",
            (namespace, key, project_id or "", company_id or ""),
        ).fetchone()
        return int(row["v"] or 0) + 1

    def _require(self, document_id: str) -> ProjectDocument:
        doc = self.get(document_id)
        if doc is None:
            raise LookupError(f"document not found: {document_id}")
        return doc

    @staticmethod
    def _assert(current: DocumentStatus, target: DocumentStatus) -> None:
        if target not in _TRANSITIONS.get(current, frozenset()):
            raise IllegalDocumentTransition(
                f"cannot move document from {current.value} to {target.value}"
            )

    @staticmethod
    def _row(row: Any) -> ProjectDocument:
        return ProjectDocument(
            id=row["id"],
            company_id=row["company_id"],
            project_id=row["project_id"],
            namespace=row["namespace"],
            key=row["key"],
            version=int(row["version"]),
            status=DocumentStatus(row["status"]),
            content=json.loads(row["content"] or "{}"),
            checksum=row["checksum"] or "",
            created_by=row["created_by"],
            approval_id=row["approval_id"],
            predecessor_version=row["predecessor_version"],
            note=row["note"],
            run_id=row["run_id"],
            created_at=_parse_dt(row["created_at"]) or datetime.now(UTC),
            updated_at=_parse_dt(row["updated_at"]),
            approved_at=_parse_dt(row["approved_at"]),
            approved_by=row["approved_by"],
            rejection_reason=row["rejection_reason"],
        )


# Scope predicate shared by every query: NULL project/company collapse to "".
_SCOPE = "IFNULL(project_id, '') = ? AND IFNULL(company_id, '') = ?"


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
    "DocumentImmutable",
    "DocumentStatus",
    "IllegalDocumentTransition",
    "ProjectDocument",
    "ProjectDocumentStore",
    "content_checksum",
]
