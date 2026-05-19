"""Company glossary — founder-defined canonical terms + forbidden synonyms.

Mirrors the AI-session [[glossary-canonical-terms]] mechanism at the product
layer. The founder defines which words the company uses (``customer`` vs
``user``, ``MRR`` vs ``yearly``) and the CoS retrospective scans every
finished episode for drift, surfacing violations through the standard
approval-thread inbox.

Storage is the existing ``company_config`` key-value table — no schema
migration is needed. The full glossary serialises as one
``company_config['glossary']`` row holding a JSON list of entries, plus
a single ``company_config['glossary_updated_at']`` timestamp.

Model uses ``extra="forbid"`` so manifest typos or REST callers sending
unknown keys fail loudly. Two structural invariants are enforced in
``GlossaryEntry``:

1. ``term`` must not appear in its own ``forbidden_synonyms`` (a term can
   never forbid itself; that would silence every legitimate use of the
   canonical word during drift scans).
2. ``term`` is normalised to its trimmed form so ``"customer "`` and
   ``"customer"`` collide on roundtrip.

``CompanyGlossary`` is a thin Pydantic container the service layer uses
to hold the active list + the last-updated timestamp. The container's
helpers (``find`` / ``add`` / ``update`` / ``remove`` / ``add_or_update``)
preserve insertion order while keeping term lookups case-insensitive.

See ``.trellis/tasks/05-19-glossary-and-drift-detection/prd.md`` for the
design and the user-visible drift workflow.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kompany.state.database import Database


# ``company_config`` keys we own. Kept module-level so callers (engine,
# tests, future migrations) don't re-discover the naming scheme.
_KEY_GLOSSARY = "glossary"
_KEY_GLOSSARY_UPDATED_AT = "glossary_updated_at"


# How many entries the prompt-injection helper renders at most. Older
# entries are dropped from the summary so the prompt stays terse; the
# full list is still inspectable via ``kompany glossary list``.
MAX_PROMPT_ENTRIES = 20


GlossarySource = Literal["founder", "cos_proposal", "template"]


class GlossaryEntry(BaseModel):
    """One canonical term + its forbidden synonyms.

    Example::

        GlossaryEntry(
            term="customer",
            definition="a paying user (NOT trial, NOT lead).",
            forbidden_synonyms=["user", "lead", "prospect"],
            added_at=datetime.now(timezone.utc),
            added_by="founder",
        )
    """

    model_config = ConfigDict(extra="forbid")

    term: str = Field(..., min_length=1)
    definition: str = Field(..., min_length=1)
    forbidden_synonyms: list[str] = Field(default_factory=list)
    added_at: datetime
    added_by: GlossarySource = "founder"
    source_episode_id: str | None = None

    @field_validator("term")
    @classmethod
    def _strip_term(cls, value: str) -> str:
        out = value.strip()
        if not out:
            raise ValueError("term must not be blank after stripping whitespace")
        return out

    @field_validator("forbidden_synonyms")
    @classmethod
    def _normalize_synonyms(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError(
                    f"forbidden_synonyms entries must be strings, got {type(raw).__name__}"
                )
            stripped = raw.strip()
            if not stripped:
                continue
            key = stripped.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(stripped)
        return out

    @model_validator(mode="after")
    def _forbid_self_reference(self) -> "GlossaryEntry":
        """Reject ``term`` appearing in its own forbidden list.

        Self-reference would silence every legitimate use of the canonical
        word during drift scans. We compare via casefold so ``"Customer"``
        in the synonym list still trips when ``term == "customer"``.
        """
        term_key = self.term.casefold()
        for syn in self.forbidden_synonyms:
            if syn.casefold() == term_key:
                raise ValueError(
                    f"forbidden_synonyms cannot include the canonical term "
                    f"{self.term!r} (self-reference)"
                )
        return self


class CompanyGlossary(BaseModel):
    """Container holding the active term list + last-updated timestamp.

    The service layer (``load_from_config`` / ``save_to_config``) is the
    seam between this in-memory model and the JSON row in
    ``company_config``. Helpers mutate in place; callers must call
    ``save_to_config`` to persist.
    """

    model_config = ConfigDict(extra="forbid")

    entries: list[GlossaryEntry] = Field(default_factory=list)
    updated_at: datetime | None = None

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def find(self, term: str) -> GlossaryEntry | None:
        """Return the entry whose ``term`` matches case-insensitively."""
        key = term.strip().casefold()
        for entry in self.entries:
            if entry.term.casefold() == key:
                return entry
        return None

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterable[GlossaryEntry]:  # type: ignore[override]
        return iter(self.entries)

    # ------------------------------------------------------------------
    # Mutation helpers (in-memory; persistence is the caller's job)
    # ------------------------------------------------------------------

    def add(self, entry: GlossaryEntry) -> GlossaryEntry:
        """Insert a brand-new entry. Raises if the term already exists."""
        if self.find(entry.term) is not None:
            raise ValueError(
                f"glossary already has a term matching {entry.term!r}; "
                "use update() to replace it"
            )
        self.entries.append(entry)
        self.updated_at = datetime.now(timezone.utc)
        return entry

    def update(
        self,
        term: str,
        *,
        definition: str | None = None,
        forbidden_synonyms: list[str] | None = None,
    ) -> GlossaryEntry:
        """Mutate an existing entry's definition/synonyms in place."""
        existing = self.find(term)
        if existing is None:
            raise LookupError(f"glossary has no term matching {term!r}")
        # Re-build via model_copy so the model validators (self-reference
        # check, synonym dedupe) run on the new payload.
        merged = existing.model_copy(
            update={
                "definition": (
                    definition if definition is not None else existing.definition
                ),
                "forbidden_synonyms": (
                    forbidden_synonyms
                    if forbidden_synonyms is not None
                    else list(existing.forbidden_synonyms)
                ),
            }
        )
        # Re-validate by round-tripping the dump back through the model.
        replacement = GlossaryEntry.model_validate(merged.model_dump(mode="python"))
        idx = self.entries.index(existing)
        self.entries[idx] = replacement
        self.updated_at = datetime.now(timezone.utc)
        return replacement

    def add_or_update(self, entry: GlossaryEntry) -> GlossaryEntry:
        """Convenience: insert when missing, otherwise overwrite in place."""
        if self.find(entry.term) is None:
            return self.add(entry)
        return self.update(
            entry.term,
            definition=entry.definition,
            forbidden_synonyms=entry.forbidden_synonyms,
        )

    def remove(self, term: str) -> bool:
        """Drop the entry whose ``term`` matches. Returns True if removed."""
        existing = self.find(term)
        if existing is None:
            return False
        self.entries.remove(existing)
        self.updated_at = datetime.now(timezone.utc)
        return True

    # ------------------------------------------------------------------
    # Prompt-injection rendering
    # ------------------------------------------------------------------

    def compose_summary(self, max_entries: int = MAX_PROMPT_ENTRIES) -> str:
        """Render a terse multi-line block for agent system prompts.

        Returns ``""`` when the glossary is empty so callers can splice
        the value in without an explicit ``if`` guard. The newest entries
        (by ``added_at``) win when the list overflows ``max_entries`` —
        recently-curated terms are the highest-signal ones for live drift.
        """
        if not self.entries:
            return ""
        ordered = sorted(
            self.entries,
            key=lambda e: e.added_at,
            reverse=True,
        )
        picked = ordered[: max(1, int(max_entries))]
        lines = ["COMPANY GLOSSARY (use canonical terms, avoid forbidden synonyms):"]
        for entry in picked:
            forbid_part = (
                f" (NOT {', '.join(entry.forbidden_synonyms)})"
                if entry.forbidden_synonyms
                else ""
            )
            # Keep one entry per line so the LLM can scan the list quickly.
            lines.append(
                f"- {entry.term}: {entry.definition.strip()}{forbid_part}"
            )
        if len(self.entries) > len(picked):
            lines.append(
                f"... (+{len(self.entries) - len(picked)} more — "
                f"run `kompany glossary list` to see them all)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _read_config(db: Database, key: str) -> str | None:
    row = db.execute(
        "SELECT value FROM company_config WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def _write_config(db: Database, key: str, value: str) -> None:
    db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value,
             updated_at = datetime('now')""",
        (key, value),
    )


def _delete_config(db: Database, key: str) -> None:
    db.execute("DELETE FROM company_config WHERE key = ?", (key,))


def load_from_config(db: Database) -> CompanyGlossary:
    """Build an in-memory :class:`CompanyGlossary` from ``company_config``.

    Returns an empty glossary when no row exists; corrupt JSON is also
    treated as empty (with no exception) so a broken row never wedges
    agent prompts or the CoS retrospective.
    """
    raw = _read_config(db, _KEY_GLOSSARY)
    if not raw:
        return CompanyGlossary()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return CompanyGlossary()
    if not isinstance(payload, list):
        return CompanyGlossary()
    entries: list[GlossaryEntry] = []
    for item in payload:
        try:
            entries.append(GlossaryEntry.model_validate(item))
        except Exception:  # noqa: BLE001 — skip individual corrupt rows
            continue
    updated_raw = _read_config(db, _KEY_GLOSSARY_UPDATED_AT)
    updated_at: datetime | None = None
    if updated_raw:
        try:
            updated_at = datetime.fromisoformat(updated_raw)
        except (TypeError, ValueError):
            updated_at = None
    return CompanyGlossary(entries=entries, updated_at=updated_at)


def save_to_config(db: Database, glossary: CompanyGlossary) -> None:
    """Persist a :class:`CompanyGlossary` back to ``company_config``.

    Writes both the JSON list row and a sibling timestamp row so the UI
    can surface ``Updated`` without re-parsing the JSON each render.
    """
    payload = [entry.model_dump(mode="json") for entry in glossary.entries]
    _write_config(db, _KEY_GLOSSARY, json.dumps(payload))
    stamp = (glossary.updated_at or datetime.now(timezone.utc)).isoformat()
    _write_config(db, _KEY_GLOSSARY_UPDATED_AT, stamp)
    db.commit()


def clear_glossary(db: Database) -> None:
    """Delete every glossary row. Used by tests; not surfaced via UI."""
    _delete_config(db, _KEY_GLOSSARY)
    _delete_config(db, _KEY_GLOSSARY_UPDATED_AT)
    db.commit()


# ---------------------------------------------------------------------------
# Service: high-level CRUD for callers (CLI/SDK/REST/MCP)
# ---------------------------------------------------------------------------


class GlossaryService:
    """Stateless façade over :func:`load_from_config` / :func:`save_to_config`.

    Each method re-reads the persisted glossary so concurrent writers
    don't trample each other — at the cost of one cheap company_config
    round-trip per call. The drift scanner takes its own snapshot via
    :meth:`load` so a mid-scan edit doesn't shift the term set.
    """

    def __init__(self, db: Database):
        self.db = db

    def load(self) -> CompanyGlossary:
        return load_from_config(self.db)

    def list_terms(self) -> list[GlossaryEntry]:
        return list(self.load().entries)

    def get(self, term: str) -> GlossaryEntry | None:
        return self.load().find(term)

    def add(
        self,
        term: str,
        definition: str,
        forbidden_synonyms: list[str] | None = None,
        added_by: GlossarySource = "founder",
        source_episode_id: str | None = None,
    ) -> GlossaryEntry:
        glossary = self.load()
        entry = GlossaryEntry(
            term=term,
            definition=definition,
            forbidden_synonyms=list(forbidden_synonyms or []),
            added_at=datetime.now(timezone.utc),
            added_by=added_by,
            source_episode_id=source_episode_id,
        )
        glossary.add(entry)
        save_to_config(self.db, glossary)
        return entry

    def update(
        self,
        term: str,
        *,
        definition: str | None = None,
        forbidden_synonyms: list[str] | None = None,
    ) -> GlossaryEntry:
        glossary = self.load()
        entry = glossary.update(
            term,
            definition=definition,
            forbidden_synonyms=forbidden_synonyms,
        )
        save_to_config(self.db, glossary)
        return entry

    def remove(self, term: str) -> bool:
        glossary = self.load()
        removed = glossary.remove(term)
        if removed:
            save_to_config(self.db, glossary)
        return removed

    def add_or_update(self, entry: GlossaryEntry) -> GlossaryEntry:
        glossary = self.load()
        result = glossary.add_or_update(entry)
        save_to_config(self.db, glossary)
        return result

    def bulk_install_from_template(
        self,
        template_entries: list[dict[str, Any]],
    ) -> int:
        """Apply a template manifest's ``glossary`` list to the current company.

        Used by ``Templates.apply``. Each entry is added with
        ``added_by="template"`` so the audit trail shows where the term
        came from. Existing terms are *not* clobbered — the founder may
        have curated them by hand between template applications.
        Returns the number of new rows actually inserted.
        """
        glossary = self.load()
        installed = 0
        for raw in template_entries:
            if not isinstance(raw, dict):
                continue
            term = raw.get("term")
            if not isinstance(term, str):
                continue
            if glossary.find(term) is not None:
                continue
            try:
                entry = GlossaryEntry(
                    term=term,
                    definition=raw.get("definition", ""),
                    forbidden_synonyms=list(raw.get("forbidden_synonyms") or []),
                    added_at=datetime.now(timezone.utc),
                    added_by="template",
                )
            except Exception:  # noqa: BLE001 — skip individual bad entries
                continue
            glossary.add(entry)
            installed += 1
        if installed:
            save_to_config(self.db, glossary)
        return installed


__all__ = [
    "CompanyGlossary",
    "GlossaryEntry",
    "GlossaryService",
    "GlossarySource",
    "MAX_PROMPT_ENTRIES",
    "clear_glossary",
    "load_from_config",
    "save_to_config",
]
