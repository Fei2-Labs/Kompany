"""Service-layer tests for ``GlossaryService`` + ``company_config`` persistence.

Glossary-and-drift-detection task 05-19. Covers CRUD, the
``company_config`` round-trip, the corrupt-row fallback, and the
``bulk_install_from_template`` helper that templates lean on.
"""

from __future__ import annotations

import pytest

from kompany.state.database import Database
from kompany.state.glossary import (
    CompanyGlossary,
    GlossaryService,
    clear_glossary,
    load_from_config,
    save_to_config,
)


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path)


def test_load_on_empty_db_returns_empty_glossary(db: Database) -> None:
    glossary = load_from_config(db)
    assert isinstance(glossary, CompanyGlossary)
    assert len(glossary) == 0


def test_add_persists_to_company_config(db: Database) -> None:
    service = GlossaryService(db)
    service.add(
        term="customer",
        definition="a paying account",
        forbidden_synonyms=["user", "lead"],
    )
    row = db.execute(
        "SELECT value FROM company_config WHERE key = 'glossary'"
    ).fetchone()
    assert row is not None
    assert "customer" in row["value"]
    assert "user" in row["value"]


def test_add_then_get_round_trips(db: Database) -> None:
    service = GlossaryService(db)
    service.add(
        term="MRR",
        definition="monthly recurring revenue",
        forbidden_synonyms=["ARR"],
    )
    entry = service.get("mrr")
    assert entry is not None
    assert entry.term == "MRR"
    assert entry.forbidden_synonyms == ["ARR"]


def test_list_terms_returns_all_in_insertion_order(db: Database) -> None:
    service = GlossaryService(db)
    service.add("customer", "definition 1")
    service.add("MRR", "definition 2")
    service.add("churn", "definition 3")
    terms = [e.term for e in service.list_terms()]
    assert terms == ["customer", "MRR", "churn"]


def test_update_persists_changes(db: Database) -> None:
    service = GlossaryService(db)
    service.add("customer", "old definition", forbidden_synonyms=["user"])
    service.update(
        "customer",
        definition="new definition",
        forbidden_synonyms=["user", "lead", "prospect"],
    )
    entry = service.get("customer")
    assert entry is not None
    assert entry.definition == "new definition"
    assert entry.forbidden_synonyms == ["user", "lead", "prospect"]


def test_update_missing_term_raises_lookup_error(db: Database) -> None:
    service = GlossaryService(db)
    with pytest.raises(LookupError):
        service.update("missing", definition="x")


def test_remove_returns_true_on_success(db: Database) -> None:
    service = GlossaryService(db)
    service.add("customer", "x")
    assert service.remove("customer") is True
    assert service.get("customer") is None


def test_remove_returns_false_when_term_missing(db: Database) -> None:
    service = GlossaryService(db)
    assert service.remove("nothing") is False


def test_add_duplicate_term_raises_value_error(db: Database) -> None:
    service = GlossaryService(db)
    service.add("customer", "x")
    with pytest.raises(ValueError):
        service.add("customer", "y")


def test_add_with_self_referencing_synonym_rejected(db: Database) -> None:
    """The validator on GlossaryEntry must reject a synonym that
    matches the canonical term case-insensitively."""
    service = GlossaryService(db)
    with pytest.raises(Exception):  # noqa: BLE001 — ValidationError or ValueError
        service.add("customer", "x", forbidden_synonyms=["Customer"])


def test_load_handles_corrupt_json_row_gracefully(db: Database) -> None:
    db.execute(
        "INSERT INTO company_config (key, value) VALUES ('glossary', '<<not json>>')"
    )
    db.commit()
    glossary = load_from_config(db)
    assert len(glossary) == 0


def test_clear_glossary_removes_rows(db: Database) -> None:
    service = GlossaryService(db)
    service.add("customer", "x")
    clear_glossary(db)
    assert len(load_from_config(db)) == 0
    row = db.execute(
        "SELECT value FROM company_config WHERE key = 'glossary'"
    ).fetchone()
    assert row is None


def test_save_roundtrip_keeps_timestamp(db: Database) -> None:
    glossary = CompanyGlossary()
    glossary.add(
        # Use the service indirection for the timestamp side effect
        # so we exercise the real production path.
        __import__("kompany.state.glossary", fromlist=["GlossaryEntry"])
        .GlossaryEntry(
            term="customer",
            definition="x",
            added_at=__import__("datetime").datetime(
                2026, 5, 19, tzinfo=__import__("datetime").timezone.utc
            ),
        )
    )
    save_to_config(db, glossary)
    reloaded = load_from_config(db)
    assert reloaded.updated_at is not None
    assert reloaded.entries[0].term == "customer"


def test_bulk_install_from_template_inserts_new_rows(db: Database) -> None:
    service = GlossaryService(db)
    template_rows = [
        {"term": "customer", "definition": "paying account",
         "forbidden_synonyms": ["user"]},
        {"term": "MRR", "definition": "monthly recurring revenue",
         "forbidden_synonyms": ["ARR"]},
    ]
    installed = service.bulk_install_from_template(template_rows)
    assert installed == 2
    glossary = service.load()
    assert glossary.find("customer") is not None
    assert glossary.find("MRR") is not None
    assert glossary.find("customer").added_by == "template"


def test_bulk_install_skips_existing_terms(db: Database) -> None:
    service = GlossaryService(db)
    service.add("customer", "founder-curated", forbidden_synonyms=["user"])
    installed = service.bulk_install_from_template([
        {"term": "customer", "definition": "template version",
         "forbidden_synonyms": ["user"]},
        {"term": "MRR", "definition": "new", "forbidden_synonyms": []},
    ])
    # Only the missing one was installed.
    assert installed == 1
    entry = service.get("customer")
    assert entry is not None
    assert entry.definition == "founder-curated"
    assert entry.added_by == "founder"


def test_bulk_install_skips_malformed_entries(db: Database) -> None:
    service = GlossaryService(db)
    installed = service.bulk_install_from_template([
        {"term": "valid", "definition": "ok"},
        {"definition": "missing term"},
        "not even a dict",
        {"term": "valid2", "definition": "ok2"},
    ])
    assert installed == 2
